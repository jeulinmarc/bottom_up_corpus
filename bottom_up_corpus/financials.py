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
            ("InterestExpense", "InterestExpenseNonoperating", "InterestAndDebtExpense",
             "InterestExpenseOperating"), False),
    # Pretax income: the Domestic-only subtotal is deliberately excluded -- for a
    # multinational it would make effective_tax_rate = total tax / domestic pretax.
    Concept("pretax_income", "Pretax income",
            ("IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
             "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"), False),
    Concept("income_tax", "Income tax expense", ("IncomeTaxExpenseBenefit",), False),
    # NetIncomeLoss is attributable to the parent (after NCI); ProfitLoss is the
    # consolidated total. net_income_nci is the NCI portion (reconciliation only).
    Concept("net_income", "Net income", ("NetIncomeLoss", "ProfitLoss"), False),
    Concept("net_income_nci", "Net income attributable to NCI",
            ("NetIncomeLossAttributableToNoncontrollingInterest",), False),
    Concept("preferred_dividends", "Preferred dividends",
            ("PreferredStockDividendsAndOtherAdjustments",
             "PreferredStockDividendsIncomeStatementImpact"), False),
    # Depreciation & amortization (cash-flow statement; needed for EBITDA)
    Concept("dep_amort", "Depreciation & amortization",
            ("DepreciationDepletionAndAmortization", "DepreciationAmortizationAndAccretionNet",
             "DepreciationAndAmortization"), False),
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
    Concept("buybacks", "Share repurchases",
            ("PaymentsForRepurchaseOfCommonStock", "PaymentsForRepurchaseOfEquity"), False),
    Concept("acquisitions_net", "Acquisitions (net of cash acquired)",
            ("PaymentsToAcquireBusinessesNetOfCashAcquired", "PaymentsToAcquireBusinessesGross"), False),
    Concept("debt_proceeds", "Debt issuance proceeds",
            ("ProceedsFromIssuanceOfLongTermDebt", "ProceedsFromDebtNetOfIssuanceCosts",
             "ProceedsFromIssuanceOfDebt"), False),
    Concept("debt_repayments", "Debt repayments",
            ("RepaymentsOfLongTermDebt", "RepaymentsOfDebt"), False),
    Concept("finance_lease_principal", "Finance lease principal payments",
            ("FinanceLeasePrincipalPayments",), False),
    Concept("asset_sale_proceeds", "Proceeds from asset/business sales",
            ("ProceedsFromSaleOfPropertyPlantAndEquipment",
             "ProceedsFromDivestitureOfBusinessesNetOfCashDivested"), False),
    # --- Balance sheet (instant) ---
    Concept("assets", "Total assets", ("Assets",), True),
    Concept("assets_current", "Current assets", ("AssetsCurrent",), True),
    Concept("cash", "Cash & equivalents",
            ("CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsAndShortTermInvestments"), True),
    Concept("short_term_investments", "Short-term investments",
            ("ShortTermInvestments", "MarketableSecuritiesCurrent",
             "AvailableForSaleSecuritiesDebtSecuritiesCurrent"), True),
    # Long-term marketable securities: a large liquid asset for cash-rich issuers
    # (Apple, Microsoft, ...) that net debt must offset. (Financial-sector issuers
    # hold these under sector-specific tags; net debt is sector-flagged anyway.)
    Concept("long_term_investments", "Long-term investments",
            ("MarketableSecuritiesNoncurrent", "AvailableForSaleSecuritiesDebtSecuritiesNoncurrent",
             "LongTermInvestments"), True),
    Concept("restricted_cash", "Restricted cash",
            ("RestrictedCashAndCashEquivalents", "RestrictedCashNoncurrent",
             "RestrictedCashAndCashEquivalentsCurrentAndNoncurrent"), True),
    Concept("receivables", "Accounts receivable",
            ("AccountsReceivableNetCurrent", "ReceivablesNetCurrent"), True),
    Concept("inventory", "Inventory", ("InventoryNet",), True),
    Concept("ppe_net", "Property, plant & equipment (net)",
            ("PropertyPlantAndEquipmentNet",), True),
    Concept("goodwill", "Goodwill", ("Goodwill",), True),
    Concept("intangibles", "Intangible assets (ex-goodwill)",
            ("IntangibleAssetsNetExcludingGoodwill", "FiniteLivedIntangibleAssetsNet",
             "IndefiniteLivedIntangibleAssetsExcludingGoodwill"), True),
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
    # Parent-only stockholders' equity. The NCI-inclusive figure is a DIFFERENT
    # number (equity_total) -- keeping it out of this fallback prevents silently
    # dividing parent-only net income by parent+NCI equity in ROE / book value.
    Concept("equity", "Stockholders' equity (parent)", ("StockholdersEquity",), True),
    Concept("equity_total", "Total equity (incl. NCI)",
            ("StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",), True),
    Concept("noncontrolling_interest", "Noncontrolling interest", ("MinorityInterest",), True),
    # Preferred stock CARRYING value (par + APIC), not par-only: PreferredStockValue
    # is 0/par for most filers and absent for some banks, so the incl.-APIC tag and
    # the outstanding/liquidation tags take priority (verified against BofA/Wells).
    Concept("preferred_stock", "Preferred stock (carrying value)",
            ("PreferredStockIncludingAdditionalPaidInCapital", "PreferredStockValueOutstanding",
             "PreferredStockLiquidationPreferenceValue", "PreferredStockValue"), True),
    Concept("retained_earnings", "Retained earnings / (accumulated deficit)",
            ("RetainedEarningsAccumulatedDeficit",), True),
    Concept("treasury_stock", "Treasury stock",
            ("TreasuryStockValue", "TreasuryStockCommonValue"), True),
    Concept("aoci", "Accumulated other comprehensive income / (loss)",
            ("AccumulatedOtherComprehensiveIncomeLossNetOfTax",), True),
    Concept("pension_obligations", "Pension & post-retirement obligations (noncurrent)",
            ("PensionAndOtherPostretirementDefinedBenefitPlansLiabilitiesNoncurrent",), True),
    # --- Shares (instant, shares) ---
    Concept("shares_outstanding", "Shares outstanding",
            ("CommonStockSharesOutstanding", "EntityCommonStockSharesOutstanding"), True, "shares"),
    Concept("wavg_shares_diluted", "Weighted-avg diluted shares",
            ("WeightedAverageNumberOfDilutedSharesOutstanding",), False, "shares"),
)

