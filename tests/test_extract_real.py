"""Real-extraction unit tests. The Anthropic client is always a fake: no key, no network, no cost."""
import json
import shutil
from datetime import date, datetime, timezone
from types import SimpleNamespace

import anthropic
import httpx2
import pytest
from fastapi.testclient import TestClient

from backend import extract, pipeline
from backend.extract import (
    MODEL,
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    DocContext,
    ExtractionError,
    doc_context,
    extract_facts,
    parse_model_output,
    supplier_matches,
    validate_quotes,
)
from backend.main import Settings, create_app
from backend.models import Fact
from tests.conftest import DATA

FAKE_KEY = "sk-test-SECRET"
TODAY = date(2026, 11, 16)
NOW = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)
ALL_DOCS = ["tshirt_bom", "tier1_declaration", "tier2_gots_certificate", "tier3_ginning_invoice",
            "bottle_declaration", "bottle_packaging_spec", "bottle_recycled_certificate", "eco_bottle_statement"]
_FIELDS = ["supplier", "tier", "material", "origin_country", "certification_body", "certificate_id", "valid_from",
           "valid_to", "document_date", "recycled_content_pct", "weight_kg", "source_quote", "confidence"]


def doc(stem):
    return DATA / "documents" / f"{stem}.txt"


def doc_text(stem):
    return doc(stem).read_bytes().decode("utf-8")


def model_item(**overrides):
    """A well-formed model item for tier3_ginning_invoice (the Bangladesh fact)."""
    item = {"supplier": "Delta Ginning Co.", "tier": 3, "material": "Raw organic cotton lint",
            "origin_country": "Bangladesh", "certification_body": None, "certificate_id": None,
            "valid_from": None, "valid_to": None, "document_date": "2026-05-02", "recycled_content_pct": None,
            "weight_kg": 18000, "source_quote": "Country of origin: Bangladesh", "confidence": 0.97}
    item.update(overrides)
    return item


def as_text(*items):
    return json.dumps({"facts": list(items)})


def reply(text, stop_reason="end_turn", model=MODEL):
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], stop_reason=stop_reason, model=model)


class FakeClient:
    """Plays back outputs in order (the last one repeats). An Exception instance is raised instead."""

    def __init__(self, *outputs):
        self.outputs = list(outputs)
        self.calls = []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        out = self.outputs.pop(0) if len(self.outputs) > 1 else self.outputs[0]
        if isinstance(out, Exception):
            raise out
        return out if not isinstance(out, str) else reply(out)


class MockBackedClient:
    """Answers each document from its hand-written mock JSON, so real-mode runs mirror mock-mode runs."""

    def __init__(self, overrides=None):
        self.overrides = overrides or {}  # file name -> text, or Exception
        self.calls = []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        content = kwargs["messages"][0]["content"]
        name = content.split("Document file: ")[1].split("\n")[0]
        if name in self.overrides:
            out = self.overrides[name]
            if isinstance(out, Exception):
                raise out
            return reply(out)
        data = json.loads((DATA / "mock_extractions" / f"{name[:-4]}.json").read_text(encoding="utf-8"))
        items = [{k: v for k, v in f.items() if k in _FIELDS} for f in data["facts"]]
        return reply(as_text(*items))


def sdk_error(cls, status):
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls("boom", response=httpx2.Response(status, request=request, headers={"request-id": "req_test_1"}),
               body=None)


@pytest.fixture(autouse=True)
def real_mode(monkeypatch, tmp_path):
    monkeypatch.setenv("MOCK_MODE", "false")
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    monkeypatch.delenv("EXTRACTION_CACHE", raising=False)
    monkeypatch.setattr(extract, "DEFAULT_CACHE_DIR", tmp_path / "default_cache")  # never touch data/


@pytest.fixture
def cache_dir(tmp_path):
    return tmp_path / "cache"


def run_tier3(client, cache_dir, **kw):
    return extract_facts(doc("tier3_ginning_invoice"), client=client, cache_dir=cache_dir, **kw)


# ---------------------------------------------------------------- happy path and request shape

