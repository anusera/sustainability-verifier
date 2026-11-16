import hashlib
import json
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from backend import audit
from backend.audit import GENESIS_HASH, append, log_document_ingested, read_entries, verify_chain

REPO = Path(__file__).resolve().parent.parent
REAL_LOG = REPO / "data" / "audit_log.jsonl"
SRC_DOCS = REPO / "data" / "documents"
DOC_A = "tier1_declaration.txt"
DOC_B = "tier3_ginning_invoice.txt"


@pytest.fixture
def docs(tmp_path):
    d = tmp_path / "docs"
    shutil.copytree(SRC_DOCS, d)
    return d


@pytest.fixture
def log(tmp_path):
    return tmp_path / "audit.jsonl"


@pytest.fixture
def chain(log, docs):
    """A valid 5-entry chain with fixed timestamps."""
    ts = lambda n: f"2026-11-16T12:00:0{n}Z"
    log_document_ingested("tshirt-001", docs / DOC_A, log_path=log, timestamp=ts(1))
    log_document_ingested("tshirt-001", docs / DOC_B, log_path=log, timestamp=ts(2))
    append({"product_id": "tshirt-001", "event_type": "facts_extracted", "details": {"facts": 4}},
           log_path=log, timestamp=ts(3))
    append({"product_id": "tshirt-001", "event_type": "rules_run", "details": {"failed": 2}},
           log_path=log, timestamp=ts(4))
    append({"product_id": "tshirt-001", "event_type": "verdict_changed", "details": {"verdict": "Contradicted"}},
           log_path=log, timestamp=ts(5))
    return log


def lines_of(log):
    return log.read_text(encoding="utf-8").splitlines()


def write_lines(log, lines):
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")


def verify(chain, docs):
    return verify_chain(chain, docs)


def test_valid_chain_is_intact(chain, docs):
    assert verify(chain, docs) == {"intact": True}


def test_first_entry_has_genesis_prev_hash_and_ids_increase(chain):
    entries = read_entries(chain)
    assert entries[0]["prev_hash"] == "0" * 64 == GENESIS_HASH
    assert [e["entry_id"] for e in entries] == [1, 2, 3, 4, 5]
    for prev, cur in zip(entries, entries[1:]):
        assert cur["prev_hash"] == prev["entry_hash"]


def test_entry_hash_is_canonical_and_timestamp_verbatim(chain):
    entry = read_entries(chain)[2]
    assert entry["timestamp"] == "2026-11-16T12:00:03Z"
    body = {k: v for k, v in entry.items() if k != "entry_hash"}
    raw = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    assert entry["entry_hash"] == hashlib.sha256(raw).hexdigest()


def test_document_ingested_fields(chain, docs):
    e = read_entries(chain)[0]
    assert e["event_type"] == "document_ingested"
    assert e["details"] == {"file_name": DOC_A}
    assert e["document_sha256"] == hashlib.sha256((docs / DOC_A).read_bytes()).hexdigest()
    assert read_entries(chain)[2]["document_sha256"] is None


def test_hashing_is_binary_so_line_endings_matter(tmp_path):
    f = tmp_path / "crlf.txt"
    f.write_bytes(b"line one\r\nline two\r\n")
    assert audit.sha256_file(f) == hashlib.sha256(b"line one\r\nline two\r\n").hexdigest()
    assert audit.sha256_file(f) != hashlib.sha256(b"line one\nline two\n").hexdigest()


def test_edited_details_breaks_at_that_entry(chain, docs):
    lines = lines_of(chain)
    entry = json.loads(lines[2])
    entry["details"]["facts"] = 99
    lines[2] = json.dumps(entry)
    write_lines(chain, lines)
    r = verify(chain, docs)
    assert r["intact"] is False and r["first_broken_entry"] == 3
    assert r["reason"].startswith("entry hash mismatch")


