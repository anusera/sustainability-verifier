"""Pipeline: ingest, extract, rules, verdict, audit log (SPEC 5, 9, 10).

All paths are injectable so tests can run entirely inside tmp_path.
"""
import hashlib
import json
from datetime import date, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from . import audit
from .audit import DEFAULT_DOCS_DIR, DEFAULT_LOG_PATH, read_entries, sha256_file
from .extract import (
    DEFAULT_MOCK_DIR,
    PROMPT_VERSION,
    DocContext,
    ExtractionError,
    ExtractionResult,
    RejectedFact,
    active_model,
    extract_facts,
)
from .models import Check, Fact, Verdict
from .reason import explain_verdict
from .rules import claim_type, run_rules
from .timeutil import utc_now_iso
from .verdict import decide_verdict

_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PRODUCTS_PATH = _ROOT / "data" / "products.json"
DEFAULT_RUNS_DIR = _ROOT / "data" / "runs"


class UnknownProduct(KeyError):
    """The product id is not in products.json."""

    def __str__(self) -> str:
        return f"Unknown product '{self.args[0]}'"


class FailedDocument(BaseModel):
    doc_id: str
    file: str
    reason: str


class DocumentWithoutFacts(BaseModel):
    """A document that was read but yielded no accepted facts (empty answer, or every fact rejected)."""
    doc_id: str
    file: str
    rejected: int


class SupplierNode(BaseModel):
    tier: int
    supplier: str
    doc_ids: list[str]
    fact_count: int
    status: Literal["has_facts", "no_facts"]
    children: list["SupplierNode"] = []


class ClaimResult(BaseModel):
    claim_id: str
    claim_text: str
    checks: list[Check]
    verdict: Verdict
    verdict_changed: bool


class RunResult(BaseModel):
    product_id: str
    product_name: str
    run_at: str
    facts: list[Fact]
    rejected: list[RejectedFact]
    failed_documents: list[FailedDocument]
    documents_without_facts: list[DocumentWithoutFacts] = []
    claims: list[ClaimResult]
    tree: SupplierNode


def load_products(path: Path = DEFAULT_PRODUCTS_PATH) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def get_product(product_id: str, *, products_path: Path = DEFAULT_PRODUCTS_PATH) -> dict:
    products = load_products(products_path)
    if product_id not in products:
        raise UnknownProduct(product_id)
    return products[product_id]


def build_tree(product: dict, facts: list[Fact]) -> SupplierNode:
    """Chain tier 1 > tier 2 > tier 3. products.json has no parent links, so parent = previous tier."""
    nodes: list[SupplierNode] = []
    for s in sorted(product["supply_chain"], key=lambda s: s["tier"]):
        count = sum(1 for f in facts if f.tier == s["tier"])
        nodes.append(SupplierNode(
            tier=s["tier"],
            supplier=s["supplier"],
            doc_ids=[d["doc_id"] for d in product["documents"] if d["tier"] == s["tier"]],
            fact_count=count,
            status="has_facts" if count else "no_facts",
        ))
    for parent, child in zip(nodes, nodes[1:]):
        parent.children.append(child)
    return nodes[0]


def previous_verdict_label(product_id: str, claim_id: str, *, log_path: Path) -> str | None:
    """Label from the last verdict_changed entry for this claim. The audit log is the source of truth."""
    label = None
    for e in read_entries(log_path):
        d = e.get("details") or {}
        if e["product_id"] == product_id and e["event_type"] == "verdict_changed" and d.get("claim_id") == claim_id:
            label = d.get("verdict")
    return label


def _last_logged_hash(product_id: str, file_name: str, *, log_path: Path) -> str | None:
    h = None
    for e in read_entries(log_path):
        if (e["product_id"] == product_id and e["event_type"] == "document_ingested"
                and (e.get("details") or {}).get("file_name") == file_name):
            h = e.get("document_sha256")
    return h


def save_snapshot(result: RunResult, runs_dir: Path) -> None:
    runs_dir = Path(runs_dir)
    runs_dir.mkdir(parents=True, exist_ok=True)
    (runs_dir / f"{result.product_id}.json").write_text(result.model_dump_json(indent=2), encoding="utf-8")


def load_snapshot(product_id: str, runs_dir: Path) -> RunResult | None:
    p = Path(runs_dir) / f"{product_id}.json"
    if not p.is_file():
        return None
    return RunResult.model_validate_json(p.read_text(encoding="utf-8"))


def run_product(
    product_id: str,
    *,
    log_path: Path = DEFAULT_LOG_PATH,
    docs_dir: Path = DEFAULT_DOCS_DIR,
    mock_dir: Path = DEFAULT_MOCK_DIR,
    products_path: Path = DEFAULT_PRODUCTS_PATH,
    runs_dir: Path = DEFAULT_RUNS_DIR,
    today: date | None = None,
    now: datetime | None = None,
) -> RunResult:
    product = get_product(product_id, products_path=products_path)
    docs_dir = Path(docs_dir)

    facts: list[Fact] = []
    rejected: list[RejectedFact] = []
    failed: list[FailedDocument] = []
    without_facts: list[DocumentWithoutFacts] = []
    run_model = active_model()  # "mock", or the extraction model in effect for this run

    for doc in product["documents"]:
        path = docs_dir / doc["file"]
        if not path.is_file():
            failed.append(FailedDocument(doc_id=doc["doc_id"], file=doc["file"], reason="document file not found"))
            continue
        if sha256_file(path) != _last_logged_hash(product_id, path.name, log_path=log_path):
            audit.log_document_ingested(product_id, path, log_path=log_path, model=run_model)
        # Always pass the context: real extraction must not fall back to the default products.json.
        context = DocContext(product_id=product_id, product_name=product["name"], doc_id=doc["doc_id"],
                             tier=doc["tier"], supply_chain=product["supply_chain"])
        try:
            result: ExtractionResult = extract_facts(path, mock_dir=mock_dir, context=context)
        except (FileNotFoundError, ValueError, UnicodeDecodeError, ExtractionError) as exc:
            failed.append(FailedDocument(doc_id=doc["doc_id"], file=doc["file"], reason=str(exc)))
            continue
        facts += result.accepted
        rejected += result.rejected
        if not result.accepted:
            without_facts.append(DocumentWithoutFacts(
                doc_id=doc["doc_id"], file=doc["file"], rejected=len(result.rejected)))
        details = {
            "doc_id": result.doc_id,
            "accepted": len(result.accepted),
            "rejected": len(result.rejected),
            "fact_ids": [f.fact_id for f in result.accepted],
        }
        if result.model != "mock":  # real runs only: mock log entries stay byte-identical
            details["cached"] = result.cached
            details["prompt_version"] = PROMPT_VERSION
        audit.append({
            "product_id": product_id,
            "event_type": "facts_extracted",
            "details": details,
            "model": result.model,
        }, log_path=log_path)

    claim_results: list[ClaimResult] = []
    for claim in product["claims"]:
        ctype = claim_type(claim)
        checks = run_rules(claim, facts, today, product["supply_chain"])
        audit.append({
            "product_id": product_id,
            "event_type": "rules_run",
            "details": {"claim_id": claim["claim_id"], "checks": [f"{c.check_id}: {c.result}" for c in checks]},
            "model": run_model,
        }, log_path=log_path)
        verdict = decide_verdict(product_id, claim["claim_id"], checks, facts, product["supply_chain"], now,
                                  claim_type=ctype)
        info = explain_verdict(verdict, claim, checks, facts)
        verdict = verdict.model_copy(update={"reason": info.reason, "reason_source": info.source,
                                             "cited_fact_ids": info.cited_fact_ids})
        if run_model != "mock":  # real runs only: mock log entries stay byte-identical
            audit.append({
                "product_id": product_id,
                "event_type": "reason_generated",
                "details": {"claim_id": claim["claim_id"], "source": info.source, "model": info.model,
                            "cached": info.cached, "prompt_version": info.prompt_version,
                            "cited_fact_ids": info.cited_fact_ids, "rejected_reason": info.rejected_reason,
                            "reason_sha256": hashlib.sha256(info.reason.encode("utf-8")).hexdigest()},
                "model": run_model,
            }, log_path=log_path)
        previous = previous_verdict_label(product_id, claim["claim_id"], log_path=log_path)
        changed = previous != verdict.verdict
        if changed:
            audit.append({
                "product_id": product_id,
                "event_type": "verdict_changed",
                "details": {"claim_id": claim["claim_id"], "previous": previous,
                            "verdict": verdict.verdict, "reason": verdict.reason},
                "model": run_model,
            }, log_path=log_path)
        claim_results.append(ClaimResult(claim_id=claim["claim_id"], claim_text=claim["text"],
                                         checks=checks, verdict=verdict, verdict_changed=changed))

    run = RunResult(
        product_id=product_id,
        product_name=product["name"],
        run_at=utc_now_iso(now),
        facts=facts,
        rejected=rejected,
        failed_documents=failed,
        documents_without_facts=without_facts,
        claims=claim_results,
        tree=build_tree(product, facts),
    )
    save_snapshot(run, runs_dir)
    return run