def test_happy_path_ids_and_provenance_set_by_code(cache_dir):
    result = run_tier3(FakeClient(as_text(model_item())), cache_dir)
    assert result.rejected == []
    [fact] = result.accepted
    assert fact.fact_id == "f-tier3_ginning_invoice-1"
    assert (fact.doc_id, fact.source_file, fact.source_page) == ("tier3_ginning_invoice", "tier3_ginning_invoice.txt", 1)
    assert fact.origin_country == "Bangladesh" and fact.weight_kg == 18000
    assert result.model == "claude-sonnet-5-5" and result.cached is False


def test_request_shape_and_message_content(cache_dir):
    client = FakeClient(as_text(model_item()))
    run_tier3(client, cache_dir)
    [call] = client.calls
    assert call["model"] == MODEL == "claude-sonnet-5-5"
    assert call["system"] == SYSTEM_PROMPT
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert "temperature" not in call
    message = call["messages"][0]["content"]
    assert doc_text("tier3_ginning_invoice") in message
    assert "Registered issuer of this document: Delta Ginning Co. (tier 3)" in message
    assert "Coimbatore Textile Mills" in message  # supply chain list
    assert "Organic cotton from India" not in message  # the claim never steers extraction


def test_schema_follows_documented_limits():
    schema = extract.FACTS_SCHEMA
    item = schema["properties"]["facts"]["items"]
    assert schema["type"] == "object" and schema["additionalProperties"] is False
    assert item["additionalProperties"] is False and set(item["required"]) == set(item["properties"]) == set(_FIELDS)
    text = json.dumps(schema)
    assert "minimum" not in text and "maximum" not in text
    unions = [p for p in item["properties"].values() if isinstance(p["type"], list)]
    assert len(unions) <= 16


def test_prompt_rule_4_wording():
    assert "for a certificate, the\n   certificate holder" in SYSTEM_PROMPT
    assert PROMPT_VERSION == "extract-v1"


# ---------------------------------------------------------------- parsing and retry

def test_code_fences_and_bare_list_parsed():
    item = model_item()
    assert parse_model_output("```json\n" + as_text(item) + "\n```") == [item]
    assert parse_model_output("```\n" + json.dumps([item]) + "\n```") == [item]
    assert parse_model_output("  " + json.dumps([item]) + "\n") == [item]
    with pytest.raises(extract.InvalidModelOutput):
        parse_model_output('{"things": []}')


def test_fenced_output_accepted_end_to_end(cache_dir):
    result = run_tier3(FakeClient("```json\n" + as_text(model_item()) + "\n```"), cache_dir)
    assert len(result.accepted) == 1


def test_invalid_json_retried_once_then_succeeds(cache_dir):
    client = FakeClient("not json at all", as_text(model_item()))
    result = run_tier3(client, cache_dir)
    assert len(client.calls) == 2 and len(result.accepted) == 1


def test_invalid_json_twice_raises_naming_document(cache_dir):
    client = FakeClient("not json at all")
    with pytest.raises(ExtractionError, match="tier3_ginning_invoice.txt") as excinfo:
        run_tier3(client, cache_dir)
    assert len(client.calls) == 2
    assert "invalid JSON twice" in str(excinfo.value) and "not json at all" not in str(excinfo.value)


def test_max_tokens_is_retried_like_invalid_json(cache_dir):
    client = FakeClient(reply(as_text(model_item()), stop_reason="max_tokens"), as_text(model_item()))
    assert len(run_tier3(client, cache_dir).accepted) == 1 and len(client.calls) == 2


def test_refusal_is_an_error_without_retry(cache_dir):
    client = FakeClient(reply("", stop_reason="refusal"))
    with pytest.raises(ExtractionError, match="tier3_ginning_invoice.txt.*declined"):
        run_tier3(client, cache_dir)
    assert len(client.calls) == 1


# ---------------------------------------------------------------- per-item rejections

def test_each_bad_item_rejected_with_reason_and_good_one_kept(cache_dir):
    items = [
        model_item(),
        "just a string",
        {k: v for k, v in model_item().items() if k != "source_quote"},
        model_item(source_quote="Country of origin: Narnia"),
        model_item(recycled_content_pct=120),
        model_item(document_date="31 March 2026"),
        model_item(confidence=1.5),
        model_item(tier=2),
        model_item(supplier="Coimbatore Textile Mills"),
    ]
    result = run_tier3(FakeClient(as_text(*items)), cache_dir)
    assert [f.fact_id for f in result.accepted] == ["f-tier3_ginning_invoice-1"]
    assert len(result.rejected) == 8  # nothing dropped silently
    reasons = " | ".join(r.reason for r in result.rejected)
    for expected in ("not a JSON object", "schema validation failed", "not found verbatim", "outside 0 to 100",
                     "not an ISO date", "confidence 1.5 is outside 0 to 1",
                     "tier 2 does not match registered tier 3", "does not match registered supplier"):
        assert expected in reasons
    unparsed = [r for r in result.rejected if r.fact is None]
    assert len(unparsed) == 2 and all(r.raw is not None for r in unparsed)


