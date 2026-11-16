"""Combine checks into one verdict (SPEC section 9). Evaluated in order, first match wins."""
from datetime import datetime, timezone

from .models import RULES_VERSION, Check, Fact, Verdict
from .rules import CONTRADICTION_CHECKS
from .timeutil import utc_now_iso


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def decide_verdict(
    product_id: str,
    claim_id: str,
    checks: list[Check],
    facts: list[Fact],
    supply_chain: list[dict],
    now: datetime | None = None,
) -> Verdict:
    now = now or datetime.now(timezone.utc)
    failed = [c for c in checks if c.result == "fail"]
    contradictions = [c for c in failed if c.check_id in CONTRADICTION_CHECKS]
    tiers_with_facts = {f.tier for f in facts}
    empty_tiers = [s["tier"] for s in supply_chain if s["tier"] not in tiers_with_facts]
    has_origin_fact = any(f.origin_country for f in facts)
    has_third_party = any(f.certificate_id for f in facts)

    if contradictions:
        verdict = "Contradicted"
        reason = "Evidence contradicts the claim: " + " ".join(c.detail for c in contradictions)
        others = [c for c in failed if c not in contradictions]
        if others:
            reason += " Also failed: " + " ".join(c.detail for c in others)
    elif empty_tiers or not has_origin_fact:
        verdict = "Missing evidence"
        bits = []
        if empty_tiers:
            bits.append("no facts found for tier " + ", ".join(str(t) for t in empty_tiers))
        if not has_origin_fact:
            bits.append("no fact states an origin country")
        reason = "Missing evidence: " + "; ".join(bits) + "."
    elif not has_third_party or failed:
        verdict = "Self-declared only"
        if not has_third_party:
            reason = "Only supplier statements were found; no third-party certificate."
        else:
            reason = "Third-party backing is not valid: " + " ".join(c.detail for c in failed)
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
