import json
import os
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from backend import extract, reason
from backend.models import Fact

DATA = Path(__file__).resolve().parent.parent / "data"
TODAY = date(2026, 11, 16)
NOW = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)

# ---------------------------------------------------------------- no network, no key, outside the live test
# backend.extract calls load_dotenv() on import, so a MOCK_MODE or ANTHROPIC_API_KEY in .env (or in the shell) reaches
# every test. These fixtures pin the test environment instead. Only tests/test_extract_live.py may use the real thing.
REAL_MAKE_CLIENT = extract._make_client
_ORIGINAL_API_KEY = os.environ.get("ANTHROPIC_API_KEY")  # kept only so the live test can restore it


def _forbid_client(*args, **kwargs):
    raise AssertionError("a non-live test tried to create a real API client")


def _lock_down(mp: pytest.MonkeyPatch) -> None:
    mp.setenv("MOCK_MODE", "true")
    mp.delenv("ANTHROPIC_API_KEY", raising=False)
    mp.setenv("EXTRACTION_CACHE", "off")
    mp.setattr(extract, "_make_client", _forbid_client)
    mp.setattr(reason, "_make_client", _forbid_client)
    mp.setenv("REASON_CACHE", "off")


@pytest.fixture(scope="session", autouse=True)
def _no_network_for_session():
    """Covers module- and session-scoped fixtures (e.g. test_bottle's `runs`), which run before any function fixture."""
    with pytest.MonkeyPatch.context() as mp:
        _lock_down(mp)
        yield


@pytest.fixture(autouse=True)
def _no_network(request, monkeypatch):
    """Re-applies the lock-down for every test, so earlier code that changed os.environ directly cannot leak."""
    if request.module.__name__.rsplit(".", 1)[-1] == "test_extract_live":
        if _ORIGINAL_API_KEY:
            monkeypatch.setenv("ANTHROPIC_API_KEY", _ORIGINAL_API_KEY)
        monkeypatch.delenv("EXTRACTION_CACHE", raising=False)
        monkeypatch.setattr(extract, "_make_client", REAL_MAKE_CLIENT)
        return
    _lock_down(monkeypatch)


@pytest.fixture
def real_make_client(monkeypatch):
    """For the few tests of _make_client itself: they remove the key or patch anthropic.Anthropic, so no call is possible."""
    monkeypatch.setattr(extract, "_make_client", REAL_MAKE_CLIENT)


def load_facts() -> list[Fact]:
    facts = []
    # Only the t-shirt's documents: other products' mocks must not leak into the tshirt fixtures.
    tshirt = json.loads((DATA / "products.json").read_text(encoding="utf-8"))["tshirt-001"]
    for doc in sorted(d["doc_id"] for d in tshirt["documents"]):
        p = DATA / "mock_extractions" / f"{doc}.json"
        facts += [Fact(**f) for f in json.loads(p.read_text(encoding="utf-8"))["facts"]]
    return facts


def by_id(facts, fid) -> Fact:
    return next(f for f in facts if f.fact_id == fid)


@pytest.fixture
def facts():
    return load_facts()


@pytest.fixture
def product():
    return json.loads((DATA / "products.json").read_text(encoding="utf-8"))["tshirt-001"]


@pytest.fixture
def today():
    return TODAY


@pytest.fixture
def now():
    return NOW