def test_padded_bom_row_with_single_spaces_stored_as_exact_line(cache_dir):
    padded = "Fabric           Organic cotton jersey     0.18          Coimbatore Textile Mills"
    item = model_item(supplier="Sunrise Garments Pvt Ltd", tier=1, material="Organic cotton jersey (fabric)",
                      origin_country=None, document_date="2026-03-15", weight_kg=0.18,
                      source_quote="Fabric Organic cotton jersey 0.18 Coimbatore Textile Mills")
    result = extract_facts(doc("tshirt_bom"), client=FakeClient(as_text(item)), cache_dir=cache_dir)
    assert result.rejected == [] and result.accepted[0].source_quote == padded


# ---------------------------------------------------------------- R2: quote edge cases

@pytest.mark.parametrize("quote", ["", "   ", "\n\t"])
def test_empty_or_whitespace_quote_rejected_explicitly(quote):
    fact = Fact(fact_id="x", doc_id="d", supplier="s", tier=1, source_file="d.txt", source_quote=quote)
    accepted, rejected = validate_quotes([fact], "anything at all")
    assert accepted == [] and rejected[0].reason == "source_quote is empty"


def test_first_match_wins_when_collapsed_quote_matches_twice():
    text = "alpha  beta\n\nalpha beta"
    fact = Fact(fact_id="x", doc_id="d", supplier="s", tier=1, source_file="d.txt", source_quote="alpha beta")
    accepted, _ = validate_quotes([fact], text)
    assert accepted[0].source_quote == "alpha  beta"  # the first occurrence (double space), not the second


# ---------------------------------------------------------------- R4: supplier matching

@pytest.mark.parametrize("variant", ["Delta Ginning Co.", "DELTA GINNING CO", "delta ginning co.",
                                     "Delta Ginning Co., Khulna", "Delta Ginning Co (Khulna, Bangladesh)"])
def test_harmless_supplier_variants_accepted_and_stored_as_registered(variant, cache_dir):
    result = run_tier3(FakeClient(as_text(model_item(supplier=variant))), cache_dir)
    assert result.rejected == []
    assert result.accepted[0].supplier == "Delta Ginning Co."


@pytest.mark.parametrize("other", ["Delta Ginning Holdings", "Coimbatore Textile Mills", "", "Delta"])
def test_different_company_rejected_naming_both_values(other, cache_dir):
    result = run_tier3(FakeClient(as_text(model_item(supplier=other))), cache_dir)
    assert result.accepted == []
    reason = result.rejected[0].reason
    assert f"'{other}'" in reason and "'Delta Ginning Co.'" in reason


def test_supplier_matches_rejects_non_strings():
    assert not supplier_matches(None, "Delta Ginning Co.")


# ---------------------------------------------------------------- errors

@pytest.mark.parametrize("error, expected", [
    (sdk_error(anthropic.AuthenticationError, 401), "API key was rejected (HTTP 401)"),
    (sdk_error(anthropic.RateLimitError, 429), "rate limited by the Anthropic API (HTTP 429)"),
    (sdk_error(anthropic.InternalServerError, 500), "HTTP 500, request id req_test_1"),
    (anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages")),
     "could not reach the Anthropic API"),
])
def test_api_errors_become_clear_errors_naming_the_document(error, expected, cache_dir):
    with pytest.raises(ExtractionError) as excinfo:
        run_tier3(FakeClient(error), cache_dir)
    assert "tier3_ginning_invoice.txt" in str(excinfo.value) and expected in str(excinfo.value)


