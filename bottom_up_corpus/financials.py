"""Curated financial concepts + period grouping for SEC XBRL company facts.

The SEC `companyfacts` API returns hundreds of raw XBRL concepts. For the RAG we
distil a curated ~20 line items (income statement / balance sheet / cash flow /
per-share), grouped into **one summary per reporting period as the company
reports it** (annual / quarterly / semi-annual), each carrying its publication
(filing) date. The full raw JSON is kept separately for exhaustivity.

Fact points carry: ``start``/``end`` (duration vs instant), ``val``, ``unit``,
``accn``, ``fy``, ``fp`` (``FY``/``Q1``-``Q3``), ``form``, ``filed`` (= the
publication date). Duration items (revenue, cash flow) are matched to the period
length; instant items (balance-sheet) are matched to the period end. Restatements
are resolved by taking the latest ``filed``.
"""

from __future__ import annotations

import html
from collections import Counter
from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True)
class Concept:
    """A curated line item, with fallback XBRL tags in priority order."""

    key: str
    label: str
    tags: tuple[str, ...]
    instant: bool          # True = balance-sheet (point-in-time); False = duration
    unit: str = "USD"      # expected unit (USD / USD/shares / shares)


# Curated set (~20). Order = display order in the summary.
CONCEPTS: tuple[Concept, ...] = (
    # Income statement (duration)
    Concept("revenue", "Revenue",
            ("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues",
             "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet"), False),
    Concept("cost_of_revenue", "Cost of revenue",
            ("CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold"), False),
    Concept("gross_profit", "Gross profit", ("GrossProfit",), False),
    Concept("operating_income", "Operating income", ("OperatingIncomeLoss",), False),
    Concept("rnd_expense", "R&D expense", ("ResearchAndDevelopmentExpense",), False),
    Concept("income_tax", "Income tax expense", ("IncomeTaxExpenseBenefit",), False),
    Concept("net_income", "Net income", ("NetIncomeLoss", "ProfitLoss"), False),
    # Per share (duration, USD/shares)
    Concept("eps_basic", "EPS (basic)", ("EarningsPerShareBasic",), False, "USD/shares"),
    Concept("eps_diluted", "EPS (diluted)", ("EarningsPerShareDiluted",), False, "USD/shares"),
    # Cash flow (duration)
    Concept("cfo", "Cash from operations",
            ("NetCashProvidedByUsedInOperatingActivities",
             "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"), False),
    Concept("cfi", "Cash from investing",
            ("NetCashProvidedByUsedInInvestingActivities",), False),
    Concept("cff", "Cash from financing",
            ("NetCashProvidedByUsedInFinancingActivities",), False),
    # Balance sheet (instant)
    Concept("assets", "Total assets", ("Assets",), True),
    Concept("assets_current", "Current assets", ("AssetsCurrent",), True),
    Concept("liabilities", "Total liabilities", ("Liabilities",), True),
    Concept("liabilities_current", "Current liabilities", ("LiabilitiesCurrent",), True),
    Concept("equity", "Stockholders' equity",
            ("StockholdersEquity",
             "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"), True),
    Concept("cash", "Cash & equivalents",
            ("CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsAndShortTermInvestments"), True),
    Concept("long_term_debt", "Long-term debt",
            ("LongTermDebtNoncurrent", "LongTermDebt"), True),
    # Shares (instant, shares)
    Concept("shares_outstanding", "Shares outstanding",
            ("CommonStockSharesOutstanding", "EntityCommonStockSharesOutstanding"), True, "shares"),
)

CONCEPTS_BY_KEY = {c.key: c for c in CONCEPTS}


