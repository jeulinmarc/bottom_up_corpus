"""Tests for the EE Äriregister bulk CSV-join register (Task 1)."""
import re

import pytest

from bottom_up_corpus.registers.ee_csv import iter_ee_reports

ELEM_FIXTURE = "tests/fixtures/ee/ee_elements_slice.csv"
META_FIXTURE = "tests/fixtures/ee/ee_meta_slice.csv"


def _find(reports, report_id):
    return next(r for r in reports if r["report_id"] == report_id)


def test_iter_ee_reports_count():
    """Iterating both fixture slices yields exactly 3 report dicts."""
    reports = list(iter_ee_reports(ELEM_FIXTURE, META_FIXTURE))
    assert len(reports) == 3


def test_iter_ee_reports_registrikood():
    """Report 3361693 is joined to registrikood 10003666 from metadata."""
    reports = list(iter_ee_reports(ELEM_FIXTURE, META_FIXTURE))
    r = _find(reports, "3361693")
    assert r["registrikood"] == "10003666"


def test_iter_ee_reports_balance_sheet_elements():
    """Report 3361693 exposes the correct standalone balance-sheet values."""
    reports = list(iter_ee_reports(ELEM_FIXTURE, META_FIXTURE))
    elems = _find(reports, "3361693")["elements"]
    assert elems["Assets"] == 5952649.0
    assert elems["Equity"] == 4007533.0
    assert elems["CurrentLiabilities"] == 1891693.0
    assert elems["NonCurrentLiabilities"] == 53423.0


def test_iter_ee_reports_period_end_iso():
    """period_end from metadata is converted DD.MM.YYYY → YYYY-MM-DD (ISO)."""
    reports = list(iter_ee_reports(ELEM_FIXTURE, META_FIXTURE))
    r = _find(reports, "3361693")
    assert r["period_end"] is not None
    assert re.match(r"^\d{4}-\d{2}-\d{2}$", r["period_end"]), (
        f"period_end not ISO: {r['period_end']!r}"
    )


def test_iter_ee_reports_no_consolidated_keys():
    """No element key ending in 'Consolidated' is emitted (standalone-only)."""
    reports = list(iter_ee_reports(ELEM_FIXTURE, META_FIXTURE))
    for report in reports:
        bad = [k for k in report["elements"] if k.endswith("Consolidated")]
        assert bad == [], f"report {report['report_id']} has Consolidated keys: {bad}"
