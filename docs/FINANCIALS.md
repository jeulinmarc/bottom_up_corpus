# Financials — curated XBRL concepts, derived metrics & TTM ratios

`bottom_up_corpus/financials.py` distils the SEC `companyfacts` XBRL feed into one
**summary per actual reporting period** (annual / quarterly / semi-annual), each
carrying ~40 curated line items, a block of **derived metrics** (single-period),
and a block of **trailing-twelve-month (TTM)** ratios aligned with Bloomberg's
methodology. The full raw JSON is kept separately for exhaustivity.

This document is the reference for what is computed and how. The definitions are
verified against the FASB/SEC US-GAAP XBRL taxonomy and, for the TTM ratios,
against Bloomberg's published quarterly figures.

## Period model

A period is keyed by its **own period-end + frequency** (derived from each fact's
`start`/`end`), so prior-year comparatives carried inside a filing land in their
own period rather than the report's fiscal year. Duration facts (income, cash flow)
define the periods; instant facts (balance sheet) attach by matching end date.
Restatements resolve to the latest `filed`; `publication_date` is the earliest
`filed` for the period. Each value also records its source XBRL **tag** as
provenance.

Monetary facts are filtered to the issuer's dominant reporting currency, so a
convenience translation (e.g. a USD value alongside a primary EUR one) never gets
summed or divided as if it were the functional currency.

## Curated concepts

~40 reported line items across income statement, per-share, cash flow, and balance
sheet, each with fallback XBRL tags in priority order (see `CONCEPTS` in
`financials.py`). Examples: `revenue`, `operating_income`, `net_income`,
`dep_amort`, `cfo`, `capex`, `assets`, `equity`, `long_term_debt`, `cash`,
`shares_outstanding`.

## Derived metrics (single period)

Computed by `compute_derived`. Each metric is emitted only when **all** its
required inputs are present; additive *components* (current debt, short-term
investments, leases) default to 0. Monetary metrics carry the reporting currency;
ratios are `%` or `x`.

| Metric | Definition |
|---|---|
| `total_debt` | long-term debt + current portion + short-term borrowings |
| `total_debt_incl_leases` | total debt + finance & operating lease liabilities |
| `net_debt` | total debt − cash − short-term investments |
| `ebitda` | operating income + D&A |
| `free_cash_flow` | CFO − capex |
| `working_capital` | current assets − current liabilities |
| `tangible_book_value` | equity − goodwill − intangibles |
| `gross/operating/net/ebitda/fcf_margin` | the respective profit ÷ revenue (%) |
| `roe`, `roa` | net income ÷ equity / assets (%) — **annual only** |
| `effective_tax_rate` | income tax ÷ pretax income (%) |
| `debt_to_equity`, `debt_to_assets` | total debt ÷ equity / assets (x) |
| `net_debt_to_ebitda` | net debt ÷ EBITDA (x) — **annual only** |
| `interest_coverage` | operating income ÷ interest expense (x) |
| `current/quick/cash_ratio` | liquidity ratios (x) |
| `asset_turnover` | revenue ÷ assets (x) — **annual only** |
| `book_value_per_share` | equity ÷ shares outstanding |

### Overlap-aware aggregation (no double counting)

Three FASB-confirmed concept overlaps are handled by branching on the resolved
source tag:

- **`LongTermDebt`** is the roll-up (`= LongTermDebtCurrent + LongTermDebtNoncurrent`).
  When it is the resolved tag, the current portion is *not* added again.
- **`DebtCurrent`** already includes current maturities of long-term debt; when it
  backs `short_term_debt`, the separate current portion is *not* added again.
- **`CashCashEquivalentsAndShortTermInvestments`** already includes short-term
  investments; when it backs `cash`, STI is *not* subtracted/added again in
  `net_debt` / `cash_ratio`.

### Guards