CONCEPTS_BY_KEY = {c.key: c for c in CONCEPTS}

# Flow concepts summed over a trailing-twelve-month window for the TTM block.
_TTM_FLOW_KEYS: tuple[str, ...] = (
    "revenue", "net_income", "operating_income", "gross_profit",
    "dep_amort", "cfo", "capex", "interest_expense",
)


@dataclass
class FlowSeries:
    """Per-concept flow values bucketed by duration, for TTM reconstruction.

    ``quarterly``/``annual``/``ytd9`` map concept key -> {period_end: value} for,
    respectively, ~3-month standalone quarters, ~full fiscal years, and ~9-month
    year-to-date cumulatives (used to derive the unreported Q4 = FY - 9M).
    """

    quarterly: dict[str, dict[date, float]]
    annual: dict[str, dict[date, float]]
    ytd9: dict[str, dict[date, float]]


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
    Derived("net_cash", "Net cash position (incl. ST+LT investments)", "USD"),
    Derived("ebitda", "EBITDA", "USD"),
    Derived("free_cash_flow", "Free cash flow", "USD"),
    Derived("working_capital", "Working capital", "USD"),
    Derived("tangible_book_value", "Tangible book value", "USD"),
    Derived("nopat", "NOPAT (op. income after tax)", "USD", annual_only=True),
    Derived("invested_capital", "Invested capital", "USD"),
    # Margins (%)
    Derived("gross_margin", "Gross margin", "%"),
    Derived("operating_margin", "Operating margin", "%"),
    Derived("net_margin", "Net margin", "%"),
    Derived("ebitda_margin", "EBITDA margin", "%"),
    Derived("fcf_margin", "FCF margin", "%"),
    # Returns (%) — stock/flow ratio -> annual only (same rationale as asset_turnover)
    Derived("roe", "Return on common equity", "%", annual_only=True),
    Derived("roa", "Return on assets", "%", annual_only=True),
    Derived("roic", "Return on invested capital", "%", annual_only=True),
    Derived("effective_tax_rate", "Effective tax rate", "%"),
    # Expense intensities & payout / quality (%) — flow/flow, meaningful at any frequency
    Derived("capex_intensity", "Capex intensity (capex / revenue)", "%"),
    Derived("rnd_intensity", "R&D intensity", "%"),
    Derived("sga_ratio", "SG&A / revenue", "%"),
    Derived("dividend_payout", "Dividend payout (dividends / net income)", "%"),
    Derived("total_payout", "Total payout (div + buybacks / FCF)", "%"),
    Derived("cash_conversion", "Cash conversion (FCF / net income)", "%"),
    # Leverage / coverage (multiples)
    Derived("debt_to_equity", "Debt / equity", "x"),
    Derived("debt_to_assets", "Debt / assets", "x"),
    Derived("net_debt_to_ebitda", "Net debt / EBITDA", "x", annual_only=True),
    Derived("interest_coverage", "Interest coverage (op. income / interest)", "x"),
    Derived("cfo_to_debt", "CFO / total debt", "x", annual_only=True),
    Derived("fcf_to_debt", "FCF / total debt", "x", annual_only=True),
    # Liquidity (multiples)
    Derived("current_ratio", "Current ratio", "x"),
    Derived("quick_ratio", "Quick ratio", "x"),
    Derived("cash_ratio", "Cash ratio", "x"),
    # Efficiency / per share
    Derived("asset_turnover", "Asset turnover", "x", annual_only=True),
    Derived("dso", "Days sales outstanding", "days", annual_only=True),
    Derived("dio", "Days inventory outstanding", "days", annual_only=True),
    Derived("dpo", "Days payable outstanding", "days", annual_only=True),
    Derived("ccc", "Cash conversion cycle", "days", annual_only=True),
    Derived("book_value_per_share", "Book value per common share", "USD/shares"),
    Derived("tangible_book_value_per_share", "Tangible book value per share", "USD/shares"),
)

