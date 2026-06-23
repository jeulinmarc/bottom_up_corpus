"""Curated financial concepts + period grouping for SEC XBRL company facts.

The SEC `companyfacts` API returns hundreds of raw XBRL concepts. For the RAG we
distil a curated ~40 line items (income statement / balance sheet / cash flow /
per-share) and compute a block of **derived metrics** on top of them (total debt,
EBITDA, net debt, free cash flow, margins, returns, and leverage / coverage /
liquidity ratios -- see :func:`compute_derived`). Everything is grouped into
**one summary per reporting period as the company reports it** (annual /
quarterly / semi-annual), each carrying its publication (filing) date. The full
raw JSON is kept separately for exhaustivity.

Fact points carry: ``start``/``end`` (duration vs instant), ``val``, ``unit``,
``accn``, ``fy``, ``fp`` (``FY``/``Q1``-``Q3``), ``form``, ``filed`` (= the
publication date). Duration items (revenue, cash flow) are matched to the period
length; instant items (balance-sheet) are matched to the period end. Restatements
are resolved by taking the latest ``filed``.
"""

from __future__ import annotations

import html
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date

# A bare ISO-4217 currency unit (e.g. "USD", "EUR"), as opposed to composite or
# non-monetary XBRL units like "USD/shares", "shares" or "pure".
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")


@dataclass(frozen=True)
class Concept:
    """A curated line item, with fallback XBRL tags in priority order."""

    key: str
    label: str
    tags: tuple[str, ...]
    instant: bool          # True = balance-sheet (point-in-time); False = duration
    unit: str = "USD"      # expected unit (USD / USD/shares / shares)


