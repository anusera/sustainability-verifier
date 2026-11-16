import json
from datetime import date

import pytest

from backend import pipeline
from backend.extract import validate_quotes
from backend.models import Fact
from backend.rules import (VAGUE_TERMS, recycled_content_match, run_rules, tier_coverage, vague_claim)
from backend.verdict import decide_verdict

from .conftest import DATA, NOW, TODAY

CHAIN = [{"tier": 1, "supplier": "Bottle Co"}, {"tier": 2, "supplier": "Resin Ltd"}, {"tier": 3, "supplier": "Flake Works"}]
CLAIM = {"claim_id": "c1", "type": "packaging", "text": "Bottle made from 100% recycled plastic",
         "claimed_recycled_pct": 100}


def fact(fid, tier=1, pct=None, **kw):
    return Fact(fact_id=fid, doc_id=f"d{tier}", supplier=f"S{tier}", tier=tier, source_file=f"d{tier}.txt",
                source_quote="q", recycled_content_pct=pct, **kw)


def cert(tier=2):
    return fact(f"cert{tier}", tier, certificate_id="GRS-1", valid_to="2027-12-31")


def full_chain(*pcts):
    """One fact per tier (so tier_coverage passes); first fact per tier carries the given pct."""
    return [fact(f"f{i}", i, p) for i, p in zip((1, 2, 3), pcts)]


# ---- recycled_content_match
def test_recycled_fail_lower_fact_first():
    c = recycled_content_match(100, [fact("hi", 1, 100), fact("lo", 3, 60)])
    assert c.result == "fail" and c.evidence == ["lo", "hi"]
    assert "60%" in c.detail and "S3" in c.detail


def test_recycled_equal_and_higher_pass():
    assert recycled_content_match(100, [fact("a", 1, 100)]).result == "pass"
    assert recycled_content_match(60, [fact("a", 1, 100)]).result == "pass"


def test_recycled_lower_facts_sorted_ascending():
    c = recycled_content_match(100, [fact("a", 1, 80), fact("b", 2, 50)])
    assert c.evidence == ["b", "a"]


def test_recycled_ignores_facts_without_pct():
    c = recycled_content_match(100, [fact("a", 1, None), fact("b", 2, 100)])
    assert c.result == "pass" and c.evidence == ["b"]


def test_recycled_none_stated_passes_with_exact_detail():
    c = recycled_content_match(100, [fact("a", 1, None)])
    assert c.result == "pass" and c.detail == "No fact states recycled content" and c.evidence == []


@pytest.mark.parametrize("bad", [-1, 100.5, 150])
def test_recycled_rejects_out_of_range_fact(bad):
    with pytest.raises(ValueError, match="outside 0 to 100"):
        recycled_content_match(100, [fact("a", 1, bad)])


def test_recycled_rejects_out_of_range_claim():
    with pytest.raises(ValueError):
        recycled_content_match(101, [])


def test_recycled_bounds_are_valid():
    assert recycled_content_match(0, [fact("a", 1, 0)]).result == "pass"
    assert recycled_content_match(100, [fact("a", 1, 100)]).result == "pass"


def test_extraction_rejects_out_of_range_pct():
    good, bad = fact("g", 1, 60), fact("b", 1, 160)
    accepted, rejected = validate_quotes([good, bad], "q")
    assert [f.fact_id for f in accepted] == ["g"]
    assert rejected[0].fact.fact_id == "b" and "outside 0 to 100" in rejected[0].reason


# ---- tier_coverage
def test_tier_coverage_pass():
    assert tier_coverage(CHAIN, full_chain(1, 1, 1)).result == "pass"


def test_tier_coverage_names_empty_tiers():
    c = tier_coverage(CHAIN, [fact("a", 1)])
    assert c.result == "fail"
    assert "Tier 2 (Resin Ltd): no facts" in c.detail and "Tier 3 (Flake Works): no facts" in c.detail


def test_tier_coverage_empty_chain_passes():
    assert tier_coverage([], []).result == "pass"


# ---- vague_claim
@pytest.mark.parametrize("text", [
    "eco-friendly bottle", "Eco friendly bottle", "Environmentally friendly packaging",
    "Sustainable bottle", "100% natural", "up to 100% recycled", "UP TO 50% recycled"])
def test_vague_terms_fail(text):
    c = vague_claim(text)
    assert c.result == "fail" and c.evidence == []


@pytest.mark.parametrize("text", [
    "100% recycled bottle", "Green glass bottle", "sustainably made", "unsustainable", "naturally", "greenhouse"])
def test_not_vague(text):
    assert vague_claim(text).result == "pass"


def test_no_bare_green_in_terms():
    assert not any(t.strip().lower() == "green" for t in VAGUE_TERMS)


def test_vague_terms_injectable():
    assert vague_claim("zero waste", terms=["zero waste"]).result == "fail"


# ---- run_rules
def test_packaging_ruleset_order():
    ids = [c.check_id for c in run_rules(CLAIM, full_chain(100, 100, 100), TODAY, CHAIN)]
    assert ids == ["recycled_content_match", "cert_validity", "tier_coverage", "vague_claim"]


