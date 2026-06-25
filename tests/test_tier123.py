"""Tier 1-3 concept/metric additions: exact-arithmetic unit tests."""
from __future__ import annotations

import pytest

from bottom_up_corpus.financials import CONCEPTS_BY_KEY, compute_derived


def _v(x, unit="USD", tag=None):
    d = {"value": float(x), "unit": unit}
    if tag:
        d["tag"] = tag
    return d


def _inputs():
    return {
        "revenue": _v(100), "operating_income": _v(20), "net_income": _v(10),
        "equity": _v(200), "assets": _v(400),
        "cash": _v(30, tag="CashAndCashEquivalentsAtCarryingValue"),
        "long_term_debt": _v(50, tag="LongTermDebtNoncurrent"),
        "long_term_investments": _v(40), "income_tax": _v(5), "pretax_income": _v(15),
        "cfo": _v(25), "capex": _v(5), "receivables": _v(10), "inventory": _v(8),
        "payables": _v(6), "cost_of_revenue": _v(60), "shares_outstanding": _v(10, "shares"),
        "preferred_stock": _v(20), "dividends_paid": _v(4), "buybacks": _v(3),
    }


def test_net_debt_subtracts_long_term_investments():
    d = compute_derived(_inputs())
    # 50 debt - 30 cash - 0 STI - 40 LT investments = -20 (net cash)
    assert d["net_debt"]["value"] == -20
    assert d["net_cash"]["value"] == 20


def test_roic_and_invested_capital():
    d = compute_derived(_inputs())
    # NOPAT = 20 * (1 - 5/15) = 13.3333 ; invested = 50 + 200 + 0 NCI - 30 cash = 220
    assert d["nopat"]["value"] == pytest.approx(13.3333, abs=1e-3)
    assert d["invested_capital"]["value"] == 220
    assert d["roic"]["value"] == pytest.approx(13.3333 / 220 * 100, abs=1e-3)


def test_book_value_nets_out_preferred():
    d = compute_derived(_inputs())
    # common equity = 200 - 20 preferred = 180
    assert d["book_value_per_share"]["value"] == pytest.approx(18.0)
    assert d["tangible_book_value"]["value"] == 180  # no goodwill/intangibles here
    assert d["tangible_book_value_per_share"]["value"] == pytest.approx(18.0)


def test_roe_uses_net_income_to_common():
    vals = _inputs()
    vals["preferred_dividends"] = _v(2)
    d = compute_derived(vals)
    # (10 - 2 preferred dividends) / 200 = 4%
    assert d["roe"]["value"] == pytest.approx(4.0)


def test_working_capital_cycle_days():
    d = compute_derived(_inputs())
    assert d["dso"]["value"] == pytest.approx(10 / (100 / 365), abs=1e-3)
    assert d["dio"]["value"] == pytest.approx(8 / (60 / 365), abs=1e-3)
    assert d["dpo"]["value"] == pytest.approx(6 / (60 / 365), abs=1e-3)
    assert d["ccc"]["value"] == pytest.approx(d["dso"]["value"] + d["dio"]["value"] - d["dpo"]["value"])
    assert d["ccc"]["unit"] == "days"


def test_payout_and_cash_conversion():
    d = compute_derived(_inputs())
    assert d["dividend_payout"]["value"] == pytest.approx(40.0)        # 4/10
    assert d["total_payout"]["value"] == pytest.approx((4 + 3) / 20 * 100)  # (div+buyback)/FCF
    assert d["cash_conversion"]["value"] == pytest.approx(200.0)       # FCF 20 / NI 10
    assert d["capex_intensity"]["value"] == pytest.approx(5.0)
    assert d["cfo_to_debt"]["value"] == pytest.approx(0.5)
    assert d["fcf_to_debt"]["value"] == pytest.approx(0.4)


def test_cycle_metrics_annual_only():
    d = compute_derived(_inputs(), frequency="quarterly")
    for k in ("dso", "dio", "dpo", "ccc", "roic", "cfo_to_debt", "fcf_to_debt"):
        assert k not in d


def test_new_metrics_flagged_for_financials_not_dropped():
    d = compute_derived(_inputs(), is_financial=True)
    for k in ("net_cash", "roic", "ccc", "cfo_to_debt", "capex_intensity"):
        assert k in d and d[k]["sector_relevant"] is False
    # sector-neutral additions stay relevant
    assert d["dividend_payout"]["sector_relevant"] is True


def test_preferred_concept_prioritises_carrying_value():
    # Verified empirically: PreferredStockValue is par/0 for many filers; the
    # incl.-APIC carrying tag must take priority.
    assert CONCEPTS_BY_KEY["preferred_stock"].tags[0] == "PreferredStockIncludingAdditionalPaidInCapital"
    assert CONCEPTS_BY_KEY["preferred_stock"].tags[-1] == "PreferredStockValue"


def test_equity_is_parent_only():
    assert CONCEPTS_BY_KEY["equity"].tags == ("StockholdersEquity",)
    assert "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest" \
        in CONCEPTS_BY_KEY["equity_total"].tags
