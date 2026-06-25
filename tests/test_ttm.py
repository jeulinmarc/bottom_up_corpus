from __future__ import annotations

from datetime import date

import pytest

from bottom_up_corpus.financials import (
    FlowSeries,
    _build_flow_series,
    _standalone_quarter,
    _ttm_flow,
    flatten_points,
)


def _dur(start, end, val, filed, fp, form):
    return {"start": start, "end": end, "val": val, "accn": "a",
            "fy": 2025, "fp": fp, "form": form, "filed": filed}


# Apple net income: 3-month standalone quarters + the FY25 annual + the 9-month
# YTD ending 2025-06-28 (so the unreported FY-end quarter can be derived).
NI_FACTS = {"facts": {"us-gaap": {"NetIncomeLoss": {"label": "NI", "units": {"USD": [
    _dur("2024-09-29", "2024-12-28", 36330000000, "2025-01-30", "Q1", "10-Q"),
    _dur("2024-12-29", "2025-03-29", 24780000000, "2025-05-01", "Q2", "10-Q"),
    _dur("2025-03-30", "2025-06-28", 23434000000, "2025-08-01", "Q3", "10-Q"),
    _dur("2024-09-29", "2025-06-28", 84544000000, "2025-08-01", "Q3", "10-Q"),  # 9M YTD
    _dur("2024-09-29", "2025-09-27", 112010000000, "2025-11-01", "FY", "10-K"),  # FY25
    _dur("2025-09-28", "2025-12-27", 42097000000, "2026-01-29", "Q1", "10-Q"),
    _dur("2025-12-28", "2026-03-28", 29578000000, "2026-05-01", "Q2", "10-Q"),
]}}}}}


def test_build_flow_series_buckets_durations():
    s = _build_flow_series(flatten_points(NI_FACTS), "USD")
    assert s.quarterly["net_income"][date(2025, 12, 27)] == 42097000000
    assert s.annual["net_income"][date(2025, 9, 27)] == 112010000000
    assert s.ytd9["net_income"][date(2025, 6, 28)] == 84544000000


def test_standalone_quarter_derives_unreported_q4():
    s = _build_flow_series(flatten_points(NI_FACTS), "USD")
    # Direct 3-month quarter.
    assert _standalone_quarter(s, "net_income", date(2025, 12, 27)) == 42097000000
    # Fiscal year-end quarter is not reported standalone -> FY - 9M YTD.
    assert _standalone_quarter(s, "net_income", date(2025, 9, 27)) == 112010000000 - 84544000000


def test_ttm_flow_sums_trailing_four_quarters():
    s = _build_flow_series(flatten_points(NI_FACTS), "USD")
    # TTM net income to 2025-12-27 = Q(Dec25)+Q(Sep25 derived)+Q(Jun25)+Q(Mar25)
    ttm = _ttm_flow(s, "net_income", date(2025, 12, 27), "quarterly")
    assert ttm == 42097000000 + (112010000000 - 84544000000) + 23434000000 + 24780000000
    assert ttm == 117777000000


def test_ttm_flow_annual_uses_fy_value():
    s = _build_flow_series(flatten_points(NI_FACTS), "USD")
    assert _ttm_flow(s, "net_income", date(2025, 9, 27), "annual") == 112010000000