# Curated set (~40 raw line items). Order = display order in the summary. These
# are the *reported* figures; ratios/aggregates (total debt, EBITDA, leverage,
# margins…) are computed from them in :func:`compute_derived`.
CONCEPTS: tuple[Concept, ...] = (
    # --- Income statement (duration) ---
    Concept("revenue", "Revenue",
            ("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues",
             "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet"), False),
    Concept("cost_of_revenue", "Cost of revenue",
            ("CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold"), False),
    Concept("gross_profit", "Gross profit", ("GrossProfit",), False),
    Concept("sga_expense", "SG&A expense",
            ("SellingGeneralAndAdministrativeExpense",
             "GeneralAndAdministrativeExpense"), False),
    Concept("rnd_expense", "R&D expense", ("ResearchAndDevelopmentExpense",), False),
    Concept("operating_income", "Operating income", ("OperatingIncomeLoss",), False),
    Concept("interest_expense", "Interest expense",
            ("InterestExpense", "InterestExpenseNonoperating", "InterestAndDebtExpense"), False),
    Concept("pretax_income", "Pretax income",
            ("IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
             "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
             "IncomeLossFromContinuingOperationsBeforeIncomeTaxesDomestic"), False),
    Concept("income_tax", "Income tax expense", ("IncomeTaxExpenseBenefit",), False),
    Concept("net_income", "Net income", ("NetIncomeLoss", "ProfitLoss"), False),
    # Depreciation & amortization (cash-flow statement; needed for EBITDA)
    Concept("dep_amort", "Depreciation & amortization",
            ("DepreciationDepletionAndAmortization", "DepreciationAmortizationAndAccretionNet",
             "DepreciationAndAmortization", "Depreciation"), False),
    # --- Per share (duration, USD/shares) ---
    Concept("eps_basic", "EPS (basic)", ("EarningsPerShareBasic",), False, "USD/shares"),
    Concept("eps_diluted", "EPS (diluted)", ("EarningsPerShareDiluted",), False, "USD/shares"),
    Concept("dividends_per_share", "Dividends declared per share",
            ("CommonStockDividendsPerShareDeclared",), False, "USD/shares"),
    # --- Cash flow (duration) ---
    Concept("cfo", "Cash from operations",
            ("NetCashProvidedByUsedInOperatingActivities",
             "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"), False),
    Concept("cfi", "Cash from investing",
            ("NetCashProvidedByUsedInInvestingActivities",), False),
    Concept("cff", "Cash from financing",
            ("NetCashProvidedByUsedInFinancingActivities",), False),
    Concept("capex", "Capital expenditures",
            ("PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets",
             "PaymentsForCapitalImprovements"), False),
    Concept("stock_comp", "Stock-based compensation", ("ShareBasedCompensation",), False),
    Concept("dividends_paid", "Dividends paid",
            ("PaymentsOfDividendsCommonStock", "PaymentsOfDividends"), False),
    Concept("buybacks", "Share repurchases", ("PaymentsForRepurchaseOfCommonStock",), False),
    # --- Balance sheet (instant) ---
    Concept("assets", "Total assets", ("Assets",), True),
    Concept("assets_current", "Current assets", ("AssetsCurrent",), True),
    Concept("cash", "Cash & equivalents",
            ("CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsAndShortTermInvestments"), True),
    Concept("short_term_investments", "Short-term investments",
            ("ShortTermInvestments", "MarketableSecuritiesCurrent"), True),
    Concept("receivables", "Accounts receivable",
            ("AccountsReceivableNetCurrent", "ReceivablesNetCurrent"), True),
    Concept("inventory", "Inventory", ("InventoryNet",), True),
    Concept("ppe_net", "Property, plant & equipment (net)",
            ("PropertyPlantAndEquipmentNet",), True),
    Concept("goodwill", "Goodwill", ("Goodwill",), True),
    Concept("intangibles", "Intangible assets (ex-goodwill)",
            ("IntangibleAssetsNetExcludingGoodwill", "FiniteLivedIntangibleAssetsNet"), True),
    Concept("liabilities", "Total liabilities", ("Liabilities",), True),
    Concept("liabilities_current", "Current liabilities", ("LiabilitiesCurrent",), True),
    Concept("payables", "Accounts payable",
            ("AccountsPayableCurrent", "AccountsPayableAndAccruedLiabilitiesCurrent"), True),
    # Debt components (needed for total debt / leverage). Long-term debt is
    # anchored on the noncurrent tag so it does not overlap the current portion.
    Concept("long_term_debt", "Long-term debt (noncurrent)",
            ("LongTermDebtNoncurrent", "LongTermDebt"), True),
    Concept("lt_debt_current", "Long-term debt (current portion)",
            ("LongTermDebtCurrent",), True),
    Concept("short_term_debt", "Short-term borrowings",
            ("ShortTermBorrowings", "CommercialPaper", "NotesPayableCurrent", "DebtCurrent"), True),
    # Lease liabilities (for adjusted / lease-inclusive leverage, post ASC 842)
    Concept("finance_lease_current", "Finance lease liability (current)",
            ("FinanceLeaseLiabilityCurrent",), True),
    Concept("finance_lease_noncurrent", "Finance lease liability (noncurrent)",
            ("FinanceLeaseLiabilityNoncurrent",), True),
    Concept("operating_lease_current", "Operating lease liability (current)",
            ("OperatingLeaseLiabilityCurrent",), True),
    Concept("operating_lease_noncurrent", "Operating lease liability (noncurrent)",
            ("OperatingLeaseLiabilityNoncurrent",), True),
    Concept("equity", "Stockholders' equity",
            ("StockholdersEquity",
             "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"), True),
    # --- Shares (instant, shares) ---
    Concept("shares_outstanding", "Shares outstanding",
            ("CommonStockSharesOutstanding", "EntityCommonStockSharesOutstanding"), True, "shares"),
    Concept("wavg_shares_diluted", "Weighted-avg diluted shares",
            ("WeightedAverageNumberOfDilutedSharesOutstanding",), False, "shares"),
)

CONCEPTS_BY_KEY = {c.key: c for c in CONCEPTS}


@dataclass(frozen=True)
class Derived:
    """A computed metric (ratio or aggregate) derived from reported concepts."""

    key: str
    label: str
    unit: str  # USD | USD/shares | x (multiple) | % (percent)
    annual_only: bool = False  # ratio mixing a balance-sheet stock with a flow


