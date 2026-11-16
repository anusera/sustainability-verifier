# SPEC: Product-Level Sustainability Claim Verifier

Version 0.1 | Status: Draft for build | Date: 13 Nov 2026

## 1. Purpose

Retailers make sustainability claims (origin, packaging, carbon) that they cannot prove at product level, because the evidence sits with suppliers across several tiers in messy, unlinked documents.

This system traces each product claim through the supplier tiers to its source documents, issues an honest verdict with a reason, and publishes the result through a QR code with two views: a short public shopper page and a login-protected regulator evidence trail.

**Success means:** sustainability data is traceable and verifiable per product across all tiers; claims stand up to scrutiny; regulators can check them without asking the retailer; shoppers see what is and is not proven.

## 2. Scope

### In scope (full demo)
- 3 products: cotton T-shirt (origin), bottled drink (packaging), ready meal (carbon)
- About 25 synthetic supplier documents with planted flaws
- Extraction, rules, carbon calculation, cross-document reasoning, verdicts
- QR passport, shopper page, regulator page, hash-chained audit log
- Supplier evidence-request email drafts, review queue, evaluation page

### Slice 1 (build first)
One product (`tshirt-001`), one claim (`c1` origin), four documents, two rules, one verdict, audit log, QR, bare shopper and regulator pages. Runs in MOCK_MODE with no API key.

### Out of scope
- Detecting forged certificates (roadmap: check IDs against certifier registries)
- Live supplier integrations, sending real emails, supplier portal
- Real company data (all data is synthetic)
- Legal review of claims
- Blockchain (a hash chain is used and no blockchain is claimed)

## 3. Users and needs

| Persona | Role | Primary need |
|---|---|---|
| Priya | Compliance manager | See risk, understand and defend verdicts, publish passports |
| Arjun | Buyer | Chase missing evidence with specific requests |
| Sam | Shopper | Ten-second honest profile at the shelf |
| Meera | Regulator or auditor | Full evidence trail and tamper-evident history |
| Lina | Supplier | Know exactly which document to provide |
| Reviewer | Retailer staff | Confirm or correct low-confidence facts |

## 4. Design principles (non-negotiable)

1. The LLM reads, reasons and writes. Code does the rules and all math. The LLM never produces carbon numbers.
2. No fact is shown as verified without a cited source: exact quote, file and page or cell.
3. "No evidence found" is a valid output. Never invent evidence.
4. Low-confidence facts go to a human reviewer.
5. The QR code encodes only a stable URL (`/p/{product_id}`), never data.
6. The audit log is append-only and SHA-256 hash-chained.
7. The shopper page supports, but does not replace, on-pack claims. A contradicted claim must not be printed on pack.
8. Rules and thresholds are configurable and versioned (`rules_version`), not scattered in code.

## 5. Functional requirements

Priority: **M** = must (slice 1), **S** = should (full demo), **C** = could.

### 5.1 Ingestion and extraction
| ID | Requirement | Pri |
|---|---|---|
| F1 | Load documents from `data/documents/` (txt first; PDF, CSV, XLSX later) | M |
| F2 | Compute SHA-256 of each document at ingestion and log it | M |
| F3 | Extract facts into the Fact schema, each with source quote, file and page/cell | M |
| F4 | MOCK_MODE reads `data/mock_extractions/`; real mode calls Claude behind the same function signature | M |
| F5 | Unreadable or failed files are listed, never silently dropped | S |
| F6 | Facts with confidence below a configurable threshold (default 0.7) enter the review queue | S |

### 5.2 Linking and verification
| ID | Requirement | Pri |
|---|---|---|
| F7 | Build the supplier tree per product from `products.json` (retailer, then tier 1, 2, 3) | M |
| F8 | Run deterministic rules (section 8) and return Check objects | M |
| F9 | Combine checks into one verdict per claim (section 9) | M |
| F10 | Carbon calculation in code from an emission factor table, shown as a range with demo factors labelled as estimates | S |
| F11 | Claude cross-document reasoning: contradictions, vague wording, chain gaps, plain-language reason | S |
| F12 | Detect vague claims (for example "eco-friendly") and mark them Not yet proven | S |