def test_origin_and_typeless_keep_two_checks():
    for claim in ({"claimed_origin": "India"}, {"type": "origin", "claimed_origin": "India"}):
        assert [c.check_id for c in run_rules(claim, [], TODAY, CHAIN)] == ["origin_match", "cert_validity"]


def test_unknown_type_raises():
    with pytest.raises(ValueError, match="Unknown claim type"):
        run_rules({"type": "carbon"}, [], TODAY)


def test_packaging_needs_claimed_pct():
    with pytest.raises(ValueError, match="claimed_recycled_pct"):
        run_rules({"claim_id": "c9", "type": "packaging", "text": "x"}, [], TODAY, CHAIN)


# ---- verdicts
def verdict(facts, claim=CLAIM, chain=CHAIN):
    checks = run_rules(claim, facts, TODAY, chain)
    return decide_verdict("bottle", "c1", checks, facts, chain, NOW, claim_type=claim["type"]), checks


def test_60_vs_100_is_contradicted():
    v, _ = verdict(full_chain(100, 100, 60) + [cert()])
    assert v.verdict == "Contradicted" and "60%" in v.reason


def test_verified():
    v, _ = verdict(full_chain(100, 100, 100) + [cert()])
    assert v.verdict == "Verified"


def test_vague_claim_is_self_declared_not_contradicted():
    claim = {**CLAIM, "text": "Eco-friendly bottle"}
    v, _ = verdict(full_chain(100, 100, 100) + [cert()], claim)
    assert v.verdict == "Self-declared only"
    assert "unqualified" in v.reason and "Not yet proven" in v.reason
    assert "Third-party backing is not valid" not in v.reason


def test_100_percent_recycled_bottle_not_vague_verified():
    claim = {**CLAIM, "text": "100% recycled bottle"}
    v, _ = verdict(full_chain(100, 100, 100) + [cert()], claim)
    assert v.verdict == "Verified"


def test_no_recycled_facts_is_missing_evidence():
    v, checks = verdict(full_chain(None, None, None) + [cert()])
    assert next(c for c in checks if c.check_id == "recycled_content_match").result == "pass"
    assert v.verdict == "Missing evidence" and "no fact states recycled content" in v.reason


def test_empty_tier_is_missing_evidence_via_check():
    v, checks = verdict([fact("a", 1, 100), fact("b", 2, 100), cert()])
    assert v.verdict == "Missing evidence"
    assert "Tier 3 (Flake Works): no facts" in v.reason
    assert "tier_coverage: fail" in v.checks


def test_contradicted_and_missing_tier_lists_both():
    v, _ = verdict([fact("a", 1, 100), fact("b", 2, 60), cert()])
    assert v.verdict == "Contradicted"
    assert "60%" in v.reason and "Tier 3 (Flake Works): no facts" in v.reason


def test_contradicted_and_missing_tier_lists_both_origin_fallback():
    claim = {"type": "origin", "claimed_origin": "India"}
    facts = [fact("a", 1, origin_country="India"), fact("b", 2, origin_country="Bangladesh")]
    v, _ = verdict(facts, claim)
    assert v.verdict == "Contradicted"
    assert "Bangladesh" in v.reason and "no facts found for tier 3" in v.reason


def test_origin_fallback_still_gives_missing_evidence():
    claim = {"type": "origin", "claimed_origin": "India"}
    v, checks = verdict([fact("a", 1, origin_country="India"), cert()], claim)
    assert [c.check_id for c in checks] == ["origin_match", "cert_validity"]
    assert v.verdict == "Missing evidence" and v.reason == "Missing evidence: no facts found for tier 3."


# ---- pipeline wiring and data shape
def test_run_product_passes_claim_type_to_decide_verdict(tmp_path, monkeypatch):
    product = {"product_id": "bottle-x", "name": "Bottle", "claims": [CLAIM], "documents": [],
               "supply_chain": CHAIN[:1]}
    pp = tmp_path / "products.json"
    pp.write_text(json.dumps({"bottle-x": product}), encoding="utf-8")
    seen = []
    real = pipeline.decide_verdict
    monkeypatch.setattr(pipeline, "decide_verdict", lambda *a, **k: (seen.append(k), real(*a, **k))[1])
    result = pipeline.run_product("bottle-x", log_path=tmp_path / "log.jsonl", docs_dir=tmp_path,
                                  mock_dir=tmp_path, products_path=pp, runs_dir=tmp_path / "runs",
                                  today=TODAY, now=NOW)
    assert seen and seen[0]["claim_type"] == "packaging"
    assert [c.check_id for c in result.claims[0].checks][0] == "recycled_content_match"


def test_every_claim_in_products_json_has_a_type():
    from backend.rules import RULESETS
    products = json.loads((DATA / "products.json").read_text(encoding="utf-8"))
    for pid, p in products.items():
        for c in p["claims"]:
            assert c.get("type") in RULESETS, f"{pid}/{c['claim_id']} lacks a valid type"
