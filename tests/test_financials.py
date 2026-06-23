from __future__ import annotations

from datetime import date

import pytest

from bottom_up_corpus.financials import (
    build_period_summaries,
    normalized_rows,
    render_summary_html,
)
from tests.conftest import SAMPLE_FACTS


def _summaries(**kw):
    return build_period_summaries(SAMPLE_FACTS, company="Apple Inc.",
                                  company_current="Apple Inc.", **kw)


def test_groups_into_annual_and_quarterly_periods():
    s = _summaries()
    keys = {(x.fy, x.frequency) for x in s}
    assert keys == {(2023, "annual"), (2023, "quarterly")}


def test_quarterly_picks_three_month_duration():
    q3 = next(x for x in _summaries() if x.frequency == "quarterly")
    # 3-month revenue (89.5B), not the 9-month YTD (270B).
    assert q3.values["revenue"]["value"] == 89498000000


def test_annual_period_fields_and_publication_date():
    fy = next(x for x in _summaries() if x.frequency == "annual")
    assert fy.values["revenue"]["value"] == 383285000000
    assert fy.values["net_income"]["value"] == 96995000000
    assert fy.period_end == date(2023, 9, 30)
    # First report of the period defines the publication date (earliest filed).
    assert fy.publication_date == date(2023, 11, 1)
    assert fy.sec_form == "10-K"
    assert fy.accession == "acc-fy23"


def test_instant_restatement_latest_filed_wins():
    fy = next(x for x in _summaries() if x.frequency == "annual")
    # Assets was restated 2024-02-01 -> the later value is used, but the
    # publication date stays the original (earliest) filing.
    assert fy.values["assets"]["value"] == 352583000000
    assert fy.publication_date == date(2023, 11, 1)


def test_point_in_time_name_applied():
    s = build_period_summaries(SAMPLE_FACTS, company="Apple Inc.",
                               company_current="Apple Inc.",
                               name_for_date=lambda d: "Old Apple Computer Inc")
    assert all(x.company == "Old Apple Computer Inc" for x in s)


def test_since_year_filter():
    assert _summaries(since_year=2024) == []
    assert len(_summaries(since_year=2023)) == 2


def test_render_summary_html_has_metrics_and_pubdate():
    fy = next(x for x in _summaries() if x.frequency == "annual")
    html = render_summary_html(fy)
    assert "Revenue" in html and "383,285,000,000" in html
    assert "2023-11-01" in html  # publication date is surfaced
    assert "FY2023" in html


def test_normalized_rows():
    fy = next(x for x in _summaries() if x.frequency == "annual")
    rows = normalized_rows("0000320193", fy)
    assert all(r["cik"] == "0000320193" and r["fy"] == 2023 for r in rows)
    rev = next(r for r in rows if r["concept"] == "revenue")
    assert rev["value"] == 383285000000 and rev["publication_date"] == "2023-11-01"
    assert rev["kind"] == "reported"
    # Derived metrics are emitted alongside reported ones, flagged by kind.
    debt = next(r for r in rows if r["concept"] == "total_debt")
    assert debt["kind"] == "derived" and debt["unit"] == "USD"


def test_derived_aggregates():
    fy = next(x for x in _summaries() if x.frequency == "annual")
    d = fy.derived
    # Total debt sums noncurrent + current portion + commercial paper.
    assert d["total_debt"]["value"] == 95281000000 + 9822000000 + 5985000000
    # EBITDA = operating income + D&A.
    assert d["ebitda"]["value"] == 114301000000 + 11519000000
    # Net debt = total debt - cash - short-term investments.
    assert d["net_debt"]["value"] == 111088000000 - 29965000000 - 31590000000
    # Free cash flow = CFO - capex.
    assert d["free_cash_flow"]["value"] == 110543000000 - 10959000000


def test_derived_ratios():
    fy = next(x for x in _summaries() if x.frequency == "annual")
    d = fy.derived
    assert d["debt_to_equity"]["value"] == pytest.approx(111088000000 / 62146000000)
    assert d["debt_to_equity"]["unit"] == "x"
    assert d["net_debt_to_ebitda"]["value"] == pytest.approx(49533000000 / 125820000000)
    assert d["current_ratio"]["value"] == pytest.approx(143566000000 / 145308000000)
    assert d["ebitda_margin"]["value"] == pytest.approx(125820000000 / 383285000000 * 100)
    assert d["ebitda_margin"]["unit"] == "%"
    assert d["effective_tax_rate"]["value"] == pytest.approx(16741000000 / 113736000000 * 100)
    assert d["interest_coverage"]["value"] == pytest.approx(114301000000 / 3933000000)
    # Label must reflect the actual numerator (operating income), not EBIT.
    assert "EBIT" not in d["interest_coverage"]["label"]
    assert "income" in d["interest_coverage"]["label"]


def _full_inputs():
    # Minimal inputs that yield both stock/flow ratios and stock/stock + flow/flow ones.
    def v(x):
        return {"value": float(x), "unit": "USD", "label": ""}
    return {
        "revenue": v(100), "operating_income": v(20), "net_income": v(10),
        "equity": v(200), "assets": v(400), "cash": v(30),
        "long_term_debt": v(50), "dep_amort": v(5),
    }


