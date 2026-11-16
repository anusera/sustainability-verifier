"""Deterministic rules (rules-v1.0). Code does the checks; the LLM is not involved."""
from datetime import date

from .models import RULES_VERSION, Check, Fact

# Checks whose failure means the claim is contradicted (SPEC section 9, step 1).
CONTRADICTION_CHECKS = {"origin_match"}


def _norm(s: str) -> str:
    return s.strip().casefold()


def origin_match(claimed_origin: str, facts: list[Fact]) -> Check:
    with_origin = [f for f in facts if f.origin_country]
    if not with_origin:
        return Check(
            check_id="origin_match",
            result="pass",
            detail="No fact states an origin country",
            evidence=[],
            rules_version=RULES_VERSION,
        )
    claimed = _norm(claimed_origin)
    conflicts = [f for f in with_origin if _norm(f.origin_country) != claimed]
    matches = [f for f in with_origin if _norm(f.origin_country) == claimed]
    if conflicts:
        conflict_txt = "; ".join(
            f"{f.fact_id} (tier {f.tier}, {f.supplier}) states {f.origin_country}" for f in conflicts
        )
        detail = f"Claimed {claimed_origin}; {conflict_txt}."
        if matches:
            detail += " Matching: " + ", ".join(f.fact_id for f in matches) + "."
        result = "fail"
    else:
        detail = f"All origin facts match claimed {claimed_origin}: " + ", ".join(f.fact_id for f in matches) + "."
        result = "pass"
    return Check(
        check_id="origin_match",
        result=result,
        detail=detail,
        evidence=[f.fact_id for f in conflicts] + [f.fact_id for f in matches],
        rules_version=RULES_VERSION,
    )


def _cert_failures(cert: Fact, facts: list[Fact], today: date) -> tuple[list[str], list[str]]:
    """Return (problem descriptions, ids of other facts whose dates beat the certificate)."""
    valid_to = date.fromisoformat(cert.valid_to)
    problems: list[str] = []
    beaten_by: list[str] = []
    for other in facts:
        if other.doc_id == cert.doc_id or not other.document_date:
            continue
        if valid_to < date.fromisoformat(other.document_date):
            problems.append(f"earlier than document_date {other.document_date} of {other.fact_id} ({other.source_file})")
            beaten_by.append(other.fact_id)
    if valid_to < today:
        problems.append(f"earlier than today {today.isoformat()}")
    return problems, beaten_by


def cert_validity(facts: list[Fact], today: date | None = None) -> Check:
    today = today or date.today()
    certs = [f for f in facts if f.valid_to]
    if not certs:
        return Check(check_id="cert_validity", result="pass", detail="No certificates found.",
                     evidence=[], rules_version=RULES_VERSION)
    parts: list[str] = []
    failed_ids: list[str] = []
    other_ids: list[str] = []
    for cert in certs:
        problems, beaten_by = _cert_failures(cert, facts, today)
        if problems:
            label = cert.certificate_id or cert.fact_id
            parts.append(f"{label} ({cert.fact_id}) valid_to {cert.valid_to} is " + "; ".join(problems))
            failed_ids.append(cert.fact_id)
            other_ids += [i for i in beaten_by if i not in other_ids]
    if not parts:
        return Check(
            check_id="cert_validity", result="pass",
            detail="All certificates are valid on every compared date: "
            + ", ".join(f"{c.fact_id} (valid_to {c.valid_to})" for c in certs) + ".",
            evidence=[c.fact_id for c in certs], rules_version=RULES_VERSION)
    return Check(check_id="cert_validity", result="fail", detail=". ".join(parts) + ".",
                 evidence=failed_ids + other_ids, rules_version=RULES_VERSION)


def run_rules(claim: dict, facts: list[Fact], today: date | None = None) -> list[Check]:
    return [origin_match(claim["claimed_origin"], facts), cert_validity(facts, today)]