def test_missing_key_error_names_document_and_builds_no_client(monkeypatch, real_make_client, cache_dir):
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    built = []
    monkeypatch.setattr(anthropic, "Anthropic", lambda *a, **k: built.append(1))
    with pytest.raises(ExtractionError, match="tier3_ginning_invoice.txt.*ANTHROPIC_API_KEY"):
        extract_facts(doc("tier3_ginning_invoice"), cache_dir=cache_dir)
    assert built == []


def test_make_client_passes_key_timeout_and_retries(monkeypatch, real_make_client):
    seen = {}
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kw: seen.update(kw) or "client")
    assert extract._make_client("x.txt") == "client"
    assert seen == {"api_key": FAKE_KEY, "timeout": extract.REQUEST_TIMEOUT_SECONDS, "max_retries": 2}


def test_secrets_and_document_text_never_leak(cache_dir, capsys, caplog):
    errors = [sdk_error(anthropic.AuthenticationError, 401), sdk_error(anthropic.RateLimitError, 429),
              sdk_error(anthropic.InternalServerError, 500)]
    messages = []
    for error in errors:
        with pytest.raises(ExtractionError) as excinfo:
            run_tier3(FakeClient(error), cache_dir)
        messages.append(str(excinfo.value))
    with pytest.raises(ExtractionError) as excinfo:
        run_tier3(FakeClient("garbage: Country of origin: Bangladesh"), cache_dir)
    messages.append(str(excinfo.value))
    run_tier3(FakeClient(as_text(model_item())), cache_dir)

    captured = capsys.readouterr()
    haystack = "\n".join(messages) + captured.out + captured.err + caplog.text
    assert FAKE_KEY not in haystack
    for line in doc_text("tier3_ginning_invoice").splitlines():
        if line.strip():
            assert line not in haystack


# ---------------------------------------------------------------- cache

def test_cache_second_call_makes_no_api_call(cache_dir):
    first_client = FakeClient(as_text(model_item()))
    first = run_tier3(first_client, cache_dir)
    second_client = FakeClient(RuntimeError("must not be called"))
    second = run_tier3(second_client, cache_dir)
    assert len(first_client.calls) == 1 and second_client.calls == []
    assert (first.cached, second.cached) == (False, True)
    assert second.accepted == first.accepted and second.model == MODEL


def test_cache_misses_on_changed_document_prompt_or_model(cache_dir, monkeypatch, tmp_path):
    run_tier3(FakeClient(as_text(model_item())), cache_dir)

    client = FakeClient(as_text(model_item()))
    with monkeypatch.context() as m:
        m.setattr(extract, "PROMPT_VERSION", "extract-v2")
        assert run_tier3(client, cache_dir).cached is False and len(client.calls) == 1

    client = FakeClient(as_text(model_item()))
    with monkeypatch.context() as m:
        m.setattr(extract, "MODEL", "claude-other-model")
        assert run_tier3(client, cache_dir).cached is False and len(client.calls) == 1

    changed = tmp_path / "tier3_ginning_invoice.txt"  # same stem, different bytes
    changed.write_bytes(doc("tier3_ginning_invoice").read_bytes() + b"\nExtra line\n")
    client = FakeClient(as_text(model_item()))
    result = extract_facts(changed, context=doc_context(doc("tier3_ginning_invoice")), client=client,
                           cache_dir=cache_dir)
    assert result.cached is False and len(client.calls) == 1


def test_cache_off_always_calls(cache_dir, monkeypatch):
    monkeypatch.setenv("EXTRACTION_CACHE", "off")
    client = FakeClient(as_text(model_item()))
    run_tier3(client, cache_dir)
    run_tier3(client, cache_dir)
    assert len(client.calls) == 2 and not cache_dir.exists()


def test_invalid_output_is_never_cached(cache_dir):
    with pytest.raises(ExtractionError):
        run_tier3(FakeClient("garbage"), cache_dir)
    assert not cache_dir.exists() or list(cache_dir.iterdir()) == []


def test_corrupt_cache_file_ignored_and_replaced(cache_dir):
    run_tier3(FakeClient(as_text(model_item())), cache_dir)
    [path] = cache_dir.iterdir()
    path.write_text("{ not json", encoding="utf-8")
    client = FakeClient(as_text(model_item()))
    assert run_tier3(client, cache_dir).cached is False and len(client.calls) == 1
    assert json.loads(path.read_text(encoding="utf-8"))["raw_text"] == as_text(model_item())


