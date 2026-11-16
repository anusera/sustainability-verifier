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
Later, put `ANTHROPIC_API_KEY` in a `.env` file and set `MOCK_MODE=false`.

`httpx` is in requirements.txt because FastAPI's TestClient needs it.
