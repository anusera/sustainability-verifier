import json
import re
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.main import Settings, create_app

REPO = Path(__file__).resolve().parent.parent
REAL_LOG = REPO / "data" / "audit_log.jsonl"
DEMO = {"username": "regulator", "password": "demo-regulator"}
BAD = {"username": "regulator", "password": "wrong"}
RETAILER = {"username": "retailer", "password": "demo-retailer"}


@pytest.fixture(scope="session", autouse=True)
def real_log_untouched():
    before = REAL_LOG.read_bytes() if REAL_LOG.exists() else None
    yield
    after = REAL_LOG.read_bytes() if REAL_LOG.exists() else None
    assert before == after, "tests modified data/audit_log.jsonl"


@pytest.fixture(autouse=True)
def mock_mode(monkeypatch):
    monkeypatch.setenv("MOCK_MODE", "true")


@pytest.fixture
def env(tmp_path):
    shutil.copytree(REPO / "data" / "documents", tmp_path / "docs")
    shutil.copytree(REPO / "data" / "mock_extractions", tmp_path / "mock")
    shutil.copy(REPO / "data" / "products.json", tmp_path / "products.json")
    return Settings(
        log_path=tmp_path / "audit_log.jsonl", docs_dir=tmp_path / "docs", mock_dir=tmp_path / "mock",
        products_path=tmp_path / "products.json", runs_dir=tmp_path / "runs",
        session_secret="test-secret")


@pytest.fixture
def anon_client(env):
    """No cookies at all."""
    return TestClient(create_app(env))


@pytest.fixture
def client(anon_client):
    """Signed in as the retailer, which POST /api/run requires. Not signed in as regulator."""
    assert anon_client.post("/api/retailer/login", json=RETAILER).status_code == 200
    return anon_client


def entries(env, event_type=None):
    lines = env.log_path.read_text(encoding="utf-8").splitlines() if env.log_path.exists() else []
    out = [json.loads(line) for line in lines if line.strip()]
    return [e for e in out if event_type is None or e["event_type"] == event_type]


def login(client):
    assert client.post("/api/regulator/login", json=DEMO).status_code == 200


def test_run_returns_contradicted_with_both_checks_failing(client):
    r = client.post("/api/run/tshirt-001")
    assert r.status_code == 200
    claim = r.json()["claims"][0]
    assert claim["verdict"]["verdict"] == "Contradicted"
    assert claim["verdict"]["checks"] == ["origin_match: fail", "cert_validity: fail"]


def test_run_requires_retailer_login(anon_client, env):
    assert anon_client.post("/api/run/tshirt-001").status_code == 401
    assert not env.log_path.exists() and not env.runs_dir.exists()  # nothing ran


def test_run_rejects_regulator_cookie(anon_client):
    assert anon_client.post("/api/regulator/login", json=DEMO).status_code == 200
    assert anon_client.post("/api/run/tshirt-001").status_code == 401


def test_run_rejects_tampered_retailer_cookie(anon_client):
    anon_client.cookies.set("retailer_session", "9999999999.deadbeef")
    assert anon_client.post("/api/run/tshirt-001").status_code == 401


def test_run_allowed_with_retailer_cookie(anon_client):
    assert anon_client.post("/api/retailer/login", json=RETAILER).status_code == 200
    assert anon_client.post("/api/run/tshirt-001").status_code == 200


def test_facts_include_bangladesh_quote_and_source_file(client):
    facts = client.post("/api/run/tshirt-001").json()["facts"]
    hit = [f for f in facts if "Country of origin: Bangladesh" in f["source_quote"]]
    assert hit and hit[0]["source_file"] == "tier3_ginning_invoice.txt"


def test_public_response_leaks_nothing(client, env):
    client.post("/api/run/tshirt-001")
    run = client.post("/api/run/tshirt-001").json()
    r = client.get("/api/public/tshirt-001")
    assert r.status_code == 200
    data = r.json()
    assert set(data) == {"product_name", "claims"}
    assert set(data["claims"][0]) == {"claim_text", "status_label", "summary", "last_verified"}
    text = r.text
    product = json.loads(env.products_path.read_text(encoding="utf-8"))["tshirt-001"]
    for s in product["supply_chain"]:
        assert s["supplier"] not in text
    for d in product["documents"]:
        assert d["file"] not in text and d["doc_id"] not in text
    assert ".txt" not in text
    assert not re.search(r"\b[0-9a-f]{64}\b", text)
    assert "Bangladesh" not in text
    assert run["claims"][0]["verdict"]["reason"] not in text


