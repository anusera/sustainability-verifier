import json

from backend.rules import origin_match, run_rules
from backend.verdict import decide_verdict

from .conftest import DATA, by_id

CLAIM = {"claimed_origin": "India"}


def verdict_for(facts, product, today, now):
    checks = run_rules(CLAIM, facts, today)
    return decide_verdict("tshirt-001", "c1", checks, facts, product["supply_chain"], now)


def good_facts(facts):
    """India everywhere, no Bangladesh fact, certificate valid until 2027."""
    out = [f for f in facts if f.fact_id != "f-t3-1"]
    out.append(by_id(facts, "f-t3-1").model_copy(update={"origin_country": "India"}))
    return [f.model_copy(update={"valid_to": "2027-12-31"}) if f.fact_id == "f-t2-1" else f for f in out]


def test_contradicted(facts, product, today, now):
    v = verdict_for(facts, product, today, now)
    assert v.verdict == "Contradicted"
    assert v.checks == ["origin_match: fail", "cert_validity: fail"]
    assert "f-t3-1" in v.evidence and "f-t2-1" in v.evidence


def test_contradicted_beats_missing(facts, product, today, now):
    f = [x for x in facts if x.tier != 1]  # tier 1 empty, origin still fails via tier 3
    v = verdict_for(f, product, today, now)
    assert v.verdict == "Contradicted"


def test_missing_evidence(facts, product, today, now):
    f = [x for x in good_facts(facts) if x.tier != 3]
    assert verdict_for(f, product, today, now).verdict == "Missing evidence"


def test_self_declared_no_certificate(facts, product, today, now):
    f = [x for x in good_facts(facts) if x.fact_id != "f-t2-1"]
    f.append(by_id(facts, "f-t2-1").model_copy(update={
        "certificate_id": None, "valid_to": None, "valid_from": None, "certification_body": None}))
    assert verdict_for(f, product, today, now).verdict == "Self-declared only"


def test_self_declared_expired_certificate_only(facts, product, today, now):
    f = [x.model_copy(update={"valid_to": "2026-03-31"}) if x.fact_id == "f-t2-1" else x
         for x in good_facts(facts)]
    v = verdict_for(f, product, today, now)
    assert v.verdict == "Self-declared only"
    assert "cert_validity: fail" in v.checks


def test_verified(facts, product, today, now):
    v = verdict_for(good_facts(facts), product, today, now)
    assert v.verdict == "Verified"
    assert v.checks == ["origin_match: pass", "cert_validity: pass"]


def test_verdict_fields(facts, product, today, now):
    v = verdict_for(facts, product, today, now)
    assert v.reviewed_by_human is False
    assert v.rules_version == "rules-v1.0"
    assert v.generated_at == "2026-11-16T12:00:00Z"
    assert v.reason and "Bangladesh" in v.reason


def test_matches_answer_key(facts, product, today, now):
    key = json.loads((DATA / "answer_key.json").read_text(encoding="utf-8"))["tshirt-001"]["claims"]["c1"]
    v = verdict_for(facts, product, today, now)
    assert v.verdict == key["expected_verdict"]
    failing = {c.split(":")[0] for c in v.checks if c.endswith("fail")}
    assert failing == set(key["expected_failing_checks"])


def test_no_origin_facts_is_missing_never_verified(facts, product, today, now):
    f = [x.model_copy(update={"origin_country": None}) for x in facts]
    c = origin_match("India", f)
    assert c.result == "pass" and c.detail == "No fact states an origin country"
    v = verdict_for(f, product, today, now)
    assert v.verdict == "Missing evidence"