DERIVED_BY_KEY = {d.key: d for d in DERIVED}

# Metrics that are low-information for financial-sector issuers (banks / insurers
# have no classified balance sheet, no COGS, and treat interest and cash as
# operating items). These are NOT dropped -- each derived metric carries a
# `sector_relevant` flag, False for these keys when the issuer's SIC is financial,
# so the corpus stays complete and consistent regardless of SIC availability.
SECTOR_SENSITIVE: frozenset[str] = frozenset({
    "ebitda", "ebitda_margin", "net_debt", "net_cash", "net_debt_to_ebitda",
    "interest_coverage", "current_ratio", "quick_ratio", "cash_ratio", "working_capital",
    "asset_turnover", "gross_margin", "total_debt_incl_leases",
    # Tier 2 metrics that are non-meaningful for banks/insurers (no COGS/inventory,
    # capex/cash treated as operating, ROIC not the standard capital metric).
    "roic", "nopat", "invested_capital", "cfo_to_debt", "fcf_to_debt",
    "capex_intensity", "dso", "dio", "dpo", "ccc",
    # TTM variants (Phase B)
    "ebitda_margin_ttm", "net_debt_to_ebitda_ttm", "interest_coverage_ttm",
    "asset_turnover_ttm", "gross_margin_ttm",
})


# Bloomberg-aligned trailing-twelve-month ratios. Numerators are TTM flows; ROA /
# ROE / asset-turnover denominators are a 2-point average (current + year-ago
# period-end); leverage uses point-in-time net debt over TTM EBITDA. All %/x.
DERIVED_TTM: tuple[Derived, ...] = (
    Derived("roa_ttm", "Return on assets (TTM)", "%"),
    Derived("roe_ttm", "Return on equity (TTM)", "%"),
    Derived("net_margin_ttm", "Net margin (TTM)", "%"),
    Derived("operating_margin_ttm", "Operating margin (TTM)", "%"),
    Derived("gross_margin_ttm", "Gross margin (TTM)", "%"),
    Derived("ebitda_margin_ttm", "EBITDA margin (TTM)", "%"),
    Derived("fcf_margin_ttm", "FCF margin (TTM)", "%"),
    Derived("asset_turnover_ttm", "Asset turnover (TTM)", "x"),
    Derived("net_debt_to_ebitda_ttm", "Net debt / EBITDA (TTM)", "x"),
    Derived("interest_coverage_ttm", "Interest coverage (TTM, EBITDA/interest)", "x"),
)
DERIVED_TTM_BY_KEY = {d.key: d for d in DERIVED_TTM}


