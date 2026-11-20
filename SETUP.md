# Setup (Windows, PowerShell)

Requires Python 3.11 or newer.

```powershell
cd path\to\sustainability-verifier
python -m venv venv
venv\Scripts\Activate.ps1      # if blocked: Set-ExecutionPolicy -Scope Process Bypass
pip install -r requirements.txt
```

macOS/Linux: `python3 -m venv venv`, then `source venv/bin/activate`, then `pip install -r requirements.txt`.

Run tests:   `python -m pytest -q`
Run server:  `uvicorn backend.main:app --reload`

Extraction runs in mock mode by default (reads `data/mock_extractions/`), so no API key is needed.

## Demo accounts

These are demo-only logins, not real security. Do not reuse them on a hosted copy.

| Role | Page | Username | Password |
|---|---|---|---|
| Retailer | `/dashboard` | `retailer` | `demo-retailer` |
| Regulator | `/regulator/{product_id}` | `regulator` | `demo-regulator` |

The shopper page, `/p/{product_id}`, needs no login. Product ids: `tshirt-001`, `bottle-001`,
`bottle-eco-001`, `shirt-001`.

## Try it

1. Start the server and open `http://127.0.0.1:8000/dashboard`.
2. Sign in as the retailer and click **Run verification** on each product.
3. Open a shopper page and a regulator page, and click **Verify integrity**.

## Reset to a clean state

The audit log and run snapshots are created at runtime and are git-ignored. To start clean, stop the server and run:

```powershell
Remove-Item data\audit_log.jsonl -ErrorAction SilentlyContinue
Remove-Item data\runs -Recurse -Force -ErrorAction SilentlyContinue
```

If you edit a document under `data/documents/`, restore it (`git restore data/documents/`) and reset the log, or
Verify integrity will report that the document changed.

## Real extraction (Claude)

Put `ANTHROPIC_API_KEY=...` in `.env` (git-ignored). Turn real mode on for one terminal only, so the app never
spends credits by accident: `$env:MOCK_MODE="false"`. Set a spending limit in the Anthropic console first.
The model is `claude-sonnet-5-5`. The audit log's `model` field names it on entries whose content it produced
and says `n/a` where no model was involved.

- **Cache:** real results are cached in `data/extraction_cache/` (git-ignored), keyed by document SHA-256, prompt
  version and model, so repeated runs cost nothing. Set `$env:EXTRACTION_CACHE="off"` to bypass it. Facts are
  validated again on every cache read.
- **Plain `python -m pytest` never calls the API.** All unit tests use a fake client. The live test needs both a key
  and `RUN_LIVE_EXTRACTION=1`: `$env:RUN_LIVE_EXTRACTION="1"; python -m pytest tests/test_extract_live.py`
  (spends money).
- **Compare real vs mock:** `python scripts/compare_extraction.py [--doc STEM] [--no-cache]` prints a field-by-field
  diff for every document in `products.json`, and lists documents that yielded no accepted facts.
  It never prints the key.

## Claude-written verdict reasons

Code decides the verdict; in real mode, Claude then explains it in 2 to 4 sentences and code accepts the text only
if it passes validation (cited fact ids, dates, numbers, countries, quotes, verdict label). Otherwise the template
reason is used and the rejection is recorded. Mock mode never calls the API.

- **Cache:** `data/reason_cache/` (git-ignored). `$env:REASON_CACHE="off"` bypasses it (`EXTRACTION_CACHE` does not).
- **Audit:** real runs write one `reason_generated` event per claim (source, cited ids, rejection reason, reason hash).
- **Compare side by side:** `python scripts/compare_reasons.py [--product ID] [--no-cache]` (about one API call per
  claim; never writes the audit log).
- The public API and shopper page never receive the explanation.

`httpx` is in requirements.txt because FastAPI's TestClient needs it.