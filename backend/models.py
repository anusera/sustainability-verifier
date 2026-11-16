"""Data contracts for the verifier (see CLAUDE.md)."""
from typing import Literal, Optional

from pydantic import BaseModel, Field

RULES_VERSION = "rules-v1.0"


class Fact(BaseModel):
    # required
    fact_id: str
    doc_id: str
    supplier: str
    tier: int
    source_file: str
    source_quote: str
    # optional
    material: Optional[str] = None
    origin_country: Optional[str] = None
    certification_body: Optional[str] = None
    certificate_id: Optional[str] = None
    valid_from: Optional[str] = None  # ISO date YYYY-MM-DD
    valid_to: Optional[str] = None
    document_date: Optional[str] = None
    recycled_content_pct: Optional[float] = None
    weight_kg: Optional[float] = None
    source_page: Optional[str] = None  # page number or cell reference
    confidence: Optional[float] = None


class Check(BaseModel):
    check_id: str
    result: Literal["pass", "fail"]
    detail: str
    evidence: list[str] = Field(default_factory=list)  # fact ids
    rules_version: str = RULES_VERSION


class Verdict(BaseModel):
    product_id: str
    claim_id: str
    verdict: Literal["Verified", "Self-declared only", "Contradicted", "Missing evidence"]
    reason: str
    checks: list[str]  # e.g. "origin_match: fail"
    evidence: list[str]  # fact ids
    reviewed_by_human: bool = False
    generated_at: str  # ISO timestamp
    rules_version: str = RULES_VERSION
