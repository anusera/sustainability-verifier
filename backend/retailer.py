"""Retailer overview (SPEC 12): verdict badges, summary counts and tree status. Pure code, no LLM.

Retailer data may include supplier names and verdict reasons. Never reuse it for the public view.
"""
from .pipeline import RunResult, SupplierNode

NOT_VERIFIED_LABEL = "Not yet verified"

# Fixed mapping: colour is always paired with the text label.
BADGE = {
    "Verified": "green",
    "Self-declared only": "amber",
    "Missing evidence": "amber",
    "Contradicted": "red",
}
COUNT_KEY = {
    "Verified": "verified",
    "Contradicted": "contradicted",
    "Self-declared only": "needs_evidence",
    "Missing evidence": "needs_evidence",
}


def _contradicted_tiers(run: RunResult) -> set[int]:
    """Tiers holding a fact cited by any failed check."""
    tier_of = {f.fact_id: f.tier for f in run.facts}
    return {tier_of[fid] for c in run.claims for ck in c.checks if ck.result == "fail"
            for fid in ck.evidence if fid in tier_of}


def _tree(node: SupplierNode, bad_tiers: set[int]) -> dict:
    if node.status == "no_facts":
        status = "no_facts"
    elif node.tier in bad_tiers:
        status = "contradicted"
    else:
        status = "ok"
    return {"tier": node.tier, "supplier": node.supplier, "status": status,
            "fact_count": node.fact_count, "children": [_tree(c, bad_tiers) for c in node.children]}


def build_retailer_overview(products: dict, snapshots: dict[str, RunResult | None]) -> dict:
    counts = {"verified": 0, "contradicted": 0, "needs_evidence": 0, "not_yet_verified": 0}
    out = []
    for pid, product in products.items():
        run = snapshots.get(pid)
        claims = []
        if run is None:
            for c in product["claims"]:
                counts["not_yet_verified"] += 1
                claims.append({"claim_id": c["claim_id"], "claim_text": c["text"], "verdict": None,
                               "badge": "none", "label": NOT_VERIFIED_LABEL, "reason": None, "checks": []})
        else:
            for c in run.claims:
                v = c.verdict.verdict
                counts[COUNT_KEY[v]] += 1
                claims.append({
                    "claim_id": c.claim_id, "claim_text": c.claim_text, "verdict": v,
                    "badge": BADGE[v], "label": v, "reason": c.verdict.reason,
                    "checks": [{"check_id": k.check_id, "result": k.result, "detail": k.detail}
                               for k in c.checks]})
        out.append({
            "product_id": pid, "name": product["name"], "verified": run is not None,
            "run_at": run.run_at if run else None, "claims": claims,
            "tree": _tree(run.tree, _contradicted_tiers(run)) if run else None,
        })
    return {"summary": {**counts, "total_claims": sum(counts.values())}, "products": out}
