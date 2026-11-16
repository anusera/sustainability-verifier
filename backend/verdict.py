"""Combine checks into one verdict (SPEC section 9). Evaluated in order, first match wins."""
from datetime import datetime, timezone

from .models import RULES_VERSION, Check, Fact, Verdict
from .rules import CONTRADICTION_CHECKS, empty_tiers
from .timeutil import utc_now_iso


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


# Per claim type: the fact field that must be stated somewhere, and how to word its absence.
REQUIRED_FACT = {
    "origin": (lambda f: bool(f.origin_country), "no fact states an origin country"),
    "packaging": (lambda f: f.recycled_content_pct is not None, "no fact states recycled content"),
    "packaging_wording": (lambda f: True, "no facts found"),
}

# Failed checks that cap the verdict at Self-declared only ("Not yet proven") rather than contradict.
VAGUE_CHECKS = {"vague_claim"}


def decide_verdict(
    product_id: str,
    claim_id: str,
    checks: list[Check],
    facts: list[Fact],
    supply_chain: list[dict],
    now: datetime | None = None,
    claim_type: str = "origin",
) -> Verdict:
    now = now or datetime.now(timezone.utc)
    failed = [c for c in checks if c.result == "fail"]
    contradictions = [c for c in failed if c.check_id in CONTRADICTION_CHECKS]
    has_required_fact = any(REQUIRED_FACT[claim_type][0](f) for f in facts)
    has_third_party = any(f.certificate_id for f in facts)

    # Tier coverage: the explicit check is the single source. Rule sets without it (origin, until the
    # v1.1 bump) fall back to computing the same thing here.
    tier_check = next((c for c in checks if c.check_id == "tier_coverage"), None)
    if tier_check is not None:
        missing_tier_text = tier_check.detail if tier_check.result == "fail" else None
    else:
        gaps = empty_tiers(supply_chain, facts)
        missing_tier_text = ("no facts found for tier " + ", ".join(str(s["tier"]) for s in gaps) + ".") if gaps else None
    # tier_coverage failures are reported through missing_tier_text, never twice
    failed = [c for c in failed if c.check_id != "tier_coverage"]
    contradictions = [c for c in contradictions if c.check_id != "tier_coverage"]
    vague = [c for c in failed if c.check_id in VAGUE_CHECKS]

    if contradictions:
        verdict = "Contradicted"
        reason = "Evidence contradicts the claim: " + " ".join(c.detail for c in contradictions)
        others = [c for c in failed if c not in contradictions]
        if others:
            reason += " Also failed: " + " ".join(c.detail for c in others)
        if missing_tier_text:
            reason += " Also missing evidence: " + missing_tier_text
    elif missing_tier_text or not has_required_fact:
        verdict = "Missing evidence"
        bits = []
        if missing_tier_text:
            bits.append(missing_tier_text.rstrip("."))
        if not has_required_fact:
            bits.append(REQUIRED_FACT[claim_type][1])
        reason = "Missing evidence: " + "; ".join(bits) + "."
    elif not has_third_party or failed:
        verdict = "Self-declared only"
        other_failed = [c for c in failed if c not in vague]
        parts = []
        if not has_third_party:
            parts.append("Only supplier statements were found; no third-party certificate.")
        elif other_failed:
            parts.append("Third-party backing is not valid: " + " ".join(c.detail for c in other_failed))
        if vague:
            parts.append(" ".join(c.detail for c in vague) + " Not yet proven.")
        reason = " ".join(parts)
    else:
        verdict = "Verified"
        reason = "All checks passed and a valid third-party certificate is present."

    return Verdict(
        product_id=product_id,
        claim_id=claim_id,
        verdict=verdict,
        reason=reason,
        checks=[f"{c.check_id}: {c.result}" for c in checks],
        evidence=_dedupe([e for c in checks for e in c.evidence]),
        reviewed_by_human=False,
        generated_at=utc_now_iso(now),
        rules_version=RULES_VERSION,
    )
