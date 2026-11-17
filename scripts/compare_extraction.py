"""Field-by-field diff between real (Claude) and mock extraction for every document in products.json.

    python scripts/compare_extraction.py [--doc STEM] [--no-cache]

Calls the real API (costs money unless results are cached). Forces MOCK_MODE=false for this process only.
Never prints the API key, environment values, document text or the model's raw output.
Exit code 1 if any comparison differs (apart from KNOWN_DIFFERENCES) or any document fails.
"""
import argparse
import json
import math
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend import extract  # noqa: E402

KEY_FIELDS = ("supplier", "tier", "origin_country", "certificate_id", "valid_to", "document_date",
              "recycled_content_pct")

# (doc_id, field) -> why the hand-written mock differs from what the prompt asks for.
KNOWN_DIFFERENCES = {
    ("tier2_gots_certificate", "origin_country"):
        "mock takes India from the operator's address; the prompt only allows a stated origin",
}


def value_set(facts, field: str) -> list:
    """Distinct non-null values of a field across facts, in a stable order."""
    values = []
    for fact in facts:
        v = getattr(fact, field)
        if v is not None and not any(_same(v, w) for w in values):
            values.append(v)
    return sorted(values, key=lambda v: (isinstance(v, str), v))


def _same(a, b) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        return math.isclose(a, b, rel_tol=0, abs_tol=1e-9)
    return a == b


def sets_match(mock_values: list, real_values: list) -> bool:
    return (all(any(_same(m, r) for r in real_values) for m in mock_values)
            and all(any(_same(r, m) for m in mock_values) for r in real_values))


def _fmt(values: list) -> str:
    return ", ".join(str(v) for v in values) if values else "-"


def documents_in_products(products_path: Path) -> list[tuple[str, Path]]:
    products = json.loads(Path(products_path).read_text(encoding="utf-8"))
    seen, out = set(), []
    for product in products.values():
        for doc in product["documents"]:
            if doc["doc_id"] not in seen:
                seen.add(doc["doc_id"])
                out.append((doc["doc_id"], ROOT / "data" / "documents" / doc["file"]))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--doc", help="only this document (doc id, e.g. tier3_ginning_invoice)")
    parser.add_argument("--no-cache", action="store_true", help="ignore and do not write the extraction cache")
    args = parser.parse_args(argv)

    os.environ["MOCK_MODE"] = "false"
    if args.no_cache:
        os.environ["EXTRACTION_CACHE"] = "off"

    docs = documents_in_products(extract.DEFAULT_PRODUCTS_PATH)
    if args.doc:
        docs = [(d, p) for d, p in docs if d == args.doc]
        if not docs:
            print(f"No document '{args.doc}' in products.json")
            return 2

    compared = matched = known = 0
    errors: list[str] = []
    without_facts: list[tuple[str, int]] = []

    for doc_id, path in docs:
        print(f"\n== {doc_id} ==")
        try:
            real = extract.extract_facts(path, context=extract.doc_context(path))
        except (extract.ExtractionError, FileNotFoundError) as exc:
            print(f"  ERROR: {exc}")
            errors.append(doc_id)
            continue
        try:
            mock = extract.extract_mock(path)
        except (extract.MockExtractionMissing, ValueError) as exc:
            print(f"  no mock to compare: {exc}")
            mock = None

        print(f"  model: {real.model}   cached: {'yes' if real.cached else 'no'}   "
              f"accepted: {len(real.accepted)}   rejected: {len(real.rejected)}")
        for r in real.rejected:
            who = r.fact.fact_id if r.fact else "(unparsed item)"
            print(f"  rejected {who}: {r.reason}")
        if not real.accepted:
            without_facts.append((doc_id, len(real.rejected)))
        if mock is None:
            continue

        print(f"  {'field':<22} {'mock':<28} {'real':<28} result")
        for field in KEY_FIELDS:
            m, r = value_set(mock.accepted, field), value_set(real.accepted, field)
            if not m and not r:
                continue
            compared += 1
            if sets_match(m, r):
                matched += 1
                status = "OK"
            elif (doc_id, field) in KNOWN_DIFFERENCES:
                known += 1
                status = f"KNOWN DIFF ({KNOWN_DIFFERENCES[(doc_id, field)]})"
            else:
                status = "DIFF"
            print(f"  {field:<22} {_fmt(m):<28} {_fmt(r):<28} {status}")

    print(f"\n{matched} of {compared} field comparisons match ({known} known differences).")
    if without_facts:
        print("\nDocuments without accepted facts:")
        for doc_id, n in without_facts:
            print(f"  {doc_id}: {n} rejected")
    if errors:
        print("\nDocuments that failed: " + ", ".join(errors))
    return 0 if matched + known == compared and not errors else 1


if __name__ == "__main__":
    sys.exit(main())