def test_deleted_middle_line_breaks_at_next_entry(chain, docs):
    lines = lines_of(chain)
    del lines[2]
    write_lines(chain, lines)
    r = verify(chain, docs)
    assert r["intact"] is False and r["first_broken_entry"] == 4
    assert r["reason"].startswith("prev_hash link broken")


def test_swapped_lines_break(chain, docs):
    lines = lines_of(chain)
    lines[2], lines[3] = lines[3], lines[2]
    write_lines(chain, lines)
    r = verify(chain, docs)
    assert r["intact"] is False and r["first_broken_entry"] == 4  # stored id of the line now in position 3
    assert r["reason"].startswith("prev_hash link broken")


def test_edited_document_is_caught(chain, docs):
    with open(docs / DOC_B, "ab") as f:
        f.write(b"\nTampered.")
    r = verify(chain, docs)
    assert r["intact"] is False and r["first_broken_entry"] == 2
    assert r["reason"].startswith(f"document changed: {DOC_B}")


def test_missing_document_is_caught(chain, docs):
    (docs / DOC_A).unlink()
    r = verify(chain, docs)
    assert r["intact"] is False and r["first_broken_entry"] == 1
    assert r["reason"].startswith(f"document missing: {DOC_A}")


def test_append_validation(log):
    with pytest.raises(ValueError):
        append({"product_id": "p", "event_type": "not_an_event"}, log_path=log)
    with pytest.raises(ValueError):
        append({"product_id": "p", "event_type": "rules_run", "entry_hash": "x"}, log_path=log)
    with pytest.raises(ValueError):
        append({"product_id": "p"}, log_path=log)
    assert not log.exists()


def test_concurrent_appends_keep_chain_intact(log, docs):
    def work(i):
        return append({"product_id": "tshirt-001", "event_type": "rules_run", "details": {"i": i}}, log_path=log)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(work, range(20)))
    assert [e["entry_id"] for e in read_entries(log)] == list(range(1, 21))
    assert verify_chain(log, docs) == {"intact": True}


def test_path_in_file_name_is_not_followed(log, docs, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    for name in ("../outside.txt", "sub\\x.txt", str(outside)):
        if log.exists():
            log.unlink()
        append({"product_id": "p", "event_type": "document_ingested", "details": {"file_name": name},
                "document_sha256": audit.sha256_file(outside)}, log_path=log)
        r = verify_chain(log, docs)
        assert r["intact"] is False and r["first_broken_entry"] == 1
        assert r["reason"].startswith(f"document missing: {name}")


def test_null_document_sha256_is_a_break_not_a_crash(log, docs):
    append({"product_id": "p", "event_type": "document_ingested", "details": {"file_name": DOC_A},
            "document_sha256": None}, log_path=log)
    r = verify_chain(log, docs)
    assert r["intact"] is False and r["first_broken_entry"] == 1
    assert r["reason"].startswith("document changed")


def test_trailing_blank_line_still_intact(chain, docs):
    with open(chain, "a", encoding="utf-8", newline="\n") as f:
        f.write("\n")
    assert verify(chain, docs) == {"intact": True}
    # append after a trailing blank line continues the chain
    e = append({"product_id": "tshirt-001", "event_type": "passport_published"}, log_path=chain)
    assert e["entry_id"] == 6
    assert verify(chain, docs) == {"intact": True}


def test_missing_or_empty_log_is_intact(log, docs):
    assert verify_chain(log, docs) == {"intact": True}
    log.write_text("", encoding="utf-8")
    assert verify_chain(log, docs) == {"intact": True}


def test_real_log_untouched(chain, docs, log):
    def snap():
        return REAL_LOG.read_bytes() if REAL_LOG.exists() else None

    before = snap()
    assert audit.DEFAULT_LOG_PATH == REAL_LOG
    assert chain != REAL_LOG and log != REAL_LOG
    append({"product_id": "tshirt-001", "event_type": "rules_run"}, log_path=chain)
    verify_chain(chain, docs)
    assert snap() == before