def test_annual_only_ratios_suppressed_for_sub_annual_periods():
    # net_debt/EBITDA and asset turnover divide a balance-sheet stock by a flow,
    # so a quarterly value would be ~4x off -- they must not be emitted.
    from bottom_up_corpus.financials import compute_derived
    d = compute_derived(_full_inputs(), frequency="quarterly")
    assert "net_debt_to_ebitda" not in d
    assert "asset_turnover" not in d
    # Stock/stock and flow/flow ratios are still meaningful sub-annually.
    assert d["debt_to_equity"]["value"] == pytest.approx(50 / 200)
    assert d["ebitda_margin"]["value"] == pytest.approx(25 / 100 * 100)
    # Same gate applies to semi-annual periods.
    assert "net_debt_to_ebitda" not in compute_derived(_full_inputs(), frequency="semi-annual")


def test_annual_only_ratios_present_for_annual_periods():
    from bottom_up_corpus.financials import compute_derived
    d = compute_derived(_full_inputs(), frequency="annual")
    assert d["net_debt_to_ebitda"]["value"] == pytest.approx((50 - 30) / (20 + 5))
    assert d["asset_turnover"]["value"] == pytest.approx(100 / 400)
    # Default frequency is annual, preserving the previous call signature behavior.
    assert "net_debt_to_ebitda" in compute_derived(_full_inputs())


def test_reporting_currency_defaults_and_ties():
    from bottom_up_corpus.financials import reporting_currency
    assert reporting_currency({}) is None
    # Tie between two currencies breaks towards USD.
    assert reporting_currency({"a": [{"unit": "USD"}], "b": [{"unit": "EUR"}]}) == "USD"
    # Non-monetary units are not currencies.
    assert reporting_currency({"a": [{"unit": "USD/shares"}, {"unit": "shares"}]}) is None


def test_currency_filter_ignores_convenience_translation():
    from bottom_up_corpus.financials import (
        build_period_summaries,
        flatten_points,
        reporting_currency,
    )

    def dur(val, filed):
        return {"start": "2022-01-01", "end": "2022-12-31", "val": val,
                "accn": "a", "fy": 2022, "fp": "FY", "form": "20-F", "filed": filed}

    facts = {"facts": {"us-gaap": {
        "Revenues": {"label": "Revenue", "units": {
            "EUR": [dur(1000, "2023-01-01")],
            "USD": [dur(1100, "2023-06-01")],  # later-filed convenience translation
        }},
        "OperatingIncomeLoss": {"label": "OI", "units": {"EUR": [dur(200, "2023-01-01")]}},
        "NetIncomeLoss": {"label": "NI", "units": {"EUR": [dur(150, "2023-01-01")]}},
    }}}
    # EUR dominates (3 facts vs 1), so it is the reporting currency.
    assert reporting_currency(flatten_points(facts)) == "EUR"
    fy = next(x for x in build_period_summaries(facts, company="X", company_current="X")
              if x.frequency == "annual")
    # The later USD value must NOT win over the primary EUR fact (no currency mix).
    assert fy.values["revenue"]["value"] == 1000
    assert fy.values["revenue"]["unit"] == "EUR"
    assert fy.currency == "EUR"
    # Margins stay currency-invariant (EUR/EUR): net margin = 150/1000 = 15%.
    assert fy.derived["net_margin"]["value"] == pytest.approx(15.0)
    assert fy.derived["net_margin"]["unit"] == "%"  # ratio: no currency


def test_derived_rows_carry_reporting_currency():
    # A USD issuer's derived monetary rows stay USD; ratios stay %/x.
    fy = next(x for x in _summaries() if x.frequency == "annual")
    assert fy.currency == "USD"
    assert fy.derived["total_debt"]["unit"] == "USD"
    assert fy.derived["net_debt_to_ebitda"]["unit"] == "x"
    # normalized_rows surface the currency explicitly on every row.
    rows = normalized_rows("0000320193", fy)
    assert all(r["currency"] == "USD" for r in rows)


def test_compute_derived_relabels_units_for_foreign_currency():
    from bottom_up_corpus.financials import compute_derived
    vals = {
        "revenue": {"value": 100.0, "unit": "EUR", "label": ""},
        "operating_income": {"value": 20.0, "unit": "EUR", "label": ""},
        "equity": {"value": 200.0, "unit": "EUR", "label": ""},
        "long_term_debt": {"value": 50.0, "unit": "EUR", "label": ""},
        "shares_outstanding": {"value": 10.0, "unit": "shares", "label": ""},
    }
    d = compute_derived(vals, currency="EUR")
    assert d["total_debt"]["unit"] == "EUR"            # monetary -> currency
    assert d["book_value_per_share"]["unit"] == "EUR/shares"  # per-share -> ccy/shares
    assert d["debt_to_equity"]["unit"] == "x"          # ratio unchanged


def test_derived_omits_metrics_with_missing_inputs():
    # A bare period with no debt/EBITDA inputs yields no leverage metrics.
    from bottom_up_corpus.financials import compute_derived
    d = compute_derived({"revenue": {"value": 100.0, "unit": "USD", "label": "Revenue"}})
    assert "total_debt" not in d and "ebitda" not in d and "net_debt_to_ebitda" not in d


def test_derived_rendered_in_html():
    fy = next(x for x in _summaries() if x.frequency == "annual")
    html = render_summary_html(fy)
    assert "Derived metrics" in html
    assert "EBITDA" in html and "Net debt / EBITDA" in html
    assert "Total debt" in html
