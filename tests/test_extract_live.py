"""Live extraction test. Spends real money, so it runs only when BOTH are set:
ANTHROPIC_API_KEY and RUN_LIVE_EXTRACTION=1. A plain `pytest` skips it.
"""
import os

import pytest

from backend import extract
from scripts.compare_extraction import KEY_FIELDS, KNOWN_DIFFERENCES, sets_match, value_set
from tests.conftest import DATA

SLICE_DOCS = ["tshirt_bom", "tier1_declaration", "tier2_gots_certificate", "tier3_ginning_invoice"]

pytestmark = pytest.mark.skipif(
    not (os.environ.get("ANTHROPIC_API_KEY") and os.environ.get("RUN_LIVE_EXTRACTION") == "1"),
    reason="live extraction needs ANTHROPIC_API_KEY and RUN_LIVE_EXTRACTION=1",
)


@pytest.mark.parametrize("stem", SLICE_DOCS)
def test_real_extraction_matches_mock_key_fields(stem, monkeypatch, tmp_path):
    monkeypatch.setenv("MOCK_MODE", "false")
    path = DATA / "documents" / f"{stem}.txt"
    real = extract.extract_facts(path, context=extract.doc_context(path), cache_dir=tmp_path)
    mock = extract.extract_mock(path)

    assert real.accepted, f"no accepted facts; rejected: {[r.reason for r in real.rejected]}"
    # Compare the set of non-null values per field: the model may split facts differently from the hand-written mock.
    diffs = []
    for field in KEY_FIELDS:
        m, r = value_set(mock.accepted, field), value_set(real.accepted, field)
        if (stem, field) not in KNOWN_DIFFERENCES and not sets_match(m, r):
            diffs.append(f"{field}: mock={m} real={r}")
    assert not diffs, "; ".join(diffs)


def test_bangladesh_quote_accepted(monkeypatch, tmp_path):
    monkeypatch.setenv("MOCK_MODE", "false")
    path = DATA / "documents" / "tier3_ginning_invoice.txt"
    real = extract.extract_facts(path, context=extract.doc_context(path), cache_dir=tmp_path)
    assert any(f.source_quote == "Country of origin: Bangladesh" and f.origin_country == "Bangladesh"
               for f in real.accepted)