def test_public_status_label_for_contradicted(client):
    client.post("/api/run/tshirt-001")
    claim = client.get("/api/public/tshirt-001").json()["claims"][0]
    assert claim["status_label"] == "This claim is under review"


def test_public_before_any_run_is_404(anon_client):
    client = anon_client
    r = client.get("/api/public/tshirt-001")
    assert r.status_code == 404 and "Not yet verified" in r.json()["detail"]


def test_regulator_endpoints_require_login(client):
    client.post("/api/run/tshirt-001")
    urls = ["/api/regulator/tshirt-001", "/api/regulator/tshirt-001/verify-chain"]
    for u in urls:
        assert client.get(u).status_code == 401
    assert client.post("/api/regulator/login", json=BAD).status_code == 401
    for u in urls:
        assert client.get(u).status_code == 401
    login(client)
    for u in urls:
        assert client.get(u).status_code == 200


def test_tampered_cookie_rejected(client):
    client.post("/api/run/tshirt-001")
    client.cookies.set("regulator_session", "9999999999.deadbeef")
    assert client.get("/api/regulator/tshirt-001").status_code == 401


def test_regulator_view_has_quotes_and_hashes(client):
    client.post("/api/run/tshirt-001")
    login(client)
    d = client.get("/api/regulator/tshirt-001").json()
    assert any("Bangladesh" in f["source_quote"] for f in d["facts"])
    assert len(d["documents"]) == 4 and all(len(x["sha256"]) == 64 for x in d["documents"])
    assert d["history"]


def test_regulator_access_logged_only_on_success(client, env):
    client.post("/api/run/tshirt-001")
    client.get("/api/regulator/tshirt-001")
    assert entries(env, "regulator_access") == []
    login(client)
    client.get("/api/regulator/tshirt-001")
    acc = entries(env, "regulator_access")
    assert len(acc) == 1 and acc[0]["actor"] == "regulator"


def test_verify_chain_intact_after_run(client):
    client.post("/api/run/tshirt-001")
    login(client)
    assert client.get("/api/regulator/tshirt-001/verify-chain").json() == {"intact": True}


def test_verify_chain_reports_break_after_edit(client, env):
    client.post("/api/run/tshirt-001")
    login(client)
    lines = env.log_path.read_text(encoding="utf-8").splitlines()
    lines[2] = lines[2].replace("tshirt-001", "tshirt-002", 1)
    env.log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    d = client.get("/api/regulator/tshirt-001/verify-chain").json()
    assert d["intact"] is False and d["first_broken_entry"] == 3


def test_unknown_product_404(client):
    assert client.post("/api/run/nope").status_code == 404
    assert "Unknown product 'nope'" in client.post("/api/run/nope").json()["detail"]
    assert client.get("/api/public/nope").status_code == 404
    login(client)
    assert client.get("/api/regulator/nope").status_code == 404
    assert client.get("/api/regulator/nope/verify-chain").status_code == 404


def test_second_run_adds_no_duplicate_entries(client, env):
    client.post("/api/run/tshirt-001")
    r2 = client.post("/api/run/tshirt-001").json()
    assert len(entries(env, "verdict_changed")) == 1
    assert len(entries(env, "document_ingested")) == 4
    assert r2["claims"][0]["verdict_changed"] is False


def test_edited_document_logs_one_new_ingest_and_breaks_old_entry(client, env):
    client.post("/api/run/tshirt-001")
    doc = env.docs_dir / "tier1_declaration.txt"
    doc.write_bytes(doc.read_bytes() + b" ")
    client.post("/api/run/tshirt-001")
    assert len(entries(env, "document_ingested")) == 5
    login(client)
    d = client.get("/api/regulator/tshirt-001/verify-chain").json()
    assert d["intact"] is False and "document changed" in d["reason"]


def test_missing_mock_extraction_is_listed_not_fatal(client, env):
    (env.mock_dir / "tshirt_bom.json").unlink()
    r = client.post("/api/run/tshirt-001")
    assert r.status_code == 200
    failed = r.json()["failed_documents"]
    assert [f["doc_id"] for f in failed] == ["tshirt_bom"]


def test_products_list(client):
    assert client.get("/api/products").json() == [
        {"product_id": "tshirt-001", "name": "Everyday Organic Tee"},
        {"product_id": "bottle-001", "name": "Spring Water 500ml"},
        {"product_id": "bottle-eco-001", "name": "Spring Water 330ml"},
        {"product_id": "shirt-001", "name": "Organic Poplin Shirt"},
    ]
