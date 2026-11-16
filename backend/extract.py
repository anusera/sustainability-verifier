"""Fact extraction (SPEC 5.1, 13).

MOCK_MODE (default true) reads hand-written JSON from data/mock_extractions/<doc stem>.json.
A real Claude call will replace the mock behind the SAME extract_facts signature (increment 1).
Every fact's source_quote is validated verbatim against the document text, in mock mode too.
"""
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, ValidationError

from .models import Fact

load_dotenv()  # does not override variables already set in the environment

_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MOCK_DIR = _ROOT / "data" / "mock_extractions"

_FALSE_VALUES = {"false", "0", "no", "off"}


class RejectedFact(BaseModel):
    fact: Fact
    reason: str


class ExtractionResult(BaseModel):
    doc_id: str
    accepted: list[Fact]
    rejected: list[RejectedFact]


class MockExtractionMissing(FileNotFoundError):
    """No mock extraction file exists for a document."""


def is_mock_mode() -> bool:
    return os.environ.get("MOCK_MODE", "true").strip().lower() not in _FALSE_VALUES


def normalise_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def validate_quotes(facts: list[Fact], doc_text: str) -> tuple[list[Fact], list[RejectedFact]]:
    """Split facts into accepted and rejected by checking each source_quote appears verbatim.

    Only line endings are normalised (on both sides). Rejected facts are reported, never dropped.
    """
    text = normalise_newlines(doc_text)
    accepted: list[Fact] = []
    rejected: list[RejectedFact] = []
    for fact in facts:
        quote = normalise_newlines(fact.source_quote)
        if not quote.strip():
            rejected.append(RejectedFact(fact=fact, reason="source_quote is empty"))
        elif quote not in text:
            rejected.append(RejectedFact(fact=fact, reason="source_quote not found verbatim in document"))
        elif fact.recycled_content_pct is not None and not 0 <= fact.recycled_content_pct <= 100:
            rejected.append(RejectedFact(
                fact=fact, reason=f"recycled_content_pct {fact.recycled_content_pct:g} is outside 0 to 100"))
        else:
            accepted.append(fact)
    return accepted, rejected


def extract_facts(doc_path: Path, *, mock_dir: Path = DEFAULT_MOCK_DIR) -> ExtractionResult:
    doc_path = Path(doc_path)
    if not is_mock_mode():
        raise NotImplementedError("real extraction arrives in increment 1")

    mock_path = Path(mock_dir) / f"{doc_path.stem}.json"
    if not mock_path.is_file():
        raise MockExtractionMissing(
            f"No mock extraction for document '{doc_path.name}': expected {mock_path}"
        )

    try:
        data = json.loads(mock_path.read_text(encoding="utf-8"))
        doc_id = data["doc_id"]
        facts = [Fact(**f) for f in data["facts"]]
    except (json.JSONDecodeError, KeyError, TypeError, ValidationError) as exc:
        raise ValueError(f"Invalid mock extraction for document '{doc_path.name}' ({mock_path}): {exc}") from exc
    if doc_id != doc_path.stem:
        raise ValueError(
            f"Mock extraction {mock_path} has doc_id '{doc_id}' but document is '{doc_path.stem}'"
        )

    doc_text = doc_path.read_bytes().decode("utf-8")
    accepted, rejected = validate_quotes(facts, doc_text)
    return ExtractionResult(doc_id=doc_id, accepted=accepted, rejected=rejected)
