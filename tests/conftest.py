import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from backend.models import Fact

DATA = Path(__file__).resolve().parent.parent / "data"
TODAY = date(2026, 11, 16)
NOW = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)


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