def compute_ttm_derived(
    *, t12: dict[str, float | None], avg_assets: float | None,
    avg_equity: float | None, pit_net_debt: float | None,
    is_financial: bool = False,
) -> dict[str, dict]:
    """Bloomberg-style TTM ratios from trailing-12m flows and average balances.

    Leverage / coverage multiples (net_debt_to_ebitda_ttm, interest_coverage_ttm)
    are emitted as-is even when EBITDA is negative — this is intentional; callers
    that want to suppress negative-denominator multiples should filter downstream.
    """
    out: dict[str, dict] = {}

    def put(key: str, val: float | None) -> None:
        if val is None or (isinstance(val, float) and val != val):
            return
        d = DERIVED_TTM_BY_KEY[key]
        out[key] = {"value": val, "unit": d.unit, "label": d.label,
                    "sector_relevant": not (is_financial and key in SECTOR_SENSITIVE)}

    def div(a, b):
        return a / b if (a is not None and b not in (None, 0)) else None

    def div_pos(a, b):
        return a / b if (a is not None and b is not None and b > 0) else None

    def pct(a, b):
        r = div(a, b)
        return r * 100 if r is not None else None

    def pct_pos(a, b):
        r = div_pos(a, b)
        return r * 100 if r is not None else None

    rev = t12.get("revenue")
    ni = t12.get("net_income")
    oi = t12.get("operating_income")
    da = t12.get("dep_amort")
    ebitda = oi + da if (oi is not None and da is not None) else None
    cfo, capex = t12.get("cfo"), t12.get("capex")
    fcf = cfo - capex if (cfo is not None and capex is not None) else None

    put("roa_ttm", pct(ni, avg_assets))
    put("roe_ttm", pct_pos(ni, avg_equity))
    put("net_margin_ttm", pct(ni, rev))
    put("operating_margin_ttm", pct(oi, rev))
    put("gross_margin_ttm", pct(t12.get("gross_profit"), rev))
    put("ebitda_margin_ttm", pct(ebitda, rev))
    put("fcf_margin_ttm", pct(fcf, rev))
    put("asset_turnover_ttm", div(rev, avg_assets))
    put("net_debt_to_ebitda_ttm", div(pit_net_debt, ebitda))
    put("interest_coverage_ttm", div(ebitda, t12.get("interest_expense")))
    return out


def _is_financial(sic: str | None) -> bool:
    """True for SIC 6000-6499 (depository, credit, securities, insurance).

    Real estate (6500+) is intentionally excluded -- REITs report FFO-style
    metrics and are handled as ordinary issuers here.
    """
    if not sic:
        return False
    try:
        code = int(sic)
    except (TypeError, ValueError):
        return False
    return 6000 <= code <= 6499


def _num(values: dict, key: str) -> float | int | None:
    """Numeric reported value for ``key``, or None if absent/non-numeric.

    Integral values are returned as ``int`` so monetary aggregates that only add
    and subtract them (total debt, net debt, EBITDA, FCF) stay exact and serialize
    without a spurious ``.0``. Integers up to 2**53 are exact in float64 anyway, so
    this is about clean output, not lost precision -- a full Decimal conversion
    would add JSON-serialization friction for no accuracy gain. Ratios still divide,
    which yields float.
    """
    v = values.get(key)
    if v is None:
        return None
    val = v.get("value")
    if isinstance(val, bool):  # bool is an int subclass but never a financial value
        return None
    if isinstance(val, int):
        return val
    if isinstance(val, float):
        return int(val) if val.is_integer() else val
    return None


def _src(values: dict, key: str) -> str | None:
    """The source XBRL tag that backed a curated value, if recorded."""
    v = values.get(key)
    return v.get("tag") if v else None


