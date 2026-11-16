import json
import shutil

import pytest
from fastapi.testclient import TestClient

from backend import pipeline
from backend.extract import extract_facts
from backend.main import Settings, create_app
from backend.models import Fact
from backend.rules import run_rules
from backend.verdict import decide_verdict

from .conftest import DATA, NOW, TODAY

DOCS = DATA / "documents"
KEY = json.loads((DATA / "answer_key.json").read_text(encoding="utf-8"))
BOTTLE_DOCS = ["bottle_declaration", "bottle_packaging_spec", "bottle_recycled_certificate", "eco_bottle_statement"]
SUPPLIERS = ["Clearbrook", "Northfield", "Ridgeway"]


@pytest.fixture(autouse=True)
def mock_mode(monkeypatch):
    monkeypatch.setenv("MOCK_MODE", "true")


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("bottle")
    out = {}
    for pid in ("bottle-001", "bottle-eco-001"):
        out[pid] = pipeline.run_product(pid, log_path=tmp / "log.jsonl", runs_dir=tmp / "runs",
                                        today=TODAY, now=NOW)
    return out


@pytest.fixture
def bottle(runs):
    return runs["bottle-001"]


@pytest.fixture
def eco(runs):
    return runs["bottle-eco-001"]


def test_bottle_contradicted_with_missing_tier(bottle):
    c = bottle.claims[0]
    assert [str(k) for k in (f"{x.check_id}: {x.result}" for x in c.checks)] == [
        "recycled_content_match: fail", "cert_validity: pass", "tier_coverage: fail", "vague_claim: pass"]
    v = c.verdict
    assert v.verdict == "Contradicted" and v.rules_version == "rules-v1.0"
    assert v.evidence == ["f-b2-1", "f-b3-2", "f-b1-1", "f-b3-1"]
    for text in ("60%", "f-b2-1", "f-b3-2", "Also missing evidence",
                 "Tier 3 (Ridgeway Resin Supply): no facts"):
        assert text in v.reason
    assert v.reason == (
        "Evidence contradicts the claim: Claimed 100% recycled content; "
        "f-b2-1 (tier 2, Northfield PET Converters Ltd) states 60%; "
        "f-b3-2 (tier 2, Northfield PET Converters Ltd) states 60%. Meeting the claim: f-b1-1. "
        "Also missing evidence: Tier 3 (Ridgeway Resin Supply): no facts.")


def test_recycled_pct_only_on_body_facts(bottle):
    pct = {f.fact_id: f.recycled_content_pct for f in bottle.facts if f.recycled_content_pct is not None}
    assert pct == {"f-b1-1": 100, "f-b2-1": 60, "f-b3-2": 60}
    assert all(0 <= p <= 100 for p in pct.values())
    by_id = {f.fact_id: f for f in bottle.facts}
    assert by_id["f-b2-1"].weight_kg == 0.0125
    assert by_id["f-b2-2"].recycled_content_pct is None and by_id["f-b2-3"].recycled_content_pct is None


@pytest.mark.parametrize("doc", BOTTLE_DOCS)
def test_quotes_validate_against_documents(doc):
    path = DOCS / f"{doc}.txt"
    result = extract_facts(path)
    assert result.rejected == [] and result.accepted
    text = path.read_bytes().decode("utf-8").replace("\r\n", "\n")
    for f in result.accepted:
        assert f.source_quote in text
        assert (DOCS / f.source_file).is_file()
        assert isinstance(f.source_page, int)


def test_tier3_has_no_facts_or_documents(bottle):
    product = pipeline.get_product("bottle-001")
    assert not [d for d in product["documents"] if d["tier"] == 3]
    assert bottle.failed_documents == [] and bottle.rejected == []
    tiers = {}
    node = bottle.tree
    while node:
        tiers[node.tier] = node.status
        node = node.children[0] if node.children else None
    assert tiers == {1: "has_facts", 2: "has_facts", 3: "no_facts"}
    cert = next(c for c in bottle.claims[0].checks if c.check_id == "cert_validity")
    assert cert.result == "pass"


def test_eco_product_self_declared_only(eco):
    c = eco.claims[0]
    assert [f"{x.check_id}: {x.result}" for x in c.checks] == [
        "cert_validity: pass", "tier_coverage: pass", "vague_claim: fail"]
    assert c.verdict.verdict == "Self-declared only"
    assert c.verdict.reason == ("Only supplier statements were found; no third-party certificate. "
                                "Claim wording is unqualified: 'Eco-friendly'. Not yet proven.")


@pytest.mark.parametrize("pid", ["bottle-001", "bottle-eco-001"])
def test_matches_answer_key(runs, pid):
    run = runs[pid]
    for claim_id, key in KEY[pid]["claims"].items():
        c = next(x for x in run.claims if x.claim_id == claim_id)
        assert c.verdict.verdict == key["expected_verdict"]
        failing = [x.check_id for x in c.checks if x.result == "fail"]
        assert failing == key["expected_failing_checks"]
        facts = {f.fact_id: f for f in run.facts}
        for flaw in key["planted_flaws"]:
            if "fact_id" in flaw:
                assert flaw["fact_id"] in facts
                assert facts[flaw["fact_id"]].source_file == flaw["source_file"]


# ---- packaging_wording rules
def _fact(tier=1, **kw):
    return Fact(fact_id="x", doc_id="d", supplier="S", tier=tier, source_file="d.txt", source_quote="q", **kw)


def test_packaging_wording_runs_three_checks():
    claim = {"claim_id": "c1", "type": "packaging_wording", "text": "Eco-friendly packaging"}
    checks = run_rules(claim, [_fact()], TODAY, [{"tier": 1, "supplier": "S"}])
    assert [c.check_id for c in checks] == ["cert_validity", "tier_coverage", "vague_claim"]


def test_packaging_wording_without_facts_is_missing_evidence():
    claim = {"claim_id": "c1", "type": "packaging_wording", "text": "Eco-friendly packaging"}
    chain = [{"tier": 1, "supplier": "S"}]
    checks = run_rules(claim, [], TODAY, chain)
    v = decide_verdict("p", "c1", checks, [], chain, NOW, claim_type="packaging_wording")
    assert v.verdict == "Missing evidence"


# ---- public API leaks nothing
@pytest.fixture
def client(tmp_path):
    shutil.copytree(DOCS, tmp_path / "docs")
    shutil.copytree(DATA / "mock_extractions", tmp_path / "mock")
    shutil.copy(DATA / "products.json", tmp_path / "products.json")
    st = Settings(log_path=tmp_path / "log.jsonl", docs_dir=tmp_path / "docs", mock_dir=tmp_path / "mock",
                  products_path=tmp_path / "products.json", runs_dir=tmp_path / "runs",
                  session_secret="s", retailer_secret="r")
    c = TestClient(create_app(st))
    assert c.post("/api/retailer/login", json={"username": "retailer", "password": "demo-retailer"}).status_code == 200
    return c


@pytest.mark.parametrize("pid,label", [("bottle-001", "This claim is under review"),
                                       ("bottle-eco-001", "Supplier statement only")])
def test_public_api_leaks_nothing(client, pid, label):
    assert client.post(f"/api/run/{pid}").status_code == 200
    r = client.get(f"/api/public/{pid}")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"product_name", "claims"}
    assert body["claims"][0]["status_label"] == label
    text = r.text
    forbidden = SUPPLIERS + [".txt", "bottle_", "eco_bottle", "f-b", "f-e", "60", "30%", "rPET", "RCC-DEMO", "Tier"]
    for word in forbidden:
        assert word not in text, word