# Display order for the derived block. The aggregates (total debt, EBITDA, net
# debt, FCF) are the inputs the user explicitly asked for; the ratios build on
# them. Each is emitted only when every input concept is present for the period.
DERIVED: tuple[Derived, ...] = (
    # Aggregates
    Derived("total_debt", "Total debt", "USD"),
    Derived("total_debt_incl_leases", "Total debt incl. leases", "USD"),
    Derived("net_debt", "Net debt", "USD"),
    Derived("ebitda", "EBITDA", "USD"),
    Derived("free_cash_flow", "Free cash flow", "USD"),
    Derived("working_capital", "Working capital", "USD"),
    Derived("tangible_book_value", "Tangible book value", "USD"),
    # Margins (%)
    Derived("gross_margin", "Gross margin", "%"),
    Derived("operating_margin", "Operating margin", "%"),
    Derived("net_margin", "Net margin", "%"),
    Derived("ebitda_margin", "EBITDA margin", "%"),
    Derived("fcf_margin", "FCF margin", "%"),
    # Returns (%) — period-scoped (not annualised for quarters)
    Derived("roe", "Return on equity", "%"),
    Derived("roa", "Return on assets", "%"),
    Derived("effective_tax_rate", "Effective tax rate", "%"),
    # Leverage / coverage (multiples)
    Derived("debt_to_equity", "Debt / equity", "x"),
    Derived("debt_to_assets", "Debt / assets", "x"),
    Derived("net_debt_to_ebitda", "Net debt / EBITDA", "x", annual_only=True),
    Derived("interest_coverage", "Interest coverage (op. income / interest)", "x"),
    # Liquidity (multiples)
    Derived("current_ratio", "Current ratio", "x"),
    Derived("quick_ratio", "Quick ratio", "x"),
    Derived("cash_ratio", "Cash ratio", "x"),
    # Efficiency / per share
    Derived("asset_turnover", "Asset turnover", "x", annual_only=True),
    Derived("book_value_per_share", "Book value per share", "USD/shares"),
)

DERIVED_BY_KEY = {d.key: d for d in DERIVED}


def _num(values: dict, key: str) -> float | None:
    """Numeric reported value for ``key``, or None if absent/non-numeric."""
    v = values.get(key)
    if v is not None and isinstance(v.get("value"), (int, float)):
        return float(v["value"])
    return None