def compute_derived(
    values: dict[str, dict], frequency: str = "annual", currency: str = "USD",
    is_financial: bool = False,
) -> dict[str, dict]:
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
        # Monetary units carry the issuer's reporting currency; ratios (%, x) don't.
        if d.unit == "USD":
            unit = currency
        elif d.unit == "USD/shares":
            unit = f"{currency}/shares"
        else:
            unit = d.unit
        out[key] = {"value": val, "unit": unit, "label": d.label,
                    "sector_relevant": not (is_financial and key in SECTOR_SENSITIVE)}

    def div(a: float | None, b: float | None) -> float | None:
        if a is None or b is None or b == 0:
            return None
        return a / b

    def pct(a: float | None, b: float | None) -> float | None:
        r = div(a, b)
        return r * 100 if r is not None else None

    def div_pos(a: float | None, b: float | None) -> float | None:
        # Like div, but a non-positive denominator is "not meaningful" -> None.
        if a is None or b is None or b <= 0:
            return None
        return a / b

    def pct_pos(a: float | None, b: float | None) -> float | None:
        r = div_pos(a, b)
        return r * 100 if r is not None else None

    def opt(key: str) -> float | int:  # additive component: missing -> 0
        return _num(values, key) or 0

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
        ltd_tag = _src(values, "long_term_debt")
        short_tag = _src(values, "short_term_debt")
        cur = opt("lt_debt_current")
        # LongTermDebt is the FASB roll-up (current + noncurrent); DebtCurrent
        # already includes current maturities of LTD. Either case already counts
        # the current portion, so do not add lt_debt_current again.
        if ltd_tag == "LongTermDebt" or short_tag == "DebtCurrent":
            cur = 0
        total_debt = ltd + cur + opt("short_term_debt")
    put("total_debt", total_debt)

    leases = (opt("finance_lease_current") + opt("finance_lease_noncurrent")
              + opt("operating_lease_current") + opt("operating_lease_noncurrent"))
    if total_debt is not None and leases:
        put("total_debt_incl_leases", total_debt + leases)

    net_debt = None
    if total_debt is not None and cash is not None:
        sti = opt("short_term_investments")
        if _src(values, "cash") == "CashCashEquivalentsAndShortTermInvestments":
            sti = 0  # cash tag already includes short-term investments
        # Long-term marketable securities are a liquid offset to debt (Apple,
        # Microsoft, ...): excluding them overstates net debt / hides net cash.
        net_debt = total_debt - cash - sti - opt("long_term_investments")
    put("net_debt", net_debt)
    # Friendly mirror: positive = net cash, negative = net debt.
    put("net_cash", -net_debt if net_debt is not None else None)

    da = _num(values, "dep_amort")
    ebitda = oi + da if (oi is not None and da is not None) else None
    put("ebitda", ebitda)

    cfo, capex = _num(values, "cfo"), _num(values, "capex")
    fcf = cfo - capex if (cfo is not None and capex is not None) else None
    put("free_cash_flow", fcf)

    if ac is not None and lc is not None:
        put("working_capital", ac - lc)
    # Common-equity book value nets out preferred stock and intangibles.
    common_eq = (eq - opt("preferred_stock")) if eq is not None else None
    tbv = (common_eq - opt("goodwill") - opt("intangibles")) if common_eq is not None else None
    put("tangible_book_value", tbv)

    # Returns on capital: NOPAT / invested capital. Invested capital = total debt +
    # total equity (parent + NCI) -- the total-capital base, no cash netting (keeps
    # the ratio sane for cash-rich issuers). Tax rate clamped to [0,1] for NOPAT.
    pretax = _num(values, "pretax_income")
    inc_tax = _num(values, "income_tax")
    tax_rate = (inc_tax / pretax) if (inc_tax is not None and pretax is not None and pretax > 0) else None
    if tax_rate is not None:
        tax_rate = min(max(tax_rate, 0.0), 1.0)
    nopat = oi * (1 - tax_rate) if (oi is not None and tax_rate is not None) else None
    put("nopat", nopat)
    invested = None
    if total_debt is not None and eq is not None:
        invested = total_debt + eq + opt("noncontrolling_interest")
    put("invested_capital", invested)
    put("roic", pct_pos(nopat, invested))

    # Margins (%)
    put("gross_margin", pct(_num(values, "gross_profit"), rev))
    put("operating_margin", pct(oi, rev))
    put("net_margin", pct(ni, rev))
    put("ebitda_margin", pct(ebitda, rev))
    put("fcf_margin", pct_pos(fcf, rev))  # guard negative revenue, allow negative FCF

    # Returns / tax (%). ROE is on COMMON equity: (net income - preferred dividends).
    ni_common = (ni - opt("preferred_dividends")) if ni is not None else None
    put("roe", pct_pos(ni_common, eq))
    put("roa", pct(ni, assets))
    put("effective_tax_rate", pct_pos(inc_tax, pretax))

    # Expense intensity & payout / quality (%)
    put("capex_intensity", pct(capex, rev))
    put("rnd_intensity", pct(_num(values, "rnd_expense"), rev))
    put("sga_ratio", pct(_num(values, "sga_expense"), rev))
    # Payout / conversion: suppress on non-positive denominators (a loss-year or
    # negative-FCF ratio reads as junk rather than "n.m.").
    put("dividend_payout", pct_pos(_num(values, "dividends_paid"), ni))
    put("total_payout", pct_pos(opt("dividends_paid") + opt("buybacks"), fcf))
    put("cash_conversion", pct_pos(fcf, ni))

    # Leverage / coverage (x)
    put("debt_to_equity", div_pos(total_debt, eq))
    put("debt_to_assets", div(total_debt, assets))
    put("net_debt_to_ebitda", div(net_debt, ebitda))
    put("interest_coverage", div(oi, _num(values, "interest_expense")))
    put("cfo_to_debt", div(cfo, total_debt))
    put("fcf_to_debt", div(fcf, total_debt))

    # Liquidity (x)
    put("current_ratio", div(ac, lc))
    if ac is not None:
        put("quick_ratio", div(ac - opt("inventory"), lc))
    if cash is not None:
        sti = opt("short_term_investments")
        if _src(values, "cash") == "CashCashEquivalentsAndShortTermInvestments":
            sti = 0
        put("cash_ratio", div(cash + sti, lc))

    # Efficiency / per share
    put("asset_turnover", div(rev, assets))
    # Working-capital cycle (days). Annual-only: the /365 annualisation assumes a
    # full-year flow in the numerator's denominator.
    cogs = _num(values, "cost_of_revenue")
    dso = div(_num(values, "receivables"), rev / 365) if rev else None
    dio = div(_num(values, "inventory"), cogs / 365) if cogs else None
    dpo = div(_num(values, "payables"), cogs / 365) if cogs else None
    put("dso", dso)
    put("dio", dio)
    put("dpo", dpo)
    if dso is not None and dio is not None and dpo is not None:
        put("ccc", dso + dio - dpo)
    shares = _num(values, "shares_outstanding")
    put("book_value_per_share", div(common_eq, shares))
    put("tangible_book_value_per_share", div(tbv, shares))

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
    currency: str = "USD"          # issuer's monetary reporting currency
    sic: str | None = None         # SEC SIC code (industry classification)
    ttm: dict[str, dict] = field(default_factory=dict)

    @property
    def is_financial(self) -> bool:
        return _is_financial(self.sic)

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
        return compute_derived(self.values, self.frequency, self.currency,
                               self.is_financial)


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


