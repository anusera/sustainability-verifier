# Product-Level Sustainability Claim Verifier

## Problem
Retailers make sustainability claims (origin, packaging, carbon) but cannot prove them per product.
Evidence is scattered across supplier tiers as messy documents and is never linked to the product on the shelf.

## Solution
An evidence engine that traces each product claim through supplier tiers to source documents, issues an honest
verdict, and publishes it via a QR code with two views:
- Shopper page (public, mobile-first, short, no supplier names or files)
- Regulator page (login-protected, full evidence trail, hashes, timestamps, verification history)

## Non-negotiable rules
- Claude (the LLM) reads, reasons and writes. Code does rules and ALL math. The LLM never produces carbon numbers.
- Nothing is shown as a verified fact without a cited source (exact quote + file + page/cell).
- "No evidence found" is a valid output. Never invent evidence.
- Low-confidence facts go to a human review queue (later increment).
- The QR code encodes only a stable URL (/p/{product_id}), never data.
- The audit log is append-only and hash-chained (SHA-256). No blockchain, and do not claim one.
- The shopper page supports but does not replace on-pack claims. Contradicted claims show "This claim is under review".
- Shopper wording: "Verified against supplier documents", "Supplier statement only", "Not yet proven".

## Build approach: walking skeleton
Build the thinnest end-to-end slice first, then thicken. Use real file structure and real interfaces from the start.
Extraction runs in MOCK_MODE (reads hand-written JSON) until an API key is available, then swaps to a real Claude call
behind the SAME function signature.

## Slice 1 scope
One product (tshirt-001, "Everyday Organic Tee"), one claim (c1, origin, "Organic cotton from India"),
four documents, two rules, one verdict, hash-chained log, QR, bare shopper and regulator pages.

## Folder structure
```
CLAUDE.md
data/products.json
data/documents/{tshirt_bom,tier1_declaration,tier2_gots_certificate,tier3_ginning_invoice}.txt
data/mock_extractions/   # one JSON per document, same base names
data/answer_key.json
data/audit_log.jsonl     # created at runtime
backend/{models,timeutil,extract,rules,verdict,reason,audit,passport,main}.py   # timeutil: shared utc_now_iso(); reason: Claude explains a finished verdict, code validates it
.gitattributes           # "data/documents/* -text": documents are hashed byte-exact, Git never rewrites line endings
frontend/{shopper,regulator}.html
tests/test_slice.py
```
Skip for now: carbon.py, auth.py, retailer dashboard. Regulator "sign in" is a hard-coded button for now.

## Tech
Python 3.11+, FastAPI, uvicorn, pydantic, pandas, pdfplumber, reportlab, qrcode[pil], python-dotenv, anthropic, pytest.
Single-file HTML frontend, served by FastAPI static files.

## Data contracts (fix these now)

### Fact (Pydantic)
fact_id, doc_id, supplier, tier(int), material, origin_country, certification_body, certificate_id,
valid_from, valid_to, document_date, recycled_content_pct, weight_kg, source_file, source_page,
source_quote, confidence(float). All optional except ids, supplier, tier, source_file, source_quote.

### Check (every rule returns this shape)
{"check_id":"origin_match","result":"pass|fail","detail":"...","evidence":["fact ids"],"rules_version":"rules-v1.0"}

### Verdict
{"product_id","claim_id","verdict":"Verified|Self-declared only|Contradicted|Missing evidence","reason",
 "checks":["origin_match: fail"],"evidence":["fact ids"],"reviewed_by_human":false,"generated_at","rules_version"}
In slice 1, `reason` is a template filled from check details. Claude writes it later.

### Audit log entry (one JSON object per line)
entry_id, timestamp, product_id, event_type, actor, details(object), document_sha256, model, rules_version,
prev_hash (64 zeros for the first entry), entry_hash (SHA-256 of all the other fields in canonical JSON).
`model` records the model that produced the entry's content, or `n/a`.
Events in slice 1: document_ingested, facts_extracted, rules_run, verdict_changed, passport_published.
Functions: append(event), verify_chain() (recompute every hash, check prev_hash links, re-hash stored documents,
report the first broken entry).

## Rules (slice 1)
- origin_match: compare claimed_origin with origin_country on every fact. Any mismatch fails; evidence lists the
  conflicting facts.
- cert_validity: for any fact with valid_to, fail if valid_to is earlier than the document_date of any other
  document that has a document_date (e.g. the May invoice) or earlier than today. Say which one failed.

## Verdict logic (slice 1)
Any failed origin check -> Contradicted. No fails but no third-party evidence -> Self-declared only.
A tier with no facts -> Missing evidence. All pass with a valid certificate -> Verified.

## Slice 1 data (planted flaws)
- tier1_declaration: Sunrise Garments Pvt Ltd (tier 1), 12 June 2026, says organic cotton sourced in India via
  Coimbatore Textile Mills.
- tier2_gots_certificate: Coimbatore Textile Mills (tier 2), cert GOT-IN-48213, Global Organic Textile Certification
  (demo certifier), valid 01 Apr 2023 to 31 Mar 2026 (EXPIRED before the May invoice).
- tier3_ginning_invoice: Delta Ginning Co., Khulna (tier 3), invoice 2291, 02 May 2026, sold to Coimbatore Textile
  Mills, raw organic cotton lint 18,000 kg, "Country of origin: Bangladesh" (CONTRADICTS the India claim).
- tshirt_bom: components fabric 0.18 kg + thread 0.01 kg, chain Sunrise > Coimbatore > Delta Ginning.
Expected result: Contradicted, with origin_match and cert_validity both failing.

## Endpoints
POST /api/run/{id} (ingest, extract, rules, verdict, log) | GET /api/public/{id} (shopper-safe only) |
GET /api/regulator/{id} (verdict, checks, tree, facts with quotes, hashes, history) |
GET /api/regulator/{id}/verify-chain | GET /qr/{id}.png (encodes /p/{id}) | GET /p/{id} | GET /regulator/{id}

## Shopper card (slice 1)
"Origin: This claim is under review. Last verified <date>."
"This page supports, but does not replace, claims printed on pack."

## Definition of done (tests/test_slice.py)
1. POST /api/run/tshirt-001 returns Contradicted with both checks failing.
2. Facts include the Bangladesh quote with its source file.
3. The public API response contains no supplier names or file names.
4. verify_chain() reports intact, and reports the first broken entry after one log line is edited.
5. /qr/tshirt-001.png encodes a URL that opens the shopper page showing "under review".

## Later increments (do NOT build yet)
1. Real Claude extraction (claude-sonnet-5-5) replaces the mock; compare against mock JSON
2. Integrity button and tamper demo
3. Bottle product: packaging claim, missing tier-3 supplier, 60% vs 100% recycled content
4. Supplier email drafting
5. Ready meal with carbon calculator (code only, demo emission factors labelled as estimates)
6. Review queue for low-confidence facts
7. Evaluation page, mock labels with QR, polish

SPEC.md is the full reference. Skip passport.py and the QR route for now. Slice 1 test 5 becomes: GET /p/tshirt-001 returns the shopper page showing ‘under review’.