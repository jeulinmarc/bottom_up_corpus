"""Tests for the EE Äriregister bulk CSV-join register (Tasks 1 & 2)."""
import re

import pytest

from bottom_up_corpus.financials import compute_derived
from bottom_up_corpus.registers.concepts_ee import map_ee_report
from bottom_up_corpus.registers.ee_csv import iter_ee_reports

ELEM_FIXTURE = "tests/fixtures/ee/ee_elements_slice.csv"
META_FIXTURE = "tests/fixtures/ee/ee_meta_slice.csv"


def _find(reports, report_id):
    return next(r for r in reports if r["report_id"] == report_id)


def _map_report(report_id):
    """Parse the fixtures via iter_ee_reports, then map one report to concepts."""
    reports = list(iter_ee_reports(ELEM_FIXTURE, META_FIXTURE))
    r = _find(reports, report_id)
    return map_ee_report(r["elements"], r["period_end"], r["registrikood"])


def _reason(mapped, key):
    """First recorded suppression reason for ``key``, or None."""
    return next((reason for k, reason in mapped["suppressed"] if k == key), None)


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


# ===========================================================================
# Task 2 — concept pack + NO-FALSE-DATA gate (concepts_ee.map_ee_report)
# ===========================================================================

def test_map_ee_shape_and_metadata():
    """map_ee_report returns the BE/FI sibling shape with company basis / EUR."""
    m = _map_report("3361693")
    assert set(m) == {"period_end", "basis", "currency", "values", "suppressed",
                      "unbalanced"}
    assert m["basis"] == "company"
    assert m["currency"] == "EUR"
    assert m["period_end"] == "2025-07-31"   # DD.MM.YYYY -> ISO from metadata


def test_map_ee_balance_sheet_values_3361693():
    """Report 3361693 (rk 10003666): balance-sheet anchors mapped to the cent,
    the liabilities-based leverage split is emitted, and the gate holds."""
    m = _map_report("3361693")
    assert m["unbalanced"] is False
    v = m["values"]
    assert v["assets"]["value"] == 5952649.0
    assert v["equity"]["value"] == 4007533.0
    assert v["short_term_debt"]["value"] == 1891693.0      # CurrentLiabilities
    assert v["long_term_debt"]["value"] == 53423.0         # NonCurrentLiabilities
    # Liabilities-based leverage: the debt keys carry their et-gaap tags.
    assert v["short_term_debt"]["tag"] == "et-gaap:CurrentLiabilities"
    assert v["long_term_debt"]["tag"] == "et-gaap:NonCurrentLiabilities"
    assert v["assets"]["tag"] == "et-gaap:Assets"
    assert v["assets"]["unit"] == "EUR"
    # Balance gate identity holds to the cent: Assets == Equity + CL + NCL.
    assert (v["equity"]["value"] + v["short_term_debt"]["value"]
            + v["long_term_debt"]["value"]) == v["assets"]["value"]


def test_map_ee_debt_to_equity_is_liabilities_based_3361693():
    """A liabilities-based debt_to_equity is derivable via the engine:
    total_debt = CurrentLiabilities + NonCurrentLiabilities, over equity ≈ 0.485."""
    m = _map_report("3361693")
    # Belt: the debt keys are emitted directly.
    assert "short_term_debt" in m["values"]
    assert "long_term_debt" in m["values"]
    # Suspenders: the engine turns them into total_debt + debt_to_equity.
    derived = compute_derived(m["values"], frequency="annual", currency="EUR")
    assert derived["total_debt"]["value"] == 1945116          # 1,891,693 + 53,423
    assert "debt_to_equity" in derived
    assert abs(derived["debt_to_equity"]["value"] - 0.4854) < 0.001


def test_map_ee_net_income_is_final_after_tax_3398045():
    """Report 3398045 (rk 10524187): net_income is TotalAnnualPeriodProfitLoss
    (the FINAL after-tax result, 2,637,345), DISTINCT from pretax
    (TotalProfitLossBeforeTax 2,919,396) and operating (TotalProfitLoss
    1,268,711).  Proves the net-income trap is avoided."""
    m = _map_report("3398045")
    v = m["values"]
    # net_income == the FINAL after-tax line, tagged as such.
    assert v["net_income"]["value"] == 2637345.0
    assert v["net_income"]["tag"] == "et-gaap:TotalAnnualPeriodProfitLoss"
    # The three P&L lines are three distinct keys with three distinct values.
    assert v["pretax_income"]["value"] == 2919396.0
    assert v["pretax_income"]["tag"] == "et-gaap:TotalProfitLossBeforeTax"
    assert v["operating_income"]["value"] == 1268711.0
    assert v["operating_income"]["tag"] == "et-gaap:TotalProfitLoss"
    # net_income is NEITHER pretax NOR operating — and specifically below pretax
    # (a real income-tax charge), never the larger pretax figure.
    assert v["net_income"]["value"] != v["pretax_income"]["value"]
    assert v["net_income"]["value"] != v["operating_income"]["value"]
    assert v["net_income"]["value"] < v["pretax_income"]["value"]


@pytest.mark.parametrize("report_id, expect_da", [
    ("3361693", 100735.0),
    ("3398045", 133047.0),
    ("3464392", 278681.0),
])
def test_map_ee_dep_amort_stored_positive(report_id, expect_da):
    """dep_amort is stored as a positive add-back (abs of the negative cost line)
    on all three real reports, so the engine's ebitda = operating + dep_amort holds."""
    m = _map_report(report_id)
    da = m["values"]["dep_amort"]
    assert da["value"] == expect_da
    assert da["value"] >= 0
    assert da["tag"] == "et-gaap:DepreciationAndImpairmentLossReversal"


@pytest.mark.parametrize("report_id", ["3361693", "3398045", "3464392"])
def test_map_ee_interest_coverage_suppressed(report_id):
    """interest_expense and interest_coverage are ALWAYS suppressed (the RIK bulk
    has no interest/borrowings element) — never emitted, always with a reason."""
    m = _map_report(report_id)
    assert "interest_expense" not in m["values"]
    assert "interest_coverage" not in m["values"]
    assert _reason(m, "interest_expense") is not None
    assert _reason(m, "interest_coverage") is not None
    # And the engine, lacking interest_expense, cannot fabricate coverage either.
    derived = compute_derived(m["values"], frequency="annual", currency="EUR")
    assert "interest_coverage" not in derived


def test_map_ee_synthetic_unbalanced():
    """Assets != Equity + CL + NCL beyond tol -> unbalanced, no values emitted."""
    elements = {
        "Assets": 1_000_000.0,
        "Equity": 500_000.0,
        "CurrentLiabilities": 100_000.0,
        "NonCurrentLiabilities": 50_000.0,   # sum 650k != 1,000k, diff >> tol(5k)
        "Revenue": 2_000_000.0,
    }
    m = map_ee_report(elements, "2025-12-31", "99999999")
    assert m["unbalanced"] is True
    assert m["values"] == {}
    assert _reason(m, "__all__") is not None


def test_map_ee_ngo_template_is_no_financials():
    """An NGO/non-profit template (no Assets/Equity; uses LiabilitiesAndNetAssets /
    NetSurplusDeficitForPeriod) is NOT mapped into the company schema:
    no-financials — empty values, unbalanced False, reason recorded."""
    ngo = {
        "LiabilitiesAndNetAssets": 480_000.0,
        "NetSurplusDeficitForPeriod": 12_500.0,
        "CurrentLiabilities": 80_000.0,
        "Revenue": 300_000.0,
    }
    m = map_ee_report(ngo, "2025-12-31", "80000000")
    assert m["unbalanced"] is False
    assert m["values"] == {}
    reason = _reason(m, "__all__")
    assert reason is not None
    assert "Assets" in reason or "no-financials" in reason.lower()