def _points_by_priority(concept: Concept, flat: dict[str, list[dict]]) -> list[dict]:
    """All points across the concept's fallback tags, each carrying its tag's
    priority index (``_prio``).

    Filers switch tags across taxonomy vintages (e.g. Microsoft's cost of revenue
    moved from ``CostOfRevenue`` to ``CostOfGoodsAndServicesSold``; Alphabet's
    revenue from ``RevenueFromContractWithCustomerExcludingAssessedTax`` to
    ``Revenues``). The old first-tag-wins lookup returned the stale tag's points
    and dropped the recent period. Carrying priority lets per-period selection
    prefer the highest-priority tag that actually has a value for *that* period.
    """
    pts: list[dict] = []
    for prio, tag in enumerate(concept.tags):
        for p in flat.get(tag, []):
            q = dict(p)
            q["_prio"] = prio
            pts.append(q)
    return pts


def _choose(cands: list[dict]) -> dict:
    """Resolve one (period, concept): the highest-priority tag present for this
    period, then the latest-filed point within it (restatements win)."""
    best = min(p.get("_prio", 0) for p in cands)
    return _latest_filed([p for p in cands if p.get("_prio", 0) == best])


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


def _classify_duration_days(days: int) -> str | None:
    """Bucket a duration length into 'quarterly' (~3m), 'ytd9' (~9m) or 'annual'."""
    if 80 <= days <= 100:
        return "quarterly"
    if 250 <= days <= 290:
        return "ytd9"
    if 330 <= days <= 400:
        return "annual"
    return None