### 5.3 Publishing and access
| ID | Requirement | Pri |
|---|---|---|
| F13 | Pre-publish check listing non-verified claims, warning against printing contradicted ones | S |
| F14 | Generate a QR PNG encoding `/p/{id}` | M |
| F15 | Public shopper API and page, with no supplier names or file names | M |
| F16 | Regulator API and page requiring sign-in (demo: fixed account and session cookie) | M (button), S (cookie) |
| F17 | Every regulator access is written to the audit log | S |
| F18 | Evidence pack export (claims, verdicts, checks, tree, numbered sources with hashes, history, what was not proven) | S |

### 5.4 Audit and review
| ID | Requirement | Pri |
|---|---|---|
| F19 | Append-only, hash-chained audit log (section 10) | M |
| F20 | `verify_chain()` recomputes every entry hash, checks `prev_hash` links, re-hashes stored documents, and reports the first broken entry | M |
| F21 | Human review actions (confirm, correct, reject) update affected verdicts and mark `reviewed_by_human` | S |

### 5.5 Supplier and evaluation
| ID | Requirement | Pri |
|---|---|---|
| F22 | Claude drafts a specific, editable evidence request email (product, claim, document needed, date range, reason). Nothing is sent | S |
| F23 | Evaluation page: detection rate vs answer key, false positives, percentage of claims with a fully cited chain, tamper-test result | C |

## 6. Data contracts

### 6.1 Fact
| Field | Type | Required |
|---|---|---|
| fact_id | string | yes |
| doc_id | string | yes |
| supplier | string | yes |
| tier | int | yes |
| material | string | no |
| origin_country | string | no |
| certification_body | string | no |
| certificate_id | string | no |
| valid_from | date (ISO) | no |
| valid_to | date (ISO) | no |
| document_date | date (ISO) | no. Null when a document has no issue date (for example a certificate) |
| recycled_content_pct | float | no |
| weight_kg | float | no |
| source_file | string | yes |
| source_page | int | no (use the same type everywhere; for spreadsheets add `source_cell`) |
| source_quote | string (verbatim) | yes |
| confidence | float 0 to 1 | no (model default is None; mock extraction files set it explicitly) |

### 6.2 Check
```json
{"check_id": "origin_match", "result": "pass|fail", "detail": "text",
 "evidence": ["fact ids"], "rules_version": "rules-v1.0"}
```

### 6.3 Verdict
```json
{"product_id": "", "claim_id": "",
 "verdict": "Verified|Self-declared only|Contradicted|Missing evidence",
 "reason": "", "checks": ["origin_match: fail"], "evidence": ["fact ids"],
 "reviewed_by_human": false, "generated_at": "", "rules_version": "rules-v1.0"}
```
In slice 1, `reason` is a template filled from check details. From the Claude increment, Claude writes it from the same inputs and cites fact ids.

### 6.4 Product (`products.json`, keyed by product id)
Holds name, claims (`claim_id`, `type`, `text`, `claimed_origin` or other claim parameters), documents list, and supplier tiers with parent links.

## 7. Visibility table

| Item | Shopper (public) | Regulator (signed in) |
|---|---|---|
| Product name | Yes | Yes |
| Claim status label | Yes | Yes |
| One-sentence plain explanation | Yes | Yes |
| Last verified date | Yes | Yes |
| What was checked (summary) | Yes, in expander | Yes, full detail |
| Supplier names and tiers | No | Yes |
| Source documents and quotes | No | Yes |
| SHA-256 hashes and timestamps | No | Yes |
| Audit history and integrity check | No | Yes |
| Evidence pack | No | Yes |

**Shopper status labels (exact wording):**
- "Verified against supplier documents"
- "Supplier statement only"
- "Not yet proven"
- "This claim is under review" (for Contradicted)

Footer on every shopper page: "This page supports, but does not replace, claims printed on pack."

## 8. Rules (version `rules-v1.0`)

| Check | Logic |
|---|---|
| `origin_match` | Compare the claimed origin with `origin_country` on every fact that has one. Any mismatch fails; evidence lists the conflicting facts and the matching ones |
| `cert_validity` | For each fact with `valid_to`, fail if `valid_to` is earlier than the `document_date` of any fact from a different document that has one, or earlier than today. The detail states which comparison failed. Facts with null `document_date` are skipped in the comparison |
| `weight_reconciliation` (later) | Component weights sum to the stated total within a tolerance |
| `recycled_content_match` (later) | Claimed percentage does not exceed the specified or certified percentage |
| `tier_coverage` (later) | Every supplier in the bill of materials has at least one fact; otherwise Missing evidence |
| `vague_claim` (later) | Flags unqualified or ambiguous terms ("eco-friendly", "up to 100%") |

