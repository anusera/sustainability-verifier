from datetime import date

from backend.rules import cert_validity, origin_match, run_rules

from .conftest import by_id


def test_origin_fails_on_bangladesh(facts):
    c = origin_match("India", facts)
    assert c.result == "fail"
    assert c.evidence[0] == "f-t3-1"
    assert "Bangladesh" in c.detail


def test_origin_lists_matching_and_skips_bom(facts):
    c = origin_match("India", facts)
    assert c.evidence == ["f-t3-1", "f-t1-1", "f-t2-1"]
    assert not any(e.startswith("f-bom") for e in c.evidence)


def test_origin_passes_and_ignores_case_whitespace(facts):
    facts = [f for f in facts if f.fact_id != "f-t3-1"]
    facts = [f.model_copy(update={"origin_country": " india "}) if f.fact_id == "f-t1-1" else f for f in facts]
    c = origin_match("India", facts)
    assert c.result == "pass"


def test_cert_fails_against_invoice_and_today(facts, today):
    c = cert_validity(facts, today)
    assert c.result == "fail"
    assert "2026-05-02" in c.detail and "today 2026-11-16" in c.detail
    assert c.evidence[0] == "f-t2-1"
    assert "f-t3-1" in c.evidence


def test_cert_fails_against_invoice_only(facts):
    c = cert_validity(facts, date(2026, 3, 1))
    assert c.result == "fail"
    assert "2026-05-02" in c.detail
    assert "today" not in c.detail


def test_cert_fails_against_today_only(facts, today):
    facts = [f for f in facts if f.doc_id in ("tier2_gots_certificate", "tier3_ginning_invoice")]
    facts = [f.model_copy(update={"document_date": "2026-02-01"}) if f.fact_id == "f-t3-1" else f for f in facts]
    c = cert_validity(facts, today)
    assert c.result == "fail"
    assert "today 2026-11-16" in c.detail
    assert "document_date" not in c.detail


def test_cert_passes_when_valid(facts, today):
    facts = [by_id(facts, "f-t2-1").model_copy(update={"valid_to": "2027-12-31"}), by_id(facts, "f-t3-1")]
    assert cert_validity(facts, today).result == "pass"


def test_cert_skips_null_document_dates(facts):
    assert by_id(facts, "f-t2-1").document_date is None
    c = cert_validity([by_id(facts, "f-t2-1")], date(2026, 1, 1))
    assert c.result == "pass"


def test_cert_no_certificates(facts, today):
    c = cert_validity([f for f in facts if not f.valid_to], today)
    assert c.result == "pass" and c.detail == "No certificates found."


def test_checks_have_version_and_result(facts, today):
    for c in run_rules({"claimed_origin": "India"}, facts, today):
        assert c.rules_version == "rules-v1.0"
        assert c.result in ("pass", "fail")