def _build_flow_series(flat: dict[str, list[dict]], currency: str | None) -> FlowSeries:
    quarterly: dict[str, dict[date, float]] = {}
    annual: dict[str, dict[date, float]] = {}
    ytd9: dict[str, dict[date, float]] = {}
    buckets = {"quarterly": quarterly, "annual": annual, "ytd9": ytd9}
    for key in _TTM_FLOW_KEYS:
        concept = CONCEPTS_BY_KEY[key]
        # Priority-aware union across the concept's fallback tags: a filer may tag a
        # concept differently across vintages, and TTM reconstruction needs every
        # period regardless of which tag carried it -- while still preferring the
        # higher-priority tag per period (avoids mixing e.g. excluding- vs
        # including-assessed-tax revenue when both are present for one period).
        grouped: dict[tuple[str, date], list[dict]] = {}
        for p in _currency_filtered(_points_by_priority(concept, flat), concept, currency):
            end = _to_date(p.get("end"))
            start = _to_date(p.get("start"))
            if not end or not start:
                continue
            bucket = _classify_duration_days((end - start).days)
            if not bucket:
                continue
            grouped.setdefault((bucket, end), []).append(p)
        for (bucket, end), cands in grouped.items():
            buckets[bucket].setdefault(key, {})[end] = _choose(cands)["val"]
    return FlowSeries(quarterly=quarterly, annual=annual, ytd9=ytd9)


def _standalone_quarter(series: FlowSeries, key: str, end: date) -> float | None:
    """The ~3-month flow ending at ``end``; derive an unreported FY-end quarter."""
    direct = series.quarterly.get(key, {}).get(end)
    if direct is not None:
        return direct
    fy = series.annual.get(key, {}).get(end)
    if fy is None:
        return None
    # The FY-end quarter (Q4) = FY total - 9-month YTD ending ~one quarter earlier.
    for q3_end, ytd in series.ytd9.get(key, {}).items():
        if 80 <= (end - q3_end).days <= 100:
            return fy - ytd
    return None


def _quarter_ends(series: FlowSeries) -> list[date]:
    """All quarter-boundary end dates (standalone quarters + FY ends), sorted."""
    ends: set[date] = set()
    for key in _TTM_FLOW_KEYS:
        ends.update(series.quarterly.get(key, {}))
        ends.update(series.annual.get(key, {}))
    return sorted(ends)


def _ttm_flow(series: FlowSeries, key: str, end: date, frequency: str) -> float | None:
    """Trailing-twelve-month sum of ``key`` ending at ``end``.

    Annual periods use the fiscal-year value directly; quarterly periods sum the
    four trailing standalone quarters (deriving the FY-end quarter when needed).
    Returns None if any of the four quarters cannot be resolved.
    """
    if frequency == "annual":
        return series.annual.get(key, {}).get(end)
    ends = _quarter_ends(series)
    if end not in ends:
        return None
    idx = ends.index(end)
    if idx < 3:
        return None
    window = ends[idx - 3:idx + 1]
    # Reject windows with a gap (a missing quarter) > ~one quarter between steps.
    for earlier, later in zip(window, window[1:]):
        if not (80 <= (later - earlier).days <= 100):
            return None
    total = 0.0
    for q_end in window:
        v = _standalone_quarter(series, key, q_end)
        if v is None:
            return None
        total += v
    return total


def build_period_summaries(
    facts: dict,
    *,
    company: str,
    company_current: str,
    name_for_date=None,
    since_year: int | None = None,
    until_year: int | None = None,
    sic: str | None = None,
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
        for p in _currency_filtered(_points_by_priority(concept, flat), concept, currency):
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
        if until_year is not None and end.year > until_year:
            continue

        values: dict[str, dict] = {}
        all_points: list[dict] = []
        for key, cands in per_concept.items():
            chosen = _choose(cands)  # highest-priority tag for this period, restatements win
            values[key] = {"value": chosen["val"], "unit": chosen.get("unit", CONCEPTS_BY_KEY[key].unit),
                           "label": CONCEPTS_BY_KEY[key].label,
                           "tag": chosen.get("tag")}
            all_points.extend(cands)
        for key, cands in instant.get(end, {}).items():
            chosen = _choose(cands)
            values[key] = {"value": chosen["val"], "unit": chosen.get("unit", CONCEPTS_BY_KEY[key].unit),
                           "label": CONCEPTS_BY_KEY[key].label,
                           "tag": chosen.get("tag")}
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
            currency=currency or "USD", sic=sic,
        ))

    summaries.sort(key=lambda s: (s.period_end or date.min, s.frequency), reverse=True)
    return summaries