## 9. Verdict logic

Evaluate in this order and stop at the first match:

1. Any failed `origin_match` or other content-contradiction check: **Contradicted**
2. A tier in the supplier tree with no facts: **Missing evidence**
3. No third-party evidence, or the only certificate has failed `cert_validity`: **Self-declared only**
4. All checks pass and a valid third-party certificate is present: **Verified**

Design note: an expired certificate on its own gives Self-declared only, not Contradicted, because it removes the third-party backing without proving the claim false. Revisit if you disagree.

Expected slice-1 result: **Contradicted**, with `origin_match` and `cert_validity` both failing.

## 10. Audit log

One JSON object per line in `data/audit_log.jsonl`.

| Field | Notes |
|---|---|
| entry_id | Integer, increasing |
| timestamp | UTC ISO 8601 |
| product_id | |
| event_type | `document_ingested`, `facts_extracted`, `rules_run`, `verdict_changed`, `human_review`, `passport_published`, `regulator_access` |
| actor | `system`, a user name, or `regulator` |
| details | Object, short and event-specific |
| document_sha256 | For ingestion events, otherwise null |
| model | `mock` or the model name |
| rules_version | |
| prev_hash | 64 zeros for the first entry |
| entry_hash | SHA-256 over all other fields in canonical JSON (sorted keys, no whitespace) |

`verify_chain()` returns `{"intact": true}` or `{"intact": false, "first_broken_entry": n, "reason": "..."}`. This format is fixed; changing it breaks existing chains.

## 11. API

| Route | Purpose | Auth |
|---|---|---|
| `POST /api/run/{id}` | Ingest, extract, run rules, produce verdict, write log | Retailer (open in demo) |
| `GET /api/public/{id}` | Shopper-safe data only | None |
| `GET /api/regulator/{id}` | Verdict, checks, tree, facts with quotes, hashes, history | Regulator |
| `GET /api/regulator/{id}/verify-chain` | Integrity result | Regulator |
| `GET /api/regulator/{id}/documents/{doc_id}` | Source file with cited passage | Regulator |
| `GET /api/regulator/{id}/evidence-pack` | Export | Regulator |
| `GET /qr/{id}.png` | QR encoding `/p/{id}` | None |
| `GET /p/{id}` | Shopper page | None |
| `GET /regulator/{id}` | Regulator page | Regulator |
| `POST /api/products/{id}/request-evidence` | Drafts supplier email | Retailer |
| `GET /products`, `/products/{id}/verdicts`, `/products/{id}/tree` | Retailer dashboard | Retailer |

## 12. UI requirements

**Shopper page** (mobile first, readable in about ten seconds): product name; up to three claim cards, each with status label, one plain sentence and "Last verified" date; a "See more detail" expander with summaries only; the on-pack footer note.

**Regulator page:** sign-in; claim list with verdicts; checks with pass or fail; supplier tree coloured by evidence status (dashed grey for no documents); facts table with source quotes and the contradicting line highlighted; source viewer with hash and upload time; history timeline; "Verify integrity" button; evidence pack download.

**Retailer dashboard (later):** product list with colour-coded verdicts, summary counts, publish passport button with pre-publish check, review queue, email drafting.

## 13. Claude usage (from the API-key increment)

| Task | Input | Output |
|---|---|---|
| Extraction | One document | JSON list of Facts with verbatim `source_quote`; null where not stated |
| Reasoning | Claim plus all facts for the product | JSON: contradictions, vague wording, gaps, verdict reason citing fact ids |
| Factor selection | Ingredient description | Which emission factor key applies, with a short rationale. Code multiplies |
| Email drafting | Missing-evidence gap | Editable email text |

Rules for prompts: output JSON only; use only text present in the document; never guess; verbatim quotes are validated in code against the source (a quote not found in the document rejects the fact).

## 14. Non-functional requirements

- **Reproducibility:** every verdict can be regenerated from its cited facts, rules version and model name.
- **Security:** API key only in `.env` (git-ignored); regulator routes require a session; the public API never returns supplier names, file names or hashes.
- **Privacy and data:** synthetic data only; no personal data.
- **Performance:** a slice run completes in under 5 seconds in mock mode; under about 60 seconds per product with real extraction.
- **Accessibility:** shopper page works on a 360 px wide screen, with sufficient contrast and text labels alongside colour.
- **Maintainability:** one module per concern, typed models, rules configurable.

## 15. Test plan and acceptance

### Slice 1 definition of done (`tests/test_slice.py`)
1. `POST /api/run/tshirt-001` returns Contradicted with `origin_match` and `cert_validity` both failing.
2. Facts include the Bangladesh quote with its source file.
3. The public API response contains no supplier names or file names.
4. `verify_chain()` reports intact, and reports the first broken entry after one log line is edited.
5. `/qr/tshirt-001.png` encodes a URL that opens the shopper page showing "under review".

### Unit tests (step 2)
- `origin_match` fails on the Bangladesh fact and lists it as evidence
- `cert_validity` fails and states whether it failed against the invoice date or today
- Verdict cases: Contradicted, Self-declared only, Missing evidence, Verified

### Full-demo metrics
- Detection rate of planted flaws above 90%, with false positives reported
- Percentage of claims with a fully cited chain
- Tamper test: a modified log entry and a modified document are both detected

## 16. Demo data and answer key

| Product | Claim | Planted flaws |
|---|---|---|
| tshirt-001 | Organic cotton from India | Tier-3 invoice says Bangladesh; certificate GOT-IN-48213 expired 2026-03-31, before the 2026-05-02 invoice |
| Bottled drink | 100% recycled bottle | Packaging spec says 60%; tier-3 resin supplier missing; vague "eco-friendly packaging" wording |
| Ready meal | 30% lower carbon | Missing weight for one ingredient; emission factor assumptions flagged |

`data/answer_key.json` records each planted flaw, the expected verdict and the expected failing checks.

## 17. Build increments

| # | Increment | Done when |
|---|---|---|
| 0 | Slice 1 (mock mode) | Five definition-of-done tests pass |
| 1 | Real Claude extraction | Output matches mock JSON on the four slice documents |
| 2 | Integrity button and tamper demo | UI shows intact, then a broken entry after editing |
| 3 | Bottle product | Packaging flaws detected, Missing evidence shown |
| 4 | Supplier email drafting | Email names product, claim, document, date range |
| 5 | Ready meal and carbon calculator | Table with factors and a range; code does the math |
| 6 | Review queue | Low-confidence facts reviewable; verdicts update; logged |
| 7 | Evaluation page, mock labels with QR, polish | Metrics visible; phone scan works |

Each increment leaves the app working and is committed to Git.

## 18. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Extraction errors | Confidence scores, verbatim-quote validation, review queue |
| Forged certificates | Out of scope; roadmap registry checks |
| Carbon data quality | Show method and range; label factors as estimates |
| "Verified" wording becomes a claim the retailer must defend | Use "verified against supplier documents", show the date, keep the on-pack note |
| Regulatory details change | Rules configurable and versioned; check current status before citing any regulation |
| Phone cannot open localhost | Same Wi-Fi with laptop IP, ngrok, or a deployed copy; keep a backup video |
| Scope creep | Walking-skeleton order; do not start an increment until the previous one passes |

## 19. Tech stack

Python 3.11+, FastAPI, uvicorn, Pydantic, JSON and JSONL storage, hashlib, `qrcode[pil]`, single-file HTML with vanilla JavaScript, pytest and httpx, python-dotenv. From the API-key increment: `anthropic` SDK (model `claude-sonnet-5-5`), pdfplumber, pandas, openpyxl. Later: reportlab, a simple session cookie for sign-in, optional ngrok.

Production path: Postgres, a real identity provider, object storage for documents, a task queue for ingestion, batch-level QR codes, and certifier registry checks.

## 20. Open decisions

1. Should an expired certificate alone give Contradicted or Self-declared only? (Spec chooses Self-declared only.)
2. Use integer `source_page` everywhere? (Spec chooses integer, plus `source_cell` for spreadsheets.)
3. SKU-level QR codes for the demo, with batch-level as the production version. Confirm.
4. Confidence threshold for the review queue (default 0.7).
