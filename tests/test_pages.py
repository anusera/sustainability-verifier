import json
import re
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.main import FRONTEND_DIR, Settings, create_app

REPO = Path(__file__).resolve().parent.parent
FOOTER = "This page supports, but does not replace, claims printed on pack."
PAGES = ["index.html", "shopper.html", "regulator.html", "dashboard.html"]
LABELS = ["Verified against supplier documents", "Supplier statement only", "Not yet proven",
          "This claim is under review", "Contradicted", "Self-declared only"]


@pytest.fixture
def client(tmp_path):
    shutil.copy(REPO / "data" / "products.json", tmp_path / "products.json")
    st = Settings(log_path=tmp_path / "audit_log.jsonl", docs_dir=REPO / "data" / "documents",
                  mock_dir=REPO / "data" / "mock_extractions", products_path=tmp_path / "products.json",
                  runs_dir=tmp_path / "runs", session_secret="test-secret")
    return TestClient(create_app(st))


def suppliers():
    products = json.loads((REPO / "data" / "products.json").read_text(encoding="utf-8"))
    return [s["supplier"] for p in products.values() for s in p["supply_chain"]]


def test_shopper_page_has_footer(client):
    r = client.get("/p/tshirt-001")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert FOOTER in r.text


def test_unknown_product_pages_are_html_404(client):
    for path in ("/p/unknown", "/regulator/unknown"):
        r = client.get(path)
        assert r.status_code == 404
        assert "text/html" in r.headers["content-type"]
        assert "Product not found" in r.text


def test_regulator_html_has_no_supplier_names(client):
    r = client.get("/regulator/tshirt-001")
    assert r.status_code == 200
    for name in suppliers():
        assert name not in r.text


def test_home_page(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "View as shopper" in r.text and "View as regulator" in r.text
    assert "No product found for this code" in r.text


def test_shopper_html_has_no_names_or_verdict_text():
    text = (FRONTEND_DIR / "shopper.html").read_text(encoding="utf-8")
    for s in suppliers() + LABELS:
        assert s not in text


@pytest.mark.parametrize("name", PAGES)
def test_pages_are_self_contained_and_safe(name):
    text = (FRONTEND_DIR / name).read_text(encoding="utf-8")
    assert "innerHTML" not in text
    assert "outerHTML" not in text and "document.write" not in text
    assert not re.search(r"https?://", text)
    assert not re.search(r"<script[^>]*\ssrc=", text)
    assert "<link" not in text