@dataclass
class PeriodSummary:
    """Curated financials for one reporting period of one issuer.

    A period is identified by its own **period-end date** and **frequency** (NOT
    the reporting filing's fiscal-year/period, which also tags prior-year
    comparatives). ``publication_date`` is when the period was first reported;
    values use the latest-filed figure (restatements win).
    """

    period_end: date | None
    frequency: str                 # annual | quarterly | semi-annual
    publication_date: date | None  # earliest `filed` for this period (first published)
    sec_form: str                  # form of the first report (10-K / 10-Q / ...)
    accession: str
    company: str                   # point-in-time name
    company_current: str
    # key -> {"value", "unit", "label"}
    values: dict[str, dict] = field(default_factory=dict)

    @property
    def fy(self) -> int | None:
        return self.period_end.year if self.period_end else None

    @property
    def period_label(self) -> str:
        end = self.period_end.isoformat() if self.period_end else "n/a"
        if self.frequency == "annual":
            return f"FY{self.fy} (ended {end})"
        if self.frequency == "semi-annual":
            return f"Half-year ended {end}"
        return f"Quarter ended {end}"


def _to_date(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:  # pragma: no cover
        return None


def flatten_points(facts: dict) -> dict[str, list[dict]]:
    """Flatten companyfacts JSON to ``{tag: [point + unit/tag/label]}``."""
    out: dict[str, list[dict]] = {}
    namespaces = facts.get("facts", {})
    for ns in ("us-gaap", "dei", "ifrs-full"):
        for tag, body in namespaces.get(ns, {}).items():
            points: list[dict] = []
            for unit, arr in body.get("units", {}).items():
                for p in arr:
                    q = dict(p)
                    q["unit"] = unit
                    q["tag"] = tag
                    q["label"] = body.get("label") or tag
                    points.append(q)
            if points:
                out[tag] = points
    return out


def _points_for(concept: Concept, flat: dict[str, list[dict]]) -> list[dict]:
    """Points for the first fallback tag that has data."""
    for tag in concept.tags:
        if tag in flat:
            return flat[tag]
    return []


def _classify_frequency(days: int) -> str | None:
    """Map a duration (days) to a reporting frequency, or None if non-standard.

    52/53-week fiscal years land ~364–371 days. YTD cumulatives (6/9-month) are
    deliberately excluded for quarterly filers (handled by the caller)."""
    if 330 <= days <= 400:
        return "annual"
    if 160 <= days <= 200:
        return "semi-annual"
    if 80 <= days <= 100:
        return "quarterly"
    return None


def _latest_filed(points: list[dict]) -> dict:
    return max(points, key=lambda p: p.get("filed", ""))


def build_period_summaries(
    facts: dict,
    *,
    company: str,
    company_current: str,
    name_for_date=None,
    since_year: int | None = None,
) -> list[PeriodSummary]:
    """Group curated concepts into one summary per actual reporting period.

    Periods are keyed by the value's own **period end + frequency** (derived from
    each fact's ``start``/``end``), so prior-year comparatives carried in a filing
    land in their own period rather than the report's fiscal year. Duration facts
    define the periods; instant (balance-sheet) facts attach by matching end date.
    ``name_for_date(d)`` optionally supplies the point-in-time issuer name.
    """
    flat = flatten_points(facts)

    # Duration facts -> (period_end, frequency) -> concept -> [points]
    duration: dict[tuple[date, str], dict[str, list[dict]]] = {}
    # Instant facts -> period_end -> concept -> [points]
    instant: dict[date, dict[str, list[dict]]] = {}
    freqs_seen: set[str] = set()

    for concept in CONCEPTS:
        for p in _points_for(concept, flat):
            end = _to_date(p.get("end"))
            if not end:
                continue
            if concept.instant:
                instant.setdefault(end, {}).setdefault(concept.key, []).append(p)
            else:
                start = _to_date(p.get("start"))
                if not start:
                    continue
                freq = _classify_frequency((end - start).days)
                if not freq:
                    continue
                freqs_seen.add(freq)
                duration.setdefault((end, freq), {}).setdefault(concept.key, []).append(p)

    # A quarterly filer's 6-month YTD points are not a separate reporting period.
    allowed = {"annual"}
    if "quarterly" in freqs_seen:
        allowed.add("quarterly")
    elif "semi-annual" in freqs_seen:
        allowed.add("semi-annual")

    summaries: list[PeriodSummary] = []
    for (end, freq), per_concept in duration.items():
        if freq not in allowed:
            continue
        if since_year is not None and end.year < since_year:
            continue

        values: dict[str, dict] = {}
        all_points: list[dict] = []
        for key, cands in per_concept.items():
            chosen = _latest_filed(cands)  # restatements win
            values[key] = {"value": chosen["val"], "unit": chosen.get("unit", CONCEPTS_BY_KEY[key].unit),
                           "label": CONCEPTS_BY_KEY[key].label}
            all_points.extend(cands)
        for key, cands in instant.get(end, {}).items():
            chosen = _latest_filed(cands)
            values[key] = {"value": chosen["val"], "unit": chosen.get("unit", CONCEPTS_BY_KEY[key].unit),
                           "label": CONCEPTS_BY_KEY[key].label}
            all_points.extend(cands)
        if not values:
            continue

        # First report of this period = earliest filed among its points.
        first = min(all_points, key=lambda p: p.get("filed", ""))
        pub = _to_date(first.get("filed"))
        pit = name_for_date(pub) if (name_for_date and pub) else company
        summaries.append(PeriodSummary(
            period_end=end, frequency=freq, publication_date=pub,
            sec_form=first.get("form") or "XBRL",
            accession=first.get("accn") or f"XBRL-{end.isoformat()}-{freq}",
            company=pit or company, company_current=company_current, values=values,
        ))

    summaries.sort(key=lambda s: (s.period_end or date.min, s.frequency), reverse=True)
    return summaries


def _fmt(value, unit: str) -> str:
    if not isinstance(value, (int, float)):
        return html.escape(str(value))
    if unit == "USD/shares":
        return f"{value:,.2f}"
    if unit == "shares":
        return f"{value:,.0f}"
    return f"{value:,.0f}"


def render_summary_html(summary: PeriodSummary) -> str:
    """Render a period summary as a small standalone HTML document."""
    rows = "\n".join(
        f"<tr><td>{html.escape(v['label'])}</td>"
        f"<td style='text-align:right'>{_fmt(v['value'], v['unit'])}</td>"
        f"<td>{html.escape(v['unit'])}</td></tr>"
        for v in summary.values.values()
    )
    pub = summary.publication_date.isoformat() if summary.publication_date else "n/a"
    end = summary.period_end.isoformat() if summary.period_end else "n/a"
    title = f"{summary.company} — {summary.period_label} financial summary"
    return (
        f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>{html.escape(title)}</title></head>"
        f"<body><h1>{html.escape(title)}</h1>"
        f"<p>Issuer: {html.escape(summary.company)} "
        f"(current: {html.escape(summary.company_current)})<br>"
        f"Period: {html.escape(summary.period_label)} ({summary.frequency}), ending {end}<br>"
        f"<b>Publication date (filed): {pub}</b><br>"
        f"Source form: {html.escape(summary.sec_form)} — accession {html.escape(summary.accession)}<br>"
        f"Data: SEC XBRL company facts (us-gaap)</p>"
        f"<table border='1' cellpadding='4' cellspacing='0'>"
        f"<thead><tr><th>Metric</th><th>Value</th><th>Unit</th></tr></thead>"
        f"<tbody>{rows}</tbody></table></body></html>"
    )


def normalized_rows(cik: str, summary: PeriodSummary) -> list[dict]:
    """Flatten a period summary into queryable rows for data/financials/<cik>.jsonl."""
    return [
        {
            "cik": cik, "fy": summary.fy, "frequency": summary.frequency,
            "period_end": summary.period_end.isoformat() if summary.period_end else None,
            "publication_date": summary.publication_date.isoformat() if summary.publication_date else None,
            "sec_form": summary.sec_form, "accession": summary.accession,
            "concept": key, "label": v["label"], "value": v["value"], "unit": v["unit"],
        }
        for key, v in summary.values.items()
    ]
