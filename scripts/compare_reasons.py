"""Template reason vs Claude reason, side by side, for every claim in products.json.

    python scripts/compare_reasons.py [--product ID] [--no-cache]

Calls the real API (about one call per claim; cached results cost nothing). Forces MOCK_MODE=false for this process only.
Facts come from the mock extractions, so no extraction calls are made. Writes NOTHING to the audit log or the run
snapshots; the only file written is the reason cache. Never prints the API key, the prompt input or the raw output.
"""
import argparse
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend import extract, reason  # noqa: E402
from backend.rules import claim_type, run_rules  # noqa: E402
from backend.verdict import decide_verdict  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--product", help="only this product id")
    parser.add_argument("--no-cache", action="store_true", help="ignore and do not write the reason cache")
    args = parser.parse_args(argv)

    os.environ["MOCK_MODE"] = "false"
    if args.no_cache:
        os.environ["REASON_CACHE"] = "off"

    products = json.loads((ROOT / "data" / "products.json").read_text(encoding="utf-8"))
    if args.product:
        products = {k: v for k, v in products.items() if k == args.product}
        if not products:
            print(f"No product '{args.product}' in products.json")
            return 2

    today, now = date.today(), datetime.now(timezone.utc)
    used_claude = fell_back = 0
    for pid, product in products.items():
        facts = []
        for doc in product["documents"]:
            path = ROOT / "data" / "documents" / doc["file"]
            try:
                facts += extract.extract_mock(path).accepted
            except (extract.MockExtractionMissing, ValueError, OSError) as exc:
                print(f"{pid}: no facts from {doc['file']}: {exc}")
        for claim in product["claims"]:
            checks = run_rules(claim, facts, today, product["supply_chain"])
            verdict = decide_verdict(pid, claim["claim_id"], checks, facts, product["supply_chain"], now,
                                     claim_type=claim_type(claim))
            info = reason.explain_verdict(verdict, claim, checks, facts)
            print(f"\n== {pid} / {claim['claim_id']}: {claim['text']} ==")
            print(f"  verdict:  {verdict.verdict}")
            print(f"  template: {verdict.reason}")
            print(f"  claude:   {info.reason if info.source == 'claude' else 'none'}")
            print(f"  used: {info.source}   model: {info.model}   cached: {'yes' if info.cached else 'no'}   "
                  f"cited: {', '.join(info.cited_fact_ids) or '-'}")
            if info.rejected_reason:
                print(f"  rejected: {info.rejected_reason}")
            if info.source == "claude":
                used_claude += 1
            else:
                fell_back += 1
    print(f"\n{used_claude} claim(s) explained by Claude, {fell_back} used the template")
    return 0


if __name__ == "__main__":
    sys.exit(main())