def compute_derived(values: dict[str, dict], frequency: str = "annual") -> dict[str, dict]:
    """Compute ratios/aggregates from reported concept ``values``.

    Each metric is emitted only when all of its required inputs are present, so
    a missing component is never silently treated as zero -- the exceptions are
    additive *components* (current debt, short-term investments, leases, ...)
    that default to 0 when untagged, which matches how filers report them.
    Margins, returns and the tax rate are expressed in percent; leverage,
    coverage and liquidity ratios as multiples (``x``). Returns are period-scoped
    (a quarterly summary's ROE is the quarter's, not annualised).

    ``frequency`` is the period's reporting frequency (``annual`` |
    ``quarterly`` | ``semi-annual``). Ratios that divide a balance-sheet *stock*
    (an instant, e.g. net debt or total assets) by an income/cash-flow *flow*
    over the period (``annual_only`` metrics) have no meaningful sub-annual value
    -- a quarterly net-debt/EBITDA would be ~4x the annual figure -- so they are
    emitted only for annual periods. (Computing a trailing-twelve-months variant
    would need the prior three quarters, which this single-period view lacks.)
    """
    out: dict[str, dict] = {}

    def put(key: str, val: float | None) -> None:
        if val is None or (isinstance(val, float) and val != val):  # skip None/NaN
            return
        d = DERIVED_BY_KEY[key]
        if d.annual_only and frequency != "annual":  # stock/flow ratio: annual only
            return
        out[key] = {"value": val, "unit": d.unit, "label": d.label}

    def div(a: float | None, b: float | None) -> float | None:
        if a is None or b is None or b == 0:
            return None
        return a / b

    def pct(a: float | None, b: float | None) -> float | None:
        r = div(a, b)
        return r * 100 if r is not None else None

    def opt(key: str) -> float:  # additive component: missing -> 0
        return _num(values, key) or 0.0

    rev = _num(values, "revenue")
    oi = _num(values, "operating_income")
    ni = _num(values, "net_income")
    eq = _num(values, "equity")
    assets = _num(values, "assets")
    ac, lc = _num(values, "assets_current"), _num(values, "liabilities_current")
    cash = _num(values, "cash")

    # Aggregates
    ltd = _num(values, "long_term_debt")
    total_debt = None
    if ltd is not None:
        total_debt = ltd + opt("lt_debt_current") + opt("short_term_debt")
    put("total_debt", total_debt)

    leases = (opt("finance_lease_current") + opt("finance_lease_noncurrent")
              + opt("operating_lease_current") + opt("operating_lease_noncurrent"))
    if total_debt is not None and leases:
        put("total_debt_incl_leases", total_debt + leases)

    net_debt = None
    if total_debt is not None and cash is not None:
        net_debt = total_debt - cash - opt("short_term_investments")
    put("net_debt", net_debt)

    da = _num(values, "dep_amort")
    ebitda = oi + da if (oi is not None and da is not None) else None
    put("ebitda", ebitda)

    cfo, capex = _num(values, "cfo"), _num(values, "capex")
    fcf = cfo - capex if (cfo is not None and capex is not None) else None
    put("free_cash_flow", fcf)

    if ac is not None and lc is not None:
        put("working_capital", ac - lc)
    if eq is not None:
        put("tangible_book_value", eq - opt("goodwill") - opt("intangibles"))

    # Margins (%)
    put("gross_margin", pct(_num(values, "gross_profit"), rev))
    put("operating_margin", pct(oi, rev))
    put("net_margin", pct(ni, rev))
    put("ebitda_margin", pct(ebitda, rev))
    put("fcf_margin", pct(fcf, rev))

    # Returns / tax (%)
    put("roe", pct(ni, eq))
    put("roa", pct(ni, assets))
    put("effective_tax_rate", pct(_num(values, "income_tax"), _num(values, "pretax_income")))

    # Leverage / coverage (x)
    put("debt_to_equity", div(total_debt, eq))
    put("debt_to_assets", div(total_debt, assets))
    put("net_debt_to_ebitda", div(net_debt, ebitda))
    put("interest_coverage", div(oi, _num(values, "interest_expense")))

    # Liquidity (x)
    put("current_ratio", div(ac, lc))
    if ac is not None:
        put("quick_ratio", div(ac - opt("inventory"), lc))
    if cash is not None:
        put("cash_ratio", div(cash + opt("short_term_investments"), lc))

    # Efficiency / per share
    put("asset_turnover", div(rev, assets))
    put("book_value_per_share", div(eq, _num(values, "shares_outstanding")))

    return out


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

    @property
    def derived(self) -> dict[str, dict]:
        """Computed ratios/aggregates (total debt, EBITDA, leverage, ...)."""
        return compute_derived(self.values, self.frequency)


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


def reporting_currency(flat: dict[str, list[dict]]) -> str | None:
    """The issuer's dominant monetary currency (most frequent currency unit).

    A filer reports its monetary facts in a single functional currency, but the
    feed can also carry convenience translations (e.g. a USD value alongside the
    primary EUR one). Ties break towards USD. Returns None if there are no
    monetary facts at all.
    """
    counts: Counter[str] = Counter()
    for points in flat.values():
        for p in points:
            unit = p.get("unit", "")
            if _CURRENCY_RE.match(unit):
                counts[unit] += 1
    if not counts:
        return None
    return max(counts, key=lambda u: (counts[u], u == "USD"))


