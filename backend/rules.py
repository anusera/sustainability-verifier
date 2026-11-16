"""Deterministic rules (rules-v1.0). Code does the checks; the LLM is not involved."""
import re
from datetime import date

from .models import RULES_VERSION, Check, Fact

# Checks whose failure means the claim is contradicted (SPEC section 9, step 1).
# vague_claim and tier_coverage are deliberately absent: they never prove a claim false.
CONTRADICTION_CHECKS = {"origin_match", "recycled_content_match"}

# Unqualified wording that cannot be proven as written. Regexes, matched case-insensitively on word boundaries.
VAGUE_TERMS = [
    "eco-friendly",
    "eco friendly",
    "environmentally friendly",
    "sustainable",
    "natural",
    r"up to \d+%",
]


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


def _check_pct(value: float, label: str) -> None:
    if not 0 <= value <= 100:
        raise ValueError(f"{label} {value} is outside 0 to 100")


def recycled_content_match(claimed_pct: float, facts: list[Fact]) -> Check:
    """Fail if any fact states less recycled content than claimed. Exact comparison, no tolerance."""
    _check_pct(claimed_pct, "claimed recycled percentage")
    with_pct = [f for f in facts if f.recycled_content_pct is not None]
    for f in with_pct:
        _check_pct(f.recycled_content_pct, f"recycled_content_pct of {f.fact_id}:")
    if not with_pct:
        return Check(check_id="recycled_content_match", result="pass",
                     detail="No fact states recycled content", evidence=[], rules_version=RULES_VERSION)
    lower = sorted((f for f in with_pct if f.recycled_content_pct < claimed_pct),
                   key=lambda f: f.recycled_content_pct)
    rest = [f for f in with_pct if f.recycled_content_pct >= claimed_pct]
    if lower:
        txt = "; ".join(f"{f.fact_id} (tier {f.tier}, {f.supplier}) states {f.recycled_content_pct:g}%" for f in lower)
        detail = f"Claimed {claimed_pct:g}% recycled content; {txt}."
        if rest:
            detail += " Meeting the claim: " + ", ".join(f.fact_id for f in rest) + "."
        result = "fail"
    else:
        detail = (f"All recycled-content facts meet claimed {claimed_pct:g}%: "
                  + ", ".join(f.fact_id for f in rest) + ".")
        result = "pass"
    return Check(check_id="recycled_content_match", result=result, detail=detail,
                 evidence=[f.fact_id for f in lower + rest], rules_version=RULES_VERSION)


def empty_tiers(supply_chain: list[dict], facts: list[Fact]) -> list[dict]:
    covered = {f.tier for f in facts}
    return [s for s in supply_chain if s["tier"] not in covered]


def tier_coverage(supply_chain: list[dict], facts: list[Fact]) -> Check:
    """Every listed supplier tier needs at least one fact. 'No facts', not 'no documents': a document may yield none."""
    missing = empty_tiers(supply_chain, facts)
    if missing:
        detail = "; ".join(f"Tier {s['tier']} ({s['supplier']}): no facts" for s in missing) + "."
        return Check(check_id="tier_coverage", result="fail", detail=detail, evidence=[],
                     rules_version=RULES_VERSION)
    tiers = ", ".join(str(s["tier"]) for s in supply_chain)
    return Check(check_id="tier_coverage", result="pass",
                 detail=f"Every supplier tier has at least one fact (tiers: {tiers})." if tiers
                 else "No supplier tiers to cover.",
                 evidence=[], rules_version=RULES_VERSION)


def vague_claim(claim_text: str, terms: list[str] = VAGUE_TERMS) -> Check:
    """Fail on unqualified wording. Terms are regexes matched case-insensitively on word boundaries."""
    for term in terms:
        m = re.search(rf"(?<!\w)(?:{term})(?!\w)", claim_text, flags=re.IGNORECASE)
        if m:
            return Check(check_id="vague_claim", result="fail",
                         detail=f"Claim wording is unqualified: '{m.group(0)}'.", evidence=[],
                         rules_version=RULES_VERSION)
    return Check(check_id="vague_claim", result="pass", detail="No unqualified terms found in the claim.",
                 evidence=[], rules_version=RULES_VERSION)


def claim_type(claim: dict) -> str:
    """Claims without a type are origin claims (slice-1 shape)."""
    return claim.get("type", "origin")


def _origin_rules(claim, facts, today, supply_chain) -> list[Check]:
    return [origin_match(claim["claimed_origin"], facts), cert_validity(facts, today)]


def _packaging_rules(claim, facts, today, supply_chain) -> list[Check]:
    if "claimed_recycled_pct" not in claim:
        raise ValueError(f"Packaging claim '{claim.get('claim_id')}' needs claimed_recycled_pct")
    return [
        recycled_content_match(claim["claimed_recycled_pct"], facts),
        cert_validity(facts, today),
        tier_coverage(supply_chain or [], facts),
        vague_claim(claim["text"]),
    ]


def _packaging_wording_rules(claim, facts, today, supply_chain) -> list[Check]:
    """Packaging claims with no number to compare: no recycled_content_match."""
    return [
        cert_validity(facts, today),
        tier_coverage(supply_chain or [], facts),
        vague_claim(claim["text"]),
    ]


RULESETS = {"origin": _origin_rules, "packaging": _packaging_rules,
            "packaging_wording": _packaging_wording_rules}


def run_rules(claim: dict, facts: list[Fact], today: date | None = None,
              supply_chain: list[dict] | None = None) -> list[Check]:
    ctype = claim_type(claim)
    if ctype not in RULESETS:
        raise ValueError(f"Unknown claim type '{ctype}'")
    return RULESETS[ctype](claim, facts, today, supply_chain)