def _prior_year(by_end: dict[tuple[str, date], "PeriodSummary"], freq: str,
                end: date) -> "PeriodSummary | None":
    """The same-frequency summary ending ~one year before ``end`` (345-385 days)."""
    for (f, e), s in by_end.items():
        if f == freq and 345 <= (end - e).days <= 385:
            return s
    return None


def attach_ttm_metrics(facts: dict, summaries: list[PeriodSummary]) -> None:
    """Compute and attach Bloomberg-style TTM ratios to each summary in place.

    TTM flows come from a trailing-4-quarter reconstruction; ROA/ROE/asset-turnover
    denominators use the average of the current and year-ago period-end balance.
    Issuers flagged financial carry sector_relevant=False on sensitive metrics.
    """
    flat = flatten_points(facts)
    currency = reporting_currency(flat)
    series = _build_flow_series(flat, currency)
    by_end = {(s.frequency, s.period_end): s for s in summaries if s.period_end}

    for s in summaries:
        if not s.period_end:
            continue
        t12 = {k: _ttm_flow(series, k, s.period_end, s.frequency) for k in _TTM_FLOW_KEYS}
        prior = _prior_year(by_end, s.frequency, s.period_end)

        def avg(key: str, _s=s, _prior=prior) -> float | None:
            cur = _num(_s.values, key)
            old = _num(_prior.values, key) if _prior else None
            return (cur + old) / 2 if (cur is not None and old is not None) else None

        pit_net_debt = compute_derived(
            s.values, s.frequency, s.currency, s.is_financial
        ).get("net_debt", {}).get("value")

        s.ttm = compute_ttm_derived(
            t12=t12, avg_assets=avg("assets"), avg_equity=avg("equity"),
            pit_net_debt=pit_net_debt, is_financial=s.is_financial,
        )


def _fmt(value, unit: str) -> str:
    if not isinstance(value, (int, float)):
        return html.escape(str(value))
    if unit.endswith("/shares"):  # any currency per share, e.g. USD/shares, EUR/shares
        return f"{value:,.2f}"
    if unit == "shares":
        return f"{value:,.0f}"
    if unit == "%":
        return f"{value:,.1f}%"
    if unit == "x":
        return f"{value:,.2f}x"
    if unit == "days":
        return f"{value:,.1f} days"
    return f"{value:,.0f}"  # monetary (whole units of the reporting currency)


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
    ttm_tbl = (
        _metric_table(
            (summary.ttm[k] for k in DERIVED_TTM_BY_KEY if k in summary.ttm),
            "Trailing-twelve-month metrics (Bloomberg-style)",
        )
        if summary.ttm else ""
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
        f"{reported}{derived_tbl}{ttm_tbl}</body></html>"
    )


def normalized_rows(cik: str, summary: PeriodSummary) -> list[dict]:
    """Flatten a period summary into queryable rows for data/financials/<cik>.jsonl.

    Emits both ``kind="reported"`` rows (raw XBRL concepts) and ``kind="derived"``
    rows (computed ratios/aggregates), so leverage, EBITDA, total debt, etc. are
    queryable alongside the line items they came from.
    """
    base = {
        "cik": cik, "fy": summary.fy, "frequency": summary.frequency,
        "currency": summary.currency, "sic": summary.sic,
        "is_financial": summary.is_financial,
        "period_end": summary.period_end.isoformat() if summary.period_end else None,
        "publication_date": summary.publication_date.isoformat() if summary.publication_date else None,
        "sec_form": summary.sec_form, "accession": summary.accession,
    }
    rows = [
        {**base, "kind": "reported", "concept": key,
         "label": v["label"], "value": v["value"], "unit": v["unit"],
         "tag": v.get("tag")}
        for key, v in summary.values.items()
    ]
    rows += [
        {**base, "kind": "derived", "concept": key,
         "label": v["label"], "value": v["value"], "unit": v["unit"],
         "sector_relevant": v.get("sector_relevant", True)}
        for key, v in summary.derived.items()
    ]
    rows += [
        {**base, "kind": "derived_ttm", "concept": key,
         "label": v["label"], "value": v["value"], "unit": v["unit"],
         "sector_relevant": v.get("sector_relevant", True)}
        for key, v in summary.ttm.items()
    ]
    return rows
