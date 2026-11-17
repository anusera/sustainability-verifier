# Setup (Windows, PowerShell)

Requires Python 3.11+.

```powershell
cd C:\Users\313634\sustainability-verifier
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1      # if blocked: Set-ExecutionPolicy -Scope Process Bypass
pip install -r requirements.txt
```

Run tests:   `pytest`
Run server:  `uvicorn backend.main:app --reload`

Extraction runs in MOCK_MODE by default (reads `data/mock_extractions/`).

## Real extraction (Claude)

Put `ANTHROPIC_API_KEY=...` in `.env` (git-ignored) and set `MOCK_MODE=false`. The model is `claude-sonnet-5-5`
and is recorded in the audit log's `model` field for real runs.

- **Cache:** real results are cached in `data/extraction_cache/` (git-ignored), keyed by document SHA-256, prompt
  version and model, so repeated runs cost nothing. Set `EXTRACTION_CACHE=off` to bypass it. Facts are validated again
  on every cache read.
- **Plain `pytest` never calls the API.** All unit tests use a fake client. The live test needs both a key and
  `RUN_LIVE_EXTRACTION=1`: `$env:RUN_LIVE_EXTRACTION=1; pytest tests/test_extract_live.py` (spends money).
- **Compare real vs mock:** `python scripts/compare_extraction.py [--doc STEM] [--no-cache]` prints a field-by-field
  diff for every document in `products.json`, and lists documents that yielded no accepted facts.
  It never prints the key.

## Claude-written verdict reasons

Code decides the verdict; with `MOCK_MODE=false`, Claude then explains it in 2 to 4 sentences and code accepts the text only
if it passes validation (cited fact ids, dates, numbers, countries, quotes, verdict label). Otherwise the template reason is
used and the refusal is recorded. Mock mode never calls the API.

- **Cache:** `data/reason_cache/` (git-ignored). `REASON_CACHE=off` bypasses it (`EXTRACTION_CACHE` does not).
- **Audit:** real runs write one `reason_generated` event per claim (source, cited ids, rejection reason, reason hash).
- **Compare side by side:** `python scripts/compare_reasons.py [--product ID] [--no-cache]` (about one API call per claim;
  never writes the audit log).
- The public API and shopper page never receive the explanation.

`httpx` is in requirements.txt because FastAPI's TestClient needs it.