def test_cache_file_holds_only_model_json_and_metadata_never_the_key(cache_dir):
    text = as_text(model_item())
    run_tier3(FakeClient(text), cache_dir)
    [path] = cache_dir.iterdir()
    entry = json.loads(path.read_text(encoding="utf-8"))
    assert set(entry) == {"doc_id", "document_sha256", "prompt_version", "model", "created_at", "raw_text"}
    assert entry["raw_text"] == text
    assert (entry["prompt_version"], entry["model"]) == (PROMPT_VERSION, MODEL)
    assert FAKE_KEY.encode() not in path.read_bytes()
    assert "tier3_ginning_invoice" in path.name and PROMPT_VERSION in path.name


def test_validation_reruns_on_cached_text(cache_dir, monkeypatch):
    run_tier3(FakeClient(as_text(model_item(source_quote="Country of origin: Narnia"))), cache_dir)
    result = run_tier3(FakeClient(RuntimeError("no call")), cache_dir)
    assert result.cached and result.accepted == [] and "not found verbatim" in result.rejected[0].reason


# ---------------------------------------------------------------- doc_context

@pytest.mark.parametrize("stem", ALL_DOCS)
def test_doc_context_finds_tier_and_supplier(stem):
    products = json.loads((DATA / "products.json").read_text(encoding="utf-8"))
    ctx = doc_context(doc(stem))
    expected_tier = next(d["tier"] for p in products.values() for d in p["documents"] if d["doc_id"] == stem)
    assert ctx.doc_id == stem and ctx.tier == expected_tier
    assert ctx.registered_supplier == next(
        s["supplier"] for s in products[ctx.product_id]["supply_chain"] if s["tier"] == expected_tier)


def test_doc_context_unknown_file_raises(tmp_path):
    with pytest.raises(ExtractionError, match="mystery.txt"):
        doc_context(tmp_path / "mystery.txt")


def test_doc_context_conflicting_products_raise(tmp_path):
    products = {
        "a": {"name": "A", "documents": [{"doc_id": "d", "tier": 1, "file": "d.txt"}],
              "supply_chain": [{"tier": 1, "supplier": "One"}]},
        "b": {"name": "B", "documents": [{"doc_id": "d", "tier": 1, "file": "d.txt"}],
              "supply_chain": [{"tier": 1, "supplier": "Two"}]},
    }
    path = tmp_path / "products.json"
    path.write_text(json.dumps(products), encoding="utf-8")
    with pytest.raises(ExtractionError, match="disagree"):
        doc_context(tmp_path / "d.txt", products_path=path)


def test_context_for_another_document_is_refused(cache_dir):
    ctx = doc_context(doc("tshirt_bom"))
    with pytest.raises(ExtractionError, match="tier3_ginning_invoice.txt"):
        run_tier3(FakeClient(as_text(model_item())), cache_dir, context=ctx)


# ---------------------------------------------------------------- pipeline

@pytest.fixture
def env(tmp_path):
    shutil.copytree(DATA / "documents", tmp_path / "docs")
    shutil.copytree(DATA / "mock_extractions", tmp_path / "mock")
    shutil.copy(DATA / "products.json", tmp_path / "products.json")
    return SimpleNamespace(
        path=tmp_path, log=tmp_path / "audit_log.jsonl", docs=tmp_path / "docs", mock=tmp_path / "mock",
        products=tmp_path / "products.json", runs=tmp_path / "runs")


def run(env, product_id="tshirt-001"):
    return pipeline.run_product(product_id, log_path=env.log, docs_dir=env.docs, mock_dir=env.mock,
                                products_path=env.products, runs_dir=env.runs, today=TODAY, now=NOW)


def log_entries(env):
    return [json.loads(line) for line in env.log.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_pipeline_real_run_records_model_on_every_event(env, monkeypatch):
    monkeypatch.setattr(extract, "_make_client", lambda *a, **k: MockBackedClient())
    result = run(env)
    assert result.claims[0].verdict.verdict == "Contradicted"
    assert [c.result for c in result.claims[0].checks] == ["fail", "fail"]
    entries = log_entries(env)
    assert {e["model"] for e in entries} == {"claude-sonnet-5-5"}
    extracted = [e for e in entries if e["event_type"] == "facts_extracted"]
    assert len(extracted) == 4
    assert all(e["details"]["cached"] is False and e["details"]["prompt_version"] == PROMPT_VERSION for e in extracted)
    assert result.documents_without_facts == [] and result.failed_documents == []


def test_pipeline_api_error_lands_in_failed_documents_and_run_completes(env, monkeypatch):
    error = sdk_error(anthropic.InternalServerError, 500)
    monkeypatch.setattr(extract, "_make_client",
                        lambda *a, **k: MockBackedClient({"tier3_ginning_invoice.txt": error}))
    result = run(env)
    [failed] = result.failed_documents
    assert failed.doc_id == "tier3_ginning_invoice" and "HTTP 500" in failed.reason
    assert "tier3_ginning_invoice.txt" in failed.reason
    assert len(result.facts) == 4  # the other three documents still contributed (bom 2, tier1 1, tier2 1)


def test_pipeline_mock_mode_log_unchanged(env, monkeypatch):
    monkeypatch.setenv("MOCK_MODE", "true")
    result = run(env)
    entries = log_entries(env)
    assert {e["model"] for e in entries} == {"mock"}
    for e in entries:
        if e["event_type"] == "facts_extracted":
            assert set(e["details"]) == {"doc_id", "accepted", "rejected", "fact_ids"}
    assert result.documents_without_facts == []


def test_pipeline_always_passes_context_and_never_uses_default_products(env, monkeypatch):
    """A product id absent from the default products.json: the default lookup would fail, so context must be passed."""
    products = json.loads(env.products.read_text(encoding="utf-8"))
    products["renamed-tee"] = {**products.pop("tshirt-001"), "product_id": "renamed-tee"}
    env.products.write_text(json.dumps(products), encoding="utf-8")

    def boom(*a, **k):
        raise AssertionError("doc_context (default products path) must not be used by the pipeline")

    monkeypatch.setattr(extract, "doc_context", boom)
    monkeypatch.setattr(extract, "DEFAULT_PRODUCTS_PATH", env.path / "does-not-exist.json")
    monkeypatch.setattr(extract, "_make_client", lambda *a, **k: MockBackedClient())
    contexts = []
    real = pipeline.extract_facts

    def spy(path, **kwargs):
        contexts.append(kwargs.get("context"))
        return real(path, **kwargs)

    monkeypatch.setattr(pipeline, "extract_facts", spy)
    result = run(env, "renamed-tee")
    assert result.failed_documents == [] and len(result.facts) == 5
    assert len(contexts) == 4 and all(isinstance(c, DocContext) for c in contexts)
    assert {c.product_id for c in contexts} == {"renamed-tee"}


def test_documents_without_facts_reports_empty_answer_and_all_rejected(env, monkeypatch):
    overrides = {
        "tier3_ginning_invoice.txt": as_text(),  # valid, but no facts
        "tier2_gots_certificate.txt": as_text(model_item(tier=1), model_item(source_quote="made up")),  # all rejected
    }
    monkeypatch.setattr(extract, "_make_client", lambda *a, **k: MockBackedClient(overrides))
    result = run(env)
    found = {d.doc_id: d for d in result.documents_without_facts}
    assert set(found) == {"tier3_ginning_invoice", "tier2_gots_certificate"}
    assert found["tier3_ginning_invoice"].rejected == 0 and found["tier3_ginning_invoice"].file == "tier3_ginning_invoice.txt"
    assert found["tier2_gots_certificate"].rejected == 2
    assert result.failed_documents == []  # read successfully, so not a failure
    assert len(result.rejected) == 2


def test_regulator_api_returns_documents_without_facts(env, monkeypatch):
    monkeypatch.setenv("MOCK_MODE", "true")
    settings = Settings(log_path=env.log, docs_dir=env.docs, mock_dir=env.mock, products_path=env.products,
                        runs_dir=env.runs, session_secret="test-secret")
    client = TestClient(create_app(settings))
    assert client.post("/api/retailer/login", json={"username": "retailer", "password": "demo-retailer"}).status_code == 200
    assert client.post("/api/run/tshirt-001").status_code == 200
    assert client.post("/api/regulator/login", json={"username": "regulator", "password": "demo-regulator"}).status_code == 200
    body = client.get("/api/regulator/tshirt-001").json()
    assert body["documents_without_facts"] == []
