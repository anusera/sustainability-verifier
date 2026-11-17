import json
import shutil

import anthropic
import pytest

from backend.extract import (
    DEFAULT_MOCK_DIR,
    ExtractionError,
    MockExtractionMissing,
    extract_facts,
    validate_quotes,
)
from backend.models import Fact
from tests.conftest import DATA

DOCS = ["tshirt_bom", "tier1_declaration", "tier2_gots_certificate", "tier3_ginning_invoice"]


def doc(stem):
    return DATA / "documents" / f"{stem}.txt"


@pytest.fixture(autouse=True)
def _mock_env(monkeypatch):
    monkeypatch.delenv("MOCK_MODE", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


@pytest.mark.parametrize("stem", DOCS)
def test_all_documents_load(stem):
    result = extract_facts(doc(stem))
    assert result.doc_id == stem
    assert result.accepted
    assert result.rejected == []


@pytest.mark.parametrize("stem", DOCS)
def test_every_mock_quote_is_verbatim(stem):
    text = doc(stem).read_bytes().decode("utf-8")
    for fact in extract_facts(doc(stem)).accepted:
        assert fact.source_quote in text


@pytest.mark.parametrize("stem", DOCS)
def test_facts_valid_with_integer_page(stem):
    for fact in extract_facts(doc(stem)).accepted:
        assert isinstance(fact, Fact)
        assert isinstance(fact.source_page, int) and not isinstance(fact.source_page, bool)


def _copy_mock(tmp_path, stem, mutate):
    data = json.loads((DEFAULT_MOCK_DIR / f"{stem}.json").read_text(encoding="utf-8"))
    mutate(data)
    (tmp_path / f"{stem}.json").write_text(json.dumps(data), encoding="utf-8")


def test_altered_quote_rejected_and_reported(tmp_path):
    def mutate(d):
        d["facts"][0]["source_quote"] = d["facts"][0]["source_quote"].replace("Bangladesh", "Bangladesh!")

    _copy_mock(tmp_path, "tier3_ginning_invoice", mutate)
    result = extract_facts(doc("tier3_ginning_invoice"), mock_dir=tmp_path)
    assert result.accepted == []
    assert len(result.rejected) == 1
    assert result.rejected[0].fact.fact_id == "f-t3-1"
    assert "not found verbatim" in result.rejected[0].reason


def test_other_facts_still_accepted_when_one_rejected(tmp_path):
    def mutate(d):
        d["facts"][0]["source_quote"] = "not in the document"

    _copy_mock(tmp_path, "tshirt_bom", mutate)
    result = extract_facts(doc("tshirt_bom"), mock_dir=tmp_path)
    assert [f.fact_id for f in result.accepted] == ["f-bom-2"]
    assert [r.fact.fact_id for r in result.rejected] == ["f-bom-1"]


def test_crlf_and_whitespace_normalised_but_case_is_not(facts):
    fact = next(f for f in facts if f.fact_id == "f-t3-1")
    lf_text = doc("tier3_ginning_invoice").read_bytes().decode("utf-8")
    crlf_text = lf_text.replace("\n", "\r\n")
    assert validate_quotes([fact], crlf_text) == ([fact], [])
    # Whitespace runs (spaces, tabs, line breaks) are collapsed; the stored quote is the exact document span.
    for loose in ("Country  of   origin: Bangladesh", "Country of\norigin:\tBangladesh"):
        altered = fact.model_copy(update={"source_quote": loose})
        accepted, rejected = validate_quotes([altered], lf_text)
        assert rejected == []
        assert accepted[0].source_quote == "Country of origin: Bangladesh"
    # Nothing looser: case, missing spaces and extra characters still fail.
    for bad in ("country of origin: bangladesh", "Country of origin:Bangladesh", "Country of origin: Bangladesh."):
        altered = fact.model_copy(update={"source_quote": bad})
        accepted, rejected = validate_quotes([altered], lf_text)
        assert accepted == [] and len(rejected) == 1


def test_empty_quote_rejected(facts):
    altered = facts[0].model_copy(update={"source_quote": "  "})
    accepted, rejected = validate_quotes([altered], "anything")
    assert accepted == [] and "empty" in rejected[0].reason


def test_missing_mock_file_raises_clear_error(tmp_path):
    with pytest.raises(MockExtractionMissing, match="tier3_ginning_invoice.txt"):
        extract_facts(doc("tier3_ginning_invoice"), mock_dir=tmp_path)


def test_doc_id_mismatch_raises(tmp_path):
    shutil.copy(DEFAULT_MOCK_DIR / "tshirt_bom.json", tmp_path / "tier1_declaration.json")
    with pytest.raises(ValueError, match="doc_id"):
        extract_facts(doc("tier1_declaration"), mock_dir=tmp_path)


@pytest.mark.parametrize("value", ["false", "False", "0", "no"])
def test_real_mode_without_key_raises_clear_error(monkeypatch, real_make_client, value):
    monkeypatch.setenv("MOCK_MODE", value)
    monkeypatch.setenv("EXTRACTION_CACHE", "off")  # a cached result would make the key unnecessary
    built = []
    monkeypatch.setattr(anthropic, "Anthropic", lambda *a, **k: built.append((a, k)))
    with pytest.raises(ExtractionError) as excinfo:
        extract_facts(doc("tshirt_bom"))
    assert "ANTHROPIC_API_KEY" in str(excinfo.value) and "tshirt_bom.txt" in str(excinfo.value)
    assert built == []  # no client was constructed, so no network


def test_mock_mode_true_explicit(monkeypatch):
    monkeypatch.setenv("MOCK_MODE", "true")
    assert extract_facts(doc("tshirt_bom")).accepted
