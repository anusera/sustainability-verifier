"""Append-only, SHA-256 hash-chained audit log (SPEC section 10).

One JSON object per line. Each entry's entry_hash is the SHA-256 of all its other fields in canonical
JSON (sorted keys, no whitespace, UTF-8). prev_hash links each entry to the one before; the first
entry uses 64 zeros. This is a hash chain, not a blockchain.

Known limits: truncating the last line(s), or rewriting the whole chain with recomputed hashes, is not
detected without an external anchor. The append lock protects threads in one process only.
"""
import hashlib
import json
import threading
from pathlib import Path

from .models import RULES_VERSION
from .timeutil import utc_now_iso

_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOG_PATH = _ROOT / "data" / "audit_log.jsonl"
DEFAULT_DOCS_DIR = _ROOT / "data" / "documents"
GENESIS_HASH = "0" * 64
EVENT_TYPES = {
    "document_ingested", "facts_extracted", "rules_run", "verdict_changed",
    "human_review", "passport_published", "regulator_access",
}

_EVENT_DEFAULTS = {
    "actor": "system",
    "details": None,  # replaced with a fresh {} per entry
    "document_sha256": None,
    "model": "mock",
    "rules_version": RULES_VERSION,
}
_REQUIRED = {"product_id", "event_type"}
_ALLOWED = _REQUIRED | set(_EVENT_DEFAULTS)

_APPEND_LOCK = threading.Lock()

# Reason prefixes for verify_chain (one per kind of break).
ENTRY_HASH_MISMATCH = "entry hash mismatch"
PREV_HASH_BROKEN = "prev_hash link broken"
DOCUMENT_CHANGED = "document changed"
DOCUMENT_MISSING = "document missing"


def sha256_file(path: Path) -> str:
    """Hash a file's raw bytes, so line endings are never normalised."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical_json(obj: dict) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def compute_entry_hash(entry: dict) -> str:
    body = {k: v for k, v in entry.items() if k != "entry_hash"}
    return hashlib.sha256(canonical_json(body)).hexdigest()


def _nonblank_lines(log_path: Path) -> list[tuple[int, str]]:
    """(1-based line number, text) for every non-blank line."""
    if not log_path.exists():
        return []
    with open(log_path, "r", encoding="utf-8", newline="") as f:
        return [(i, line.strip()) for i, line in enumerate(f, start=1) if line.strip()]


def append(event: dict, *, log_path: Path = DEFAULT_LOG_PATH, timestamp: str | None = None) -> dict:
    """Append one event to the log and return the full stored entry."""
    keys = set(event)
    missing = _REQUIRED - keys
    if missing:
        raise ValueError(f"event is missing required keys: {sorted(missing)}")
    unknown = keys - _ALLOWED
    if unknown:
        raise ValueError(f"event has keys that append() sets or does not allow: {sorted(unknown)}")
    if event["event_type"] not in EVENT_TYPES:
        raise ValueError(f"unknown event_type: {event['event_type']!r}")

    log_path = Path(log_path)
    with _APPEND_LOCK:
        lines = _nonblank_lines(log_path)
        if lines:
            last = json.loads(lines[-1][1])
            entry_id, prev_hash = int(last["entry_id"]) + 1, last["entry_hash"]
        else:
            entry_id, prev_hash = 1, GENESIS_HASH

        entry = {
            "entry_id": entry_id,
            "timestamp": timestamp if timestamp is not None else utc_now_iso(),
            "product_id": event["product_id"],
            "event_type": event["event_type"],
            "actor": event.get("actor", _EVENT_DEFAULTS["actor"]),
            "details": event.get("details") or {},
            "document_sha256": event.get("document_sha256"),
            "model": event.get("model", _EVENT_DEFAULTS["model"]),
            "rules_version": event.get("rules_version", _EVENT_DEFAULTS["rules_version"]),
            "prev_hash": prev_hash,
        }
        entry["entry_hash"] = compute_entry_hash(entry)

        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8", newline="\n") as f:
            f.write(canonical_json(entry).decode("utf-8") + "\n")
    return entry


def log_document_ingested(product_id: str, doc_path: Path, *, log_path: Path = DEFAULT_LOG_PATH,
                          timestamp: str | None = None, actor: str = "system", model: str = "mock") -> dict:
    doc_path = Path(doc_path)
    return append(
        {
            "product_id": product_id,
            "event_type": "document_ingested",
            "actor": actor,
            "details": {"file_name": doc_path.name},
            "document_sha256": sha256_file(doc_path),
            "model": model,
        },
        log_path=log_path,
        timestamp=timestamp,
    )


def read_entries(log_path: Path = DEFAULT_LOG_PATH) -> list[dict]:
    return [json.loads(text) for _, text in _nonblank_lines(Path(log_path))]


def _broken(n: int, reason: str) -> dict:
    return {"intact": False, "first_broken_entry": n, "reason": reason}


def _is_bare_name(name) -> bool:
    return (isinstance(name, str) and name not in ("", ".", "..")
            and "/" not in name and "\\" not in name and Path(name).name == name)


def verify_chain(log_path: Path = DEFAULT_LOG_PATH, docs_dir: Path = DEFAULT_DOCS_DIR) -> dict:
    """Recompute every entry hash, check every prev_hash link and re-hash ingested documents.

    Returns {"intact": True} or {"intact": False, "first_broken_entry": n, "reason": "..."},
    where n is the stored entry_id of the first bad line (the line number if there is none).
    """
    docs_dir = Path(docs_dir)
    expected_prev = GENESIS_HASH
    for line_no, text in _nonblank_lines(Path(log_path)):
        try:
            entry = json.loads(text)
        except json.JSONDecodeError:
            return _broken(line_no, f"{ENTRY_HASH_MISMATCH}: line {line_no} is not valid JSON")
        if not isinstance(entry, dict):
            return _broken(line_no, f"{ENTRY_HASH_MISMATCH}: line {line_no} is not a JSON object")

        n = entry.get("entry_id")
        if not isinstance(n, int) or isinstance(n, bool):
            n = line_no

        if entry.get("entry_hash") != compute_entry_hash(entry):
            return _broken(n, f"{ENTRY_HASH_MISMATCH}: stored entry_hash does not match the entry's contents")
        if entry.get("prev_hash") != expected_prev:
            return _broken(n, f"{PREV_HASH_BROKEN}: prev_hash does not match the previous entry's entry_hash")

        if entry.get("event_type") == "document_ingested":
            details = entry.get("details")
            name = details.get("file_name") if isinstance(details, dict) else None
            if not _is_bare_name(name):
                return _broken(n, f"{DOCUMENT_MISSING}: {name} (not a bare file name)")
            path = docs_dir / name
            if not path.is_file():
                return _broken(n, f"{DOCUMENT_MISSING}: {name}")
            if sha256_file(path) != entry.get("document_sha256"):
                return _broken(n, f"{DOCUMENT_CHANGED}: {name} (SHA-256 differs from the logged hash)")

        expected_prev = entry["entry_hash"]
    return {"intact": True}