def _currency_filtered(points: list[dict], concept: Concept, currency: str | None) -> list[dict]:
    """Drop monetary points not in the issuer's reporting currency.

    Without this, a EUR fact (or a stray convenience translation) would be summed
    and divided alongside USD facts as if it were USD. Non-monetary concepts
    (per-share, share counts) and currency-less feeds pass through untouched.
    """
    if currency is None or concept.unit != "USD":
        return points
    return [p for p in points if p.get("unit") == currency]


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
    currency = reporting_currency(flat)

    # Duration facts -> (period_end, frequency) -> concept -> [points]
    duration: dict[tuple[date, str], dict[str, list[dict]]] = {}
    # Instant facts -> period_end -> concept -> [points]
    instant: dict[date, dict[str, list[dict]]] = {}
    freqs_seen: set[str] = set()

    for concept in CONCEPTS:
        for p in _currency_filtered(_points_for(concept, flat), concept, currency):
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
    if unit == "%":
        return f"{value:,.1f}%"
    if unit == "x":
        return f"{value:,.2f}x"
    return f"{value:,.0f}"


def _metric_table(items, caption: str) -> str:
    rows = "\n".join(
        f"<tr><td>{html.escape(v['label'])}</td>"
        f"<td style='text-align:right'>{_fmt(v['value'], v['unit'])}</td>"
        f"<td>{html.escape(v['unit'])}</td></tr>"
        for v in items
    )
    return (
        f"<h2>{html.escape(caption)}</h2>"
        f"<table border='1' cellpadding='4' cellspacing='0'>"
        f"<thead><tr><th>Metric</th><th>Value</th><th>Unit</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
    )


def render_summary_html(summary: PeriodSummary) -> str:
    """Render a period summary as a small standalone HTML document.

    Two tables: the *reported* line items and the *derived* metrics (total debt,
    EBITDA, leverage and other ratios computed from them).
    """
    pub = summary.publication_date.isoformat() if summary.publication_date else "n/a"
    end = summary.period_end.isoformat() if summary.period_end else "n/a"
    title = f"{summary.company} — {summary.period_label} financial summary"
    reported = _metric_table(summary.values.values(), "Reported figures")
    derived = summary.derived
    derived_tbl = (
        _metric_table(
            (derived[k] for k in DERIVED_BY_KEY if k in derived),
            "Derived metrics (computed)",
        )
        if derived else ""
    )
    return (
        f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>{html.escape(title)}</title></head>"
        f"<body><h1>{html.escape(title)}</h1>"
        f"<p>Issuer: {html.escape(summary.company)} "
        f"(current: {html.escape(summary.company_current)})<br>"
        f"Period: {html.escape(summary.period_label)} ({summary.frequency}), ending {end}<br>"
        f"<b>Publication date (filed): {pub}</b><br>"
        f"Source form: {html.escape(summary.sec_form)} — accession {html.escape(summary.accession)}<br>"
        f"Data: SEC XBRL company facts (us-gaap)</p>"
        f"{reported}{derived_tbl}</body></html>"
    )


def normalized_rows(cik: str, summary: PeriodSummary) -> list[dict]:
    """Flatten a period summary into queryable rows for data/financials/<cik>.jsonl.

    Emits both ``kind="reported"`` rows (raw XBRL concepts) and ``kind="derived"``
    rows (computed ratios/aggregates), so leverage, EBITDA, total debt, etc. are
    queryable alongside the line items they came from.
    """
    base = {
        "cik": cik, "fy": summary.fy, "frequency": summary.frequency,
        "period_end": summary.period_end.isoformat() if summary.period_end else None,
        "publication_date": summary.publication_date.isoformat() if summary.publication_date else None,
        "sec_form": summary.sec_form, "accession": summary.accession,
    }
    rows = [
        {**base, "kind": "reported", "concept": key,
         "label": v["label"], "value": v["value"], "unit": v["unit"]}
        for key, v in summary.values.items()
    ]
    rows += [
        {**base, "kind": "derived", "concept": key,
         "label": v["label"], "value": v["value"], "unit": v["unit"]}
        for key, v in summary.derived.items()
    ]
    return rows
