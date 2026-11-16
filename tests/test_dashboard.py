import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.main import Settings, create_app
from backend.pipeline import load_snapshot
from backend.retailer import build_retailer_overview

REPO = Path(__file__).resolve().parent.parent
RETAILER = {"username": "retailer", "password": "demo-retailer"}
REGULATOR = {"username": "regulator", "password": "demo-regulator"}

SECOND = {
    "product_id": "bottle-002", "name": "Test Bottle",
    "claims": [{"claim_id": "c1", "type": "origin", "text": "Made in Spain", "claimed_origin": "Spain"}],
    "documents": [], "supply_chain": [{"tier": 1, "supplier": "Bottle Maker SL"}],
}


@pytest.fixture(autouse=True)
def mock_mode(monkeypatch):
    monkeypatch.setenv("MOCK_MODE", "true")


@pytest.fixture
def settings(tmp_path):
    shutil.copytree(REPO / "data" / "documents", tmp_path / "docs")
    shutil.copytree(REPO / "data" / "mock_extractions", tmp_path / "mock")
    products = json.loads((REPO / "data" / "products.json").read_text(encoding="utf-8"))
    products["bottle-002"] = SECOND
    (tmp_path / "products.json").write_text(json.dumps(products), encoding="utf-8")
    return Settings(
        log_path=tmp_path / "audit_log.jsonl", docs_dir=tmp_path / "docs", mock_dir=tmp_path / "mock",
        products_path=tmp_path / "products.json", runs_dir=tmp_path / "runs",
        session_secret="reg-secret", retailer_secret="ret-secret")


@pytest.fixture
def client(settings):
    return TestClient(create_app(settings))


@pytest.fixture
def retailer(client):
    assert client.post("/api/retailer/login", json=RETAILER).status_code == 200
    return client


def test_dashboard_route_returns_html_without_supplier_names(client):
    r = client.get("/dashboard")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    for name in ("Sunrise Garments", "Coimbatore", "Delta Ginning", "Bottle Maker"):
        assert name not in r.text


def test_retailer_endpoint_requires_retailer_login(client):
    assert client.get("/api/retailer/products").status_code == 401
    bad = client.post("/api/retailer/login", json={**RETAILER, "password": "wrong"})
    assert bad.status_code == 401
    assert client.get("/api/retailer/products").status_code == 401


def test_regulator_cookie_does_not_unlock_retailer(client):
    assert client.post("/api/regulator/login", json=REGULATOR).status_code == 200
    assert client.get("/api/retailer/products").status_code == 401


def test_endpoint_returns_all_products(retailer, settings):
    ids = set(json.loads(settings.products_path.read_text(encoding="utf-8")))
    body = retailer.get("/api/retailer/products").json()
    assert {p["product_id"] for p in body["products"]} == ids


def test_unrun_product_is_not_verified(retailer):
    body = retailer.get("/api/retailer/products").json()
    for p in body["products"]:
        assert p["verified"] is False and p["tree"] is None
        for c in p["claims"]:
            assert c["verdict"] is None and c["badge"] == "none" and c["label"] == "Not yet verified"
    assert body["summary"]["not_yet_verified"] == 2
    assert body["summary"]["total_claims"] == 2


def test_after_run_counts_match_verdicts(retailer):
    assert retailer.post("/api/run/tshirt-001").status_code == 200
    body = retailer.get("/api/retailer/products").json()
    tee = next(p for p in body["products"] if p["product_id"] == "tshirt-001")
    claim = tee["claims"][0]
    assert claim["verdict"] == "Contradicted" and claim["badge"] == "red" and claim["label"] == "Contradicted"
    assert claim["reason"]
    assert {k["check_id"]: k["result"] for k in claim["checks"]} == {"origin_match": "fail", "cert_validity": "fail"}
    s = body["summary"]
    assert (s["verified"], s["contradicted"], s["needs_evidence"], s["not_yet_verified"]) == (0, 1, 0, 1)
    assert s["verified"] + s["contradicted"] + s["needs_evidence"] + s["not_yet_verified"] == s["total_claims"]
    assert tee["tree"]["status"] != "no_facts"


def test_public_api_still_leaks_nothing(retailer):
    assert retailer.post("/api/run/tshirt-001").status_code == 200
    retailer.cookies.clear()  # the public API needs no login
    r = retailer.get("/api/public/tshirt-001")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"product_name", "claims"}
    assert set(body["claims"][0]) == {"claim_text", "status_label", "summary", "last_verified"}
    for leak in ("Sunrise", "Coimbatore", "Delta", ".txt", "origin_match", "GOT-IN"):
        assert leak not in r.text


@pytest.mark.parametrize("verdict,badge,count", [
    ("Verified", "green", "verified"),
    ("Self-declared only", "amber", "needs_evidence"),
    ("Missing evidence", "amber", "needs_evidence"),
    ("Contradicted", "red", "contradicted"),
])
def test_badge_and_count_mapping(retailer, settings, verdict, badge, count):
    assert retailer.post("/api/run/tshirt-001").status_code == 200
    run = load_snapshot("tshirt-001", settings.runs_dir)
    claim = run.claims[0]
    run.claims[0] = claim.model_copy(update={"verdict": claim.verdict.model_copy(update={"verdict": verdict})})
    products = json.loads(settings.products_path.read_text(encoding="utf-8"))
    out = build_retailer_overview({"tshirt-001": products["tshirt-001"]}, {"tshirt-001": run})
    assert out["products"][0]["claims"][0]["badge"] == badge
    assert out["summary"][count] == 1 and out["summary"]["total_claims"] == 1


def test_tree_statuses(retailer, settings):
    assert retailer.post("/api/run/tshirt-001").status_code == 200
    run = load_snapshot("tshirt-001", settings.runs_dir)
    products = json.loads(settings.products_path.read_text(encoding="utf-8"))
    tier3 = run.tree.children[0].children[0]
    tier3.status, tier3.fact_count = "no_facts", 0
    out = build_retailer_overview({"tshirt-001": products["tshirt-001"]}, {"tshirt-001": run})
    t1 = out["products"][0]["tree"]
    t2, t3 = t1["children"][0], t1["children"][0]["children"][0]
    assert t3["status"] == "no_facts"
    assert t2["status"] == "contradicted"  # expired certificate fact is cited by a failed check
    assert t1["status"] in ("ok", "contradicted")
