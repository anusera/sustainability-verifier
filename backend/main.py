"""FastAPI app: JSON APIs only (SPEC 11). No HTML pages and no QR code yet."""
import hashlib
import hmac
import os
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict

from . import audit
from .audit import DEFAULT_DOCS_DIR, DEFAULT_LOG_PATH
from .extract import DEFAULT_MOCK_DIR
from .pipeline import (
    DEFAULT_PRODUCTS_PATH,
    DEFAULT_RUNS_DIR,
    RunResult,
    UnknownProduct,
    get_product,
    load_products,
    load_snapshot,
    run_product,
)

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
NOT_FOUND_HTML = (
    '<!doctype html><html lang="en"><head><meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width, initial-scale=1">'
    "<title>Product not found</title></head><body>"
    '<h1>Product not found</h1><p>We could not find that product code.</p>'
    '<p><a href="/">Back to home</a></p></body></html>'
)

COOKIE_NAME = "regulator_session"
SESSION_TTL_SECONDS = 8 * 3600

# Public wording (SPEC 7). Fixed constants: the public view is built from these, never from evidence.
STATUS_LABEL = {
    "Verified": "Verified against supplier documents",
    "Self-declared only": "Supplier statement only",
    "Missing evidence": "Not yet proven",
    "Contradicted": "This claim is under review",
}
SUMMARY = {
    "Verified": "Supplier documents back this claim.",
    "Self-declared only": "So far only the supplier's own statement supports this claim.",
    "Missing evidence": "We do not yet have enough documents to prove this claim.",
    "Contradicted": "Our checks found a conflict with supplier records, so this claim is being looked at again.",
}


@dataclass
class Settings:
    log_path: Path = DEFAULT_LOG_PATH
    docs_dir: Path = DEFAULT_DOCS_DIR
    mock_dir: Path = DEFAULT_MOCK_DIR
    products_path: Path = DEFAULT_PRODUCTS_PATH
    runs_dir: Path = DEFAULT_RUNS_DIR
    # DEMO ONLY: fixed regulator account. Replace with real auth (auth.py) later.
    demo_user: str = field(default_factory=lambda: os.environ.get("REGULATOR_DEMO_USER", "regulator"))
    demo_password: str = field(default_factory=lambda: os.environ.get("REGULATOR_DEMO_PASSWORD", "demo-regulator"))
    session_secret: str = field(
        default_factory=lambda: os.environ.get("REGULATOR_SESSION_SECRET") or secrets.token_hex(32))


class LoginBody(BaseModel):
    username: str
    password: str


class PublicClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")
    claim_text: str
    status_label: str
    summary: str
    last_verified: str