A ratio with a non-meaningful denominator is omitted rather than emitting a
misleading number: `roe` / `debt_to_equity` when equity ≤ 0, `effective_tax_rate`
when pretax income ≤ 0. (Several large issuers carry negative book equity from
buybacks.) `roe`/`roa` are stock/flow ratios and are therefore **annual only** in
the single-period block — their sub-annual signal is carried by the TTM versions.

## Financial-sector flagging

Banks and insurers (SIC 6000–6499) have no classified balance sheet, no COGS, and
treat interest and cash as operating items, so metrics like EBITDA, net debt,
coverage and liquidity ratios are low-information for them. **Nothing is dropped** —
every derived metric carries a boolean `sector_relevant` (False for the
sector-sensitive set when the issuer is financial). This keeps the corpus complete
and consistent (a metric's presence never depends on whether the SIC was fetched);
a consumer that wants to exclude bank EBITDA can filter `sector_relevant == False`.
`sic` and `is_financial` are surfaced on every output row.

## Trailing-twelve-month (TTM) ratios — Bloomberg-aligned

`compute_ttm_derived` + `attach_ttm_metrics` add a `ttm` block so quarterly ratios
match how Bloomberg reports them. Numerators are trailing-12-month flows; the
balance-sheet denominator for ROA / ROE / asset-turnover is a **2-point average**
of the current and year-ago period-end.

```
T12(X)  = sum of the 4 trailing standalone quarters ending at t
          (the unreported fiscal-year-end quarter is derived as Annual − 9M YTD)
AVG(B)  = (B at t + B one year earlier) / 2          # same frequency
PIT(B)  = B at t

roa_ttm              = T12(net_income)      / AVG(assets)            × 100
roe_ttm              = T12(net_income)      / AVG(equity)            × 100   # equity > 0
net/operating/gross/ebitda/fcf_margin_ttm   = T12(flow) / T12(revenue) × 100
asset_turnover_ttm   = T12(revenue)         / AVG(assets)            (x)
net_debt_to_ebitda_ttm = PIT(net_debt)      / T12(ebitda)           (x)
interest_coverage_ttm  = T12(ebitda)        / T12(interest_expense) (x)
```

Annual periods use the fiscal-year value as the flow window. A TTM metric is
omitted when its 4-quarter window is incomplete (early periods) or, for the
averaged metrics, when the year-ago balance is missing — never zero-filled. The
TTM flow series unions a concept's points across all its fallback tags, so a tag
change across taxonomy vintages (e.g. `SalesRevenueNet` → `Revenues`) does not
suppress TTM.

**Validation.** This reproduces Apple's published quarterly Return on Assets to
four decimal places: **32.5629** at 2025-12-27 and **34.9060** at 2026-03-28
(`tests/test_ttm.py`).

### Conventions / known limitations

- Negative-EBITDA leverage / coverage multiples (`net_debt_to_ebitda*`,
  `interest_coverage_ttm`) are emitted **as-is** (the multiple may be negative);
  consumers that want Bloomberg's "n.m." filter downstream.
- Returns/averages use reported period-end balances (no intra-period averaging
  beyond the 2-point TTM average).
- Full IFRS (`ifrs-full`) concept mapping is deferred to the international pillar;
  an IFRS-only filer currently maps just `net_income` (via `ProfitLoss`).

## Output

`normalized_rows(cik, summary)` flattens a period into queryable rows for
`data/financials/<cik>.jsonl`, emitting `kind="reported"` (raw concepts, with their
source `tag`), `kind="derived"`, and `kind="derived_ttm"` rows. Every row carries
`cik`, `fy`, `frequency`, `currency`, `sic`, `is_financial`, `period_end`,
`publication_date`, `sec_form`, `accession`; derived/TTM rows also carry
`sector_relevant`. `render_summary_html` renders the reported, derived, and TTM
tables as a standalone HTML document.

See `examples/04_xbrl_financials.py` for a runnable end-to-end example.
