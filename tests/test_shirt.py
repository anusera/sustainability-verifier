"""shirt-001 "Organic Poplin Shirt": a clean product that reaches Verified. Mock mode, no key, no network."""
import json
import shutil
from datetime import date

import pytest
from fastapi.testclient import TestClient

from backend import pipeline
from backend.extract import extract_facts, normalise_newlines
from backend.main import Settings, create_app
from backend.rules import run_rules
from backend.verdict import decide_verdict

from .conftest import DATA, NOW, TODAY

DOCS = DATA / "documents"
KEY = json.loads((DATA / "answer_key.json").read_text(encoding="utf-8"))
SHIRT_DOCS = ["shirt_bom", "shirt_declaration", "shirt_organic_certificate", "shirt_ginning_invoice"]
LEAKS = ["Larkspur", "Meridian", "Harvest Moon", "Panipat", "Guntur", "Erode", ".txt", "shirt_", "f-sh",
         "OTX-DEMO", "origin_match", "Tier"]
RETAILER = {"username": "retailer", "password": "demo-retailer"}


@pytest.fixture(autouse=True)
def mock_mode(monkeypatch):
    monkeypatch.setenv("MOCK_MODE", "true")


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("shirt")
    return pipeline.run_product("shirt-001", log_path=tmp / "log.jsonl", runs_dir=tmp / "runs",
                                today=TODAY, now=NOW)


def test_verified_with_both_checks_passing(run):
    c = run.claims[0]
    assert [f"{x.check_id}: {x.result}" for x in c.checks] == ["origin_match: pass", "cert_validity: pass"]
    assert c.checks[0].detail == "All origin facts match claimed India: f-sh1-1, f-sh3-1."
    assert c.checks[1].detail == "All certificates are valid on every compared date: f-sh2-1 (valid_to 2030-01-31)."
    v = c.verdict
    assert v.verdict == "Verified" and v.rules_version == "rules-v1.0"
    assert v.reason == "All checks passed and a valid third-party certificate is present."
    assert v.reason_source == "template"
    assert v.evidence == ["f-sh1-1", "f-sh3-1", "f-sh2-1"]
    assert run.failed_documents == [] and run.rejected == [] and run.documents_without_facts == []
    assert len(run.facts) == 5


def test_every_tier_has_facts(run):
    tiers = {}
    node = run.tree
    while node:
        tiers[node.tier] = node.status
        node = node.children[0] if node.children else None
    assert tiers == {1: "has_facts", 2: "has_facts", 3: "has_facts"}


@pytest.mark.parametrize("doc", SHIRT_DOCS)
def test_quotes_validate_against_documents(doc):
    path = DOCS / f"{doc}.txt"
    result = extract_facts(path)
    assert result.rejected == [] and result.accepted
    text = normalise_newlines(path.read_bytes().decode("utf-8"))
    for f in result.accepted:
        assert f.source_quote in text
        assert (DOCS / f.source_file).is_file()
        assert isinstance(f.source_page, int)


def test_documents_are_lf_ascii():
    for doc in SHIRT_DOCS:
        raw = (DOCS / f"{doc}.txt").read_bytes()
        assert b"\r" not in raw
        raw.decode("ascii")


def test_certificate_has_no_origin_or_issue_date_and_outlasts_documents(run):
    cert = next(f for f in run.facts if f.certificate_id)
    assert cert.origin_country is None and cert.document_date is None
    valid_to = date.fromisoformat(cert.valid_to)
    assert valid_to >= date(2029, 12, 31)
    for f in run.facts:
        if f.document_date:
            assert date.fromisoformat(f.document_date) < valid_to
    assert [f.fact_id for f in run.facts if f.certificate_id] == ["f-sh2-1"]  # the invoice number is not a certificate


def test_matches_answer_key(run):
    key = KEY["shirt-001"]["claims"]["c1"]
    c = run.claims[0]
    assert c.verdict.verdict == key["expected_verdict"]
    assert [x.check_id for x in c.checks if x.result == "fail"] == key["expected_failing_checks"]
    assert key["planted_flaws"] == []


# ---- what-if scenarios on in-memory copies (data files are never edited)
def _verdict_for(run, facts):
    product = pipeline.get_product("shirt-001")
    claim = product["claims"][0]
    checks = run_rules(claim, facts, TODAY, product["supply_chain"])
    return checks, decide_verdict("shirt-001", "c1", checks, facts, product["supply_chain"], NOW, claim_type="origin")


def test_bangladesh_invoice_origin_gives_contradicted(run):
    facts = [f.model_copy(update={"origin_country": "Bangladesh"}) if f.fact_id == "f-sh3-1" else f
             for f in run.facts]
    checks, v = _verdict_for(run, facts)
    assert v.verdict == "Contradicted"
    assert next(c for c in checks if c.check_id == "origin_match").result == "fail"


def test_certificate_expiring_before_invoice_is_not_verified(run):
    facts = [f.model_copy(update={"valid_to": "2026-08-01"}) if f.fact_id == "f-sh2-1" else f for f in run.facts]
    checks, v = _verdict_for(run, facts)
    assert next(c for c in checks if c.check_id == "cert_validity").result == "fail"
    assert v.verdict != "Verified"


# ---- API, public view and dashboard
@pytest.fixture
def client(tmp_path):
    shutil.copytree(DOCS, tmp_path / "docs")
    shutil.copytree(DATA / "mock_extractions", tmp_path / "mock")
    shutil.copy(DATA / "products.json", tmp_path / "products.json")
    st = Settings(log_path=tmp_path / "log.jsonl", docs_dir=tmp_path / "docs", mock_dir=tmp_path / "mock",
                  products_path=tmp_path / "products.json", runs_dir=tmp_path / "runs",
                  session_secret="s", retailer_secret="r")
    c = TestClient(create_app(st))
    assert c.post("/api/retailer/login", json=RETAILER).status_code == 200
    return c


def test_public_api_leaks_nothing(client):
    assert client.post("/api/run/shirt-001").status_code == 200
    r = client.get("/api/public/shirt-001")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"product_name", "claims"}
    assert body["product_name"] == "Organic Poplin Shirt"
    assert body["claims"][0]["status_label"] == "Verified against supplier documents"
    assert body["claims"][0]["summary"] == "Supplier documents back this claim."
    for word in LEAKS:
        assert word not in r.text, word


def test_dashboard_counts_after_running_shirt_only(client):
    assert client.post("/api/run/shirt-001").status_code == 200
    body = client.get("/api/retailer/products").json()
    shirt = next(p for p in body["products"] if p["product_id"] == "shirt-001")
    claim = shirt["claims"][0]
    assert claim["verdict"] == "Verified" and claim["badge"] == "green" and claim["label"] == "Verified"
    s = body["summary"]
    assert (s["verified"], s["contradicted"], s["needs_evidence"], s["not_yet_verified"]) == (1, 0, 0, 3)
    assert s["total_claims"] == 4