class PublicView(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product_name: str
    claims: list[PublicClaim]


def _sign(secret: str, expiry: str) -> str:
    return hmac.new(secret.encode(), expiry.encode(), hashlib.sha256).hexdigest()


def make_session_token(secret: str, now: float | None = None) -> str:
    expiry = str(int((now if now is not None else time.time()) + SESSION_TTL_SECONDS))
    return f"{expiry}.{_sign(secret, expiry)}"


def valid_session_token(secret: str, token: str | None, now: float | None = None) -> bool:
    if not token or token.count(".") != 1:
        return False
    expiry, sig = token.split(".")
    if not expiry.isdigit() or not hmac.compare_digest(sig, _sign(secret, expiry)):
        return False
    return int(expiry) > (now if now is not None else time.time())


def build_public_view(run: RunResult) -> PublicView:
    """Built field by field from the allow-list. Never reads reason, evidence, facts, tree or hashes."""
    return PublicView(
        product_name=run.product_name,
        claims=[
            PublicClaim(
                claim_text=c.claim_text,
                status_label=STATUS_LABEL[c.verdict.verdict],
                summary=SUMMARY[c.verdict.verdict],
                last_verified=c.verdict.generated_at[:10],
            )
            for c in run.claims
        ],
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    st = settings or Settings()
    app = FastAPI(title="Sustainability Claim Verifier")

    def require_regulator(request: Request) -> None:
        if not valid_session_token(st.session_secret, request.cookies.get(COOKIE_NAME)):
            raise HTTPException(status_code=401, detail="Regulator sign-in required")

    def product_or_404(product_id: str) -> dict:
        try:
            return get_product(product_id, products_path=st.products_path)
        except UnknownProduct as exc:
            raise HTTPException(status_code=404, detail=str(exc))

    def snapshot_or_404(product_id: str) -> RunResult:
        run = load_snapshot(product_id, st.runs_dir)
        if run is None:
            raise HTTPException(
                status_code=404,
                detail=f"Not yet verified; run POST /api/run/{product_id} first")
        return run

    @app.post("/api/run/{product_id}")
    def run(product_id: str) -> RunResult:
        product_or_404(product_id)
        return run_product(
            product_id, log_path=st.log_path, docs_dir=st.docs_dir, mock_dir=st.mock_dir,
            products_path=st.products_path, runs_dir=st.runs_dir)

    @app.get("/api/products")
    def products() -> list[dict]:
        return [{"product_id": pid, "name": p["name"]}
                for pid, p in load_products(st.products_path).items()]

    @app.get("/api/public/{product_id}")
    def public(product_id: str) -> PublicView:
        product_or_404(product_id)
        return build_public_view(snapshot_or_404(product_id))

    @app.post("/api/regulator/login")
    def login(body: LoginBody, response: Response) -> dict:
        ok_user = hmac.compare_digest(body.username.encode(), st.demo_user.encode())
        ok_pass = hmac.compare_digest(body.password.encode(), st.demo_password.encode())
        if not (ok_user and ok_pass):
            raise HTTPException(status_code=401, detail="Invalid credentials")
        # Secure is off for localhost HTTP; turn it on behind HTTPS.
        response.set_cookie(COOKIE_NAME, make_session_token(st.session_secret), max_age=SESSION_TTL_SECONDS,
                            httponly=True, samesite="lax", secure=False)
        return {"signed_in": True}

    @app.get("/api/regulator/{product_id}", dependencies=[Depends(require_regulator)])
    def regulator(product_id: str) -> dict:
        product_or_404(product_id)
        run = snapshot_or_404(product_id)
        entries = [e for e in audit.read_entries(st.log_path) if e["product_id"] == product_id]
        documents = {}
        for e in entries:
            if e["event_type"] == "document_ingested":
                documents[e["details"]["file_name"]] = {
                    "file": e["details"]["file_name"],
                    "sha256": e["document_sha256"],
                    "ingested_at": e["timestamp"],
                }
        body = {
            "product_id": run.product_id,
            "product_name": run.product_name,
            "run_at": run.run_at,
            "claims": [c.model_dump() for c in run.claims],
            "tree": run.tree.model_dump(),
            "facts": [f.model_dump() for f in run.facts],
            "rejected": [r.model_dump() for r in run.rejected],
            "failed_documents": [d.model_dump() for d in run.failed_documents],
            "documents": list(documents.values()),
            "history": entries,
        }
        audit.append({"product_id": product_id, "event_type": "regulator_access", "actor": "regulator",
                      "details": {"endpoint": "regulator_view"}}, log_path=st.log_path)
        return body

    @app.get("/api/regulator/{product_id}/verify-chain", dependencies=[Depends(require_regulator)])
    def verify(product_id: str) -> dict:
        product_or_404(product_id)
        result = audit.verify_chain(st.log_path, st.docs_dir)
        try:
            audit.append({"product_id": product_id, "event_type": "regulator_access", "actor": "regulator",
                          "details": {"endpoint": "verify-chain", "intact": result["intact"]}},
                         log_path=st.log_path)
        except Exception:
            pass  # a corrupted log must not hide the integrity result
        return result

    def page(name: str) -> HTMLResponse:
        return HTMLResponse((FRONTEND_DIR / name).read_text(encoding="utf-8"))

    def product_page(product_id: str, name: str) -> HTMLResponse:
        try:
            get_product(product_id, products_path=st.products_path)
        except UnknownProduct:
            return HTMLResponse(NOT_FOUND_HTML, status_code=404)
        return page(name)

    @app.get("/", response_class=HTMLResponse)
    def home() -> HTMLResponse:
        return page("index.html")

    @app.get("/p/{product_id}", response_class=HTMLResponse)
    def shopper_page(product_id: str) -> HTMLResponse:
        return product_page(product_id, "shopper.html")

    @app.get("/regulator/{product_id}", response_class=HTMLResponse)
    def regulator_page(product_id: str) -> HTMLResponse:
        # The HTML is not gated; the data behind /api/regulator is.
        return product_page(product_id, "regulator.html")

    return app


app = create_app()
