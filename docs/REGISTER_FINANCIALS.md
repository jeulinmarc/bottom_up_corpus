# Register Financials — statutory accounts from national business registers

`bottom_up_corpus/registers/` ingests **open-data statutory accounts** from national
business registers and writes them in the same curated schema as the SEC and EU pillars.
This document covers the purpose, source, schema, honest caveats, and CLI for two
registers: **Norway's Brønnøysund Register Centre (Brreg)** and **UK Companies House**.

See also: [`FINANCIALS.md`](FINANCIALS.md) (the SEC/US-GAAP pillar that defines the
shared schema and derived-metrics engine) and [`EU_FINANCIALS.md`](EU_FINANCIALS.md)
(the ESEF/IFRS pillar for listed EU issuers).

## Purpose

The SEC and EU ESEF pillars cover **listed issuers** — but the credit and private-company
universe is dominated by **non-listed entities** that never file an annual report with an
exchange or regulator, and therefore never appear on SEC EDGAR or filings.xbrl.org.
National business registers are the primary structured source for this universe: in most
jurisdictions, every legal entity above a size threshold is required to file annual
statutory accounts at the register, and those accounts are increasingly available as
open data.

A second motivation is **depth of history**: ESEF mandates structured iXBRL only from
2020 onwards, whereas national registers often hold statutory filings going back a decade
or more. For Norwegian entities, Brreg's open JSON endpoint typically returns 5–10 years
of history, well beyond the ESEF cap.

The register output lives in a **separate directory** (`data/financials_register/`) and
is **never merged** with the ESEF consolidated output (`data/financials_eu/`). The two
pillars serve different universes and different GAAP regimes; keeping them separate
avoids silent cross-contamination in analytical tables.

## Source — Brreg Regnskapsregisteret (Norway)

```
GET https://data.brreg.no/regnskapsregisteret/regnskap/{orgnr}
```

**No API key required.** The endpoint is public and returns a JSON **list** of annual
account entries for the given `orgnr` (Norwegian organisation number, 9 digits). Each
entry contains:

| JSON field | Meaning |
|---|---|
| `regnskapstype` | `SELSKAP` (standalone legal entity) or `KONSERN` (consolidated group) |
| `valuta` | Reporting currency (typically `NOK`) |
| `regnskapsperiode.tilDato` | Period-end date (ISO-8601 date string) |
| `resultatregnskapResultat` | Income-statement block |
| `eiendeler` | Assets block |
| `egenkapitalGjeld` | Equity-and-liabilities block |

The engine flattens the three account blocks recursively by field name and maps the
resulting leaves to the curated concept keys (`concepts_no.py`). There is **no XBRL or
Arelle** involved: Brreg's JSON has named numeric fields, so the mapping is a direct
field-to-key lookup — a much simpler path than the ESEF/SEC XBRL pipelines.

A `404` or a network error returns an empty list; the entry is recorded as
`no-financials` in the coverage report and never silently dropped.

## Schema — `data/financials_register/<orgnr>.jsonl`

Output follows the same per-period row model as the SEC and EU pillars
(see [`FINANCIALS.md`](FINANCIALS.md) for the full definition of curated concepts and
derived metrics). Each row carries:

| Column | Value |
|---|---|
| `entity_id` | Org number (9-digit string) |
| `lei` | GLEIF LEI if supplied or resolved; `null` otherwise |
| `country` | `"NO"` |
| `source` | `"brreg"` |
| `basis` | `"company"` (from `SELSKAP`) or `"consolidated"` (from `KONSERN`) |
| `fy` | Fiscal year (integer, derived from `period_end`) |
| `frequency` | `"annual"` (Brreg holds only annual statutory accounts) |
| `currency` | Reporting currency from `valuta` (typically `"NOK"`) |
| `period_end` | ISO-8601 date string |
| `publication_date` | `null` (Brreg does not expose a filing date) |

Beyond the identity/period columns, each row is one of **two** kinds: `kind="reported"`
(the mapped curated concepts, each carrying its source Brreg field name as `tag`) and
`kind="derived"` (single-period metrics from the shared `compute_derived` engine). **No
`derived_ttm` rows are written**: Brreg holds annual accounts only, so the
trailing-twelve-month block is inert and never attached for register inputs.

Register inputs carry **no depreciation/amortization and no cash-flow statement**, so the
engine emits a strict *subset* of its full derived block — for a complete filer
(e.g. Equinor) exactly these:

- **Aggregates:** `total_debt`, `net_debt`, `net_cash`, `working_capital`,
  `nopat`, `invested_capital`
- **Margins:** `operating_margin`, `net_margin`
- **Returns / tax:** `roe`, `roa`, `roic`, `effective_tax_rate`
- **Leverage / coverage:** `debt_to_equity`, `debt_to_assets`, `interest_coverage`
- **Liquidity:** `current_ratio`, `quick_ratio`, `cash_ratio`
- **Efficiency:** `asset_turnover`, `dso`

A metric whose specific inputs are missing for a given period is simply omitted (e.g.
`interest_coverage` needs `annenRentekostnad`; `dso` needs `sumFordringer`). There is in
particular **no `ebitda`** (no D&A), **no `free_cash_flow`, `cfo_to_debt` or
`fcf_to_debt`** (no cash-flow statement) and **no `net_debt_to_ebitda`** — these are
never produced for register rows.

There is also **no `tangible_book_value`**: Brreg aggregates intangibles into
`sumAnleggsmidler` and exposes no goodwill/intangibles breakdown, so true tangible book
value cannot be derived — the engine's TBV would collapse to `equity` and silently
overstate it for any obligor carrying intangibles. Plain book value remains available as
the reported `equity` line.

A coverage report is written to `data/reports/register_coverage.jsonl` for every entity
processed: `status="ok"` with a period count, or `"no-financials"` / `"unresolved"`.

## The `basis` field — statutory entity vs. consolidated group

This distinction matters for credit analysis and is explicit in every row:

- **`basis="company"`** (from `SELSKAP`): the **legal entity's own standalone accounts**
  under Norwegian GAAP (N-GAAP). For a holding company, these are the **parent entity's
  accounts alone** — not the group. Revenue, assets, and income reflect only the legal
  entity, not its subsidiaries. This is often the correct unit of analysis for credit
  (the obligor is the legal entity, not the economic group).

- **`basis="consolidated"`** (from `KONSERN`): the **group consolidated accounts**,
  also under N-GAAP. This is the economic group, analogous in concept to the ESEF
  consolidated output — but computed under Norwegian GAAP rather than IFRS, and sourced
  from the register rather than the ESEF mandatory filing.

Both are retained when Brreg provides them, clearly labelled. A query that wants only
group-level financials should filter `basis="consolidated"`; a query targeting the legal
obligor should use `basis="company"`.

Note that `basis="consolidated"` from Brreg is **not the same as** the ESEF consolidated
rows in `data/financials_eu/`: the GAAP differs (N-GAAP vs. IFRS), the source differs
(register vs. ESEF filing), and the concepts mapped differ in granularity. Never merge
the two without an explicit accounting-regime reconciliation.

## Honest caveats

### Leverage is liabilities-based

Brreg provides total-liabilities figures (`sumGjeld`, `sumLangsiktigGjeld`,
`sumKortsiktigGjeld`) but **not pure financial borrowings** (bonds, bank loans,
leases). The mapping therefore approximates:

```
short_term_debt  ← sumKortsiktigGjeld  (all current liabilities)
long_term_debt   ← sumLangsiktigGjeld  (all non-current liabilities)
total_debt       ≈ total liabilities   (not pure financial debt)
```

When a filer has no non-current liabilities, Brreg omits the `sumLangsiktigGjeld` leaf
entirely (`langsiktigGjeld: {}`). Because the engine gates `total_debt` — and therefore
every gearing metric — on `long_term_debt` being present, `long_term_debt` is in that
case **synthesized** as total − current liabilities (`sumGjeld − sumKortsiktigGjeld`,
recorded with a `derived` tag) so that gearing stays available for the small/private
filers this pillar targets. When the leaf is present it is used directly, with no
synthesis.

As a result, **every derived metric built on `total_debt` inherits this
total-liabilities basis**:

- `debt_to_equity` and `debt_to_assets` are **liabilities-based gearing**, not
  pure-borrowings ratios.
- `net_debt` here is **total liabilities − cash** (NOT financial net debt), and
  `net_cash` is its mirror. `invested_capital` (= total liabilities + equity) and
  `roic` (NOPAT / invested capital) therefore also rest on the total-liabilities basis.
  Read these as liabilities-based, not borrowings-based, figures.
- `interest_coverage` (operating income / interest) is an **approximation**: Brreg's
  only gross-interest field is `annenRentekostnad`, and intra-group interest
  (`rentekostnadSammeKonsern`) is not added in, so coverage **excludes intra-group
  interest**. (The net `sumFinanskostnad` is deliberately *not* used — it is a net
  financial figure, not gross interest.)
- The `net_debt_to_ebitda`, `cfo_to_debt` and `fcf_to_debt` ratios are **not emitted at
  all** for register rows (no EBITDA and no cash-flow statement — see the schema section).
- This is coarser than the ESEF/SEC pillars (which map tagged debt line items from XBRL)
  but is the best available from the register's structured data.
- The source field `tag` in each reported row records which Brreg field was used, so
  downstream consumers can inspect the mapping.

For the credit and private-company universe — where Brreg is typically the **only**
structured source — this approximation is accepted as-is. Cross-checking against the
entity's actual credit agreements or audited notes is the appropriate next step for
individual names.

### Annual accounts only

Brreg holds statutory annual accounts. There are no quarterly or semi-annual periods.
`frequency` is always `"annual"`.

### Norwegian GAAP (N-GAAP), not IFRS

The statutory accounts filed at Brreg follow N-GAAP (or IFRS as adopted by Norwegian
law for large listed entities). Concept definitions differ from IFRS in edge cases
(e.g. treatment of deferred tax, pension, minority interest). The curated mapping
(`concepts_no.py`) maps the Brreg fields as closely as possible to the shared schema
keys, but users comparing register rows to ESEF rows for the same entity should be
aware of these GAAP differences.

## Identity resolution

Two input modes, mutually exclusive:

- **`--orgnrs`**: comma-separated 9-digit Norwegian org numbers. Used directly, no
  external lookup required.
- **`--leis`**: comma-separated GLEIF LEIs. For each LEI, the engine calls
  `GET https://api.gleif.org/api/v1/lei-records/{lei}` and extracts the
  `entity.registeredAs` field. This is accepted as the orgnr **only when**
  `entity.legalAddress.country == "NO"` — there is no guess, no fuzzy match. A LEI
  from a non-Norwegian jurisdiction returns `status="unresolved"` in the coverage
  report.

In both modes, the resolved `lei` (if available) is carried through to every output row.
An entity that could not be resolved is recorded in the coverage report and skipped —
never a silent drop.

## CLI usage

```
# dry-run (default): print summary, nothing written
bottom_up_corpus register-financials --orgnrs 923609016,974760673

# write data/financials_register/<orgnr>.jsonl + coverage report
bottom_up_corpus register-financials --orgnrs 923609016,974760673 --write

# resolve from LEIs (GLEIF lookup, Norway only)
bottom_up_corpus register-financials --leis 5493001KJTIIGC8Y1R12 --write
```

`--write` is the only side-effecting flag. Omitting it is a safe dry-run that prints the
entity and period count without touching disk. `--orgnrs` and `--leis` are mutually
exclusive.

---

## Source — UK Companies House statutory accounts (iXBRL)

UK statutory accounts are filed with **Companies House** as **iXBRL** (inline XBRL)
documents under **FRC taxonomies**: FRS 105 for micro-entities, FRS 102 for small and
medium companies (the dominant volume by count), and IFRS for listed or large companies.
Each filing is a bare `.html` file carrying inline XBRL tags in the FRC namespace.

The engine parses these files with **Arelle** — the same bare-`.html` loader already
used by the EU Tier B pipeline (`oim_from_ch_html` → `flatten_oim_json`). The OIM-JSON
fact format produced by Arelle is flattened by `flatten_oim_json` and mapped to curated
concepts by `map_ch_facts` (`concepts_uk.py`). No new XBRL infrastructure is needed: the
EU Tier B path is reused without duplication.

### Two acquisition paths

| Path | Key required | Status |
|---|---|---|
| **Accounts Data Product** (bulk monthly ZIP) | None | Built — `--ch-bulk` |
| **Companies House REST API** (targeted, per CH number) | Free developer key | Deferred — next PR |

The **Accounts Data Product** is a public bulk download. Companies House publishes one
ZIP per monthly cut, containing every set of accounts filed during that month as
individual `.html` iXBRL files. No API key or authentication is required. The
`--ch-bulk` flag takes the local path to a downloaded ZIP and processes every filing
inside it in a single pass (with an optional `--limit N` cap for bounded test runs).

The **targeted REST API** accepts a CH number and returns that company's filing history.
It requires a free developer key and is better suited to named-entity workflows where
processing the full bulk extract is not needed. Targeted-API support is deferred to a
follow-up PR; the `--ch-bulk` path does not filter by company — it processes the entire
ZIP.

## The balance-sheet-primary reality

The UK private-company universe is **overwhelmingly micro and small companies** filing
**balance-sheet-only** statutory accounts. In a calibration sample of 5,326 files drawn
from the Accounts Data Product:

| Measure | Approximate share |
|---|---|
| Any balance-sheet data (net assets / equity tagged) | ~72% |
| Revenue (`TurnoverRevenue`) tagged | ~1.3% |
| Dormant / nil filings | ~28% |

The practical consequence: this register is a **balance-sheet register, not a P&L
register**. For the overwhelming majority of filers the useful outputs are **equity, net
assets, leverage, and liquidity** — not revenue, operating income, or margin ratios.
P&L concepts (`revenue`, `gross_profit`, `operating_income`, `net_income`) are available
only for the minority that file **full accounts** rather than abridged or micro-entity
accounts.

Do not use this register to rank or screen UK private companies by revenue: approximately
98.7% of filings carry no tagged revenue and would silently drop out of any such query.
Balance-sheet scoring and equity-based ranking are the intended use case.

## Schema — `data/financials_register/<ch_number>.jsonl`

Output follows the same per-period row model as the NO register and the SEC/EU pillars
(see [`FINANCIALS.md`](FINANCIALS.md) for the full curated-concepts definition):

| Column | Value |
|---|---|
| `entity_id` | CH number (string; verbatim — leading zeros and SC/NI/OC prefixes preserved) |
| `lei` | `null` (LEI resolution deferred — see Identity section below) |
| `country` | `"GB"` |
| `source` | `"companies_house"` |
| `basis` | `"company"` (statutory legal entity; consolidated detection deferred) |
| `fy` | Fiscal year (integer, derived from `period_end`) |
| `frequency` | `"annual"` |
| `currency` | Reporting currency from the iXBRL unit (typically `"GBP"`) |
| `period_end` | ISO-8601 date string (the latest end date seen in the iXBRL filing) |
| `publication_date` | `null` (filing date not extracted from the bulk product) |

Each row is `kind="reported"` (directly-tagged curated concepts, each carrying its FRC
local name as `tag`) or `kind="derived"` (single-period metrics from the shared
`compute_derived` engine). No `derived_ttm` rows: the bulk product contains one period
per filing.

Because most UK filings carry only balance-sheet data, the derived block is a strict
subset of the engine's full output. For a **complete filer** (revenue + full balance
sheet both tagged):

- **Aggregates:** `total_debt`, `net_debt`, `net_cash`, `working_capital`,
  `invested_capital`
- **Margins:** `operating_margin`, `net_margin`
- **Returns:** `roe`, `roa`
- **Leverage / liquidity:** `debt_to_equity`, `debt_to_assets`, `current_ratio`,
  `quick_ratio`, `cash_ratio`

For the **majority of balance-sheet-only filers**, every metric that requires revenue or
income is absent. There is no `ebitda` (no D&A in the iXBRL schema), no
`free_cash_flow`, `cfo_to_debt`, or `fcf_to_debt` (no cash-flow statement), no
`net_debt_to_ebitda`, and no `tangible_book_value` (suppressed — FRC filings carry no
goodwill/intangibles breakdown, so the engine's TBV would collapse to equity and
silently overstate it for any filer carrying intangibles; suppressed via the shared
`_SUPPRESSED_CONCEPTS` filter, the same mechanism as the NO register).

A coverage report is written to `data/reports/register_coverage.jsonl` for every filing
processed: `status="ok"` with a period count, `"no-financials"` (no usable iXBRL facts
or empty values after gating), `"unbalanced"` (primary gate rejected the filing), or
`"error"` (parse exception). When items were suppressed by the confidence gate, a
`suppressed` list records each key name and the reason.

## The confidence gate — no false data

**Governing principle (from `concepts_uk.py`):** a number known to be wrong must never
be emitted; a missing number is strictly better than a wrong one. UK iXBRL filings carry
no universally-tagged `TotalAssets` or `TotalDebt` field — totals must be **derived from
structural anchors**, and every derivation is gated: if the anchor does not confirm the
result, the derived value is suppressed and the reason is recorded. The engine never
defaults a missing balance-sheet item to zero.

### Primary gate: `NetAssetsLiabilities == Equity`

Both `NetAssetsLiabilities` and `Equity` are independently tagged in the vast majority
of balance-sheet filings. If they disagree beyond a tolerance of
`max(2 GBP, 0.5% of the larger figure)`, the **entire filing is rejected**:
`status="unbalanced"`, no values emitted at all. A balance sheet that does not close is
untrustworthy by construction; partial values from such a filing would actively mislead a
downstream user.

### Anchor check: `TALCL == FixedAssets + NetCurrentAssets`

When `FixedAssets` is tagged, `TotalAssetsLessCurrentLiabilities` must equal
`FixedAssets + NetCurrentAssetsLiabilities`. A mismatch proves the balance-sheet inputs
are internally inconsistent. On mismatch, **every derived balance-sheet item**
(`assets`, `liabilities`, `liabilities_current`, `short_term_debt`, `long_term_debt`) is
suppressed — the directly-tagged P&L lines and equity / net_assets / cash still stand.
The mismatch reason is recorded per key in the coverage `suppressed` list.

### The `assets` derivation — why TALCL, not `FixedAssets + CurrentAssets`

```
assets = TotalAssetsLessCurrentLiabilities + current_liabilities
```

Not `FixedAssets + CurrentAssets`. The reason: `FixedAssets` is frequently **untagged**
in micro and small-company filings — dimensioned away when the company holds no fixed
assets, or reported under sub-components that the taxonomy does not roll up into the
`FixedAssets` concept. Using `FixedAssets + CurrentAssets` in those cases would
**silently understate** total assets (a wrong number). The TALCL anchor is tagged
consistently even when fixed assets are absent, making it the robust derivation path.

### Atomic liability block — no partial debt

The derived liability / debt block is emitted **all-or-nothing**:

```
current_liabilities  =  CurrentAssets − NetCurrentAssetsLiabilities
long_term_debt       =  TotalAssetsLessCurrentLiabilities − NetAssets
short_term_debt      =  current_liabilities   (mirrors current liabilities; total-liabilities basis)
assets               =  TotalAssetsLessCurrentLiabilities + current_liabilities
liabilities          =  current_liabilities + long_term_debt
```

If either the current-liabilities inputs (`CurrentAssets`, `NetCurrentAssetsLiabilities`)
or the long-term input (`TotalAssetsLessCurrentLiabilities`) is absent, the whole block
is withheld. Emitting only one half would silently understate `total_debt` (the engine
computes it as `long_term_debt + short_term_debt`) — a wrong number is worse than none.

### Leverage is total-liabilities-based

As with the NO register, `short_term_debt` mirrors current liabilities and
`long_term_debt` mirrors non-current liabilities: `total_debt` equals **total
liabilities**, not pure financial borrowings (bonds, bank debt, leases). All gearing
metrics (`debt_to_equity`, `debt_to_assets`, `net_debt`, `net_cash`) inherit this
total-liabilities basis. FRC-tagged filings do not consistently expose a pure borrowings
breakdown, so this is the best available from the register.

### Coverage yields to correctness

Suppressed items and their reasons are always recorded in the coverage report. The
practical distribution in a typical bulk batch:

- ~72% of filings yield at least `net_assets` / `equity` rows
- Derived balance-sheet items (`assets`, `liabilities`) are available where TALCL and
  NetCurrentAssetsLiabilities are both tagged
- Revenue and P&L are present in ~1.3% of filings
- A filing that fails the primary gate is counted as `unbalanced` (not `no-financials`),
  so the distinction is queryable in the coverage report

## The `basis` field — UK

All UK rows carry `basis="company"`. UK group consolidated accounts are filed in the
same iXBRL format as standalone legal-entity accounts, but the engine does not currently
distinguish the two (the FRC taxonomy does not mandate a consistent basis-of-consolidation
tag). Consolidated detection — analogous to Brreg's `KONSERN` / `SELSKAP` distinction —
is deferred to a follow-up PR.

Note that `basis="company"` rows from Companies House and `basis="consolidated"` rows
from the EU ESEF pillar (`data/financials_eu/`) are already separated by output
directory. They must **not** be merged without an explicit GAAP and scope reconciliation:
FRS 102 / FRS 105 differs materially from IFRS on pension, deferred tax, and leases.

## Identity — CH number and LEI

**CH number (entity_id):** The `entity_id` is the CH number exactly as it appears in the
bulk ZIP filename — **no digit-stripping, no prefix removal, no normalisation**. Scottish
(`SC`), Northern Irish (`NI`), and Limited Liability Partnership (`OC`) prefixes are
preserved verbatim, as are leading zeros on numeric CH numbers. Stripping or normalising
would conflate distinct companies or break lookups against the Companies House register.

**LEI resolution (deferred):** The bulk product does not include LEI data. All rows
currently carry `lei=null`. A future targeted-API PR will add optional LEI lookup via
GLEIF: the `registeredAs` field is accepted as a CH number only when
`entity.legalAddress.country == "GB"` — the same country guard as the NO register's
`"NO"` filter, so a LEI registered in a different jurisdiction never silently resolves to
a GB CH number.

## CLI usage — UK (`--ch-bulk`)

```bash
# dry-run (default): print summary, nothing written
bottom_up_corpus register-financials --ch-bulk accounts_monthly_2024_01.zip

# bounded test run: first 100 filings only (dry-run)
bottom_up_corpus register-financials --ch-bulk accounts_monthly_2024_01.zip --limit 100

# write data/financials_register/<ch_number>.jsonl + coverage report
bottom_up_corpus register-financials --ch-bulk accounts_monthly_2024_01.zip --write
```

`--write` is the only side-effecting flag. Omitting it is a safe dry-run that prints
entity / period / unbalanced counts without touching disk. `--limit N` caps the number
of filings processed and is available for `--ch-bulk` only (no equivalent for the NO
`--orgnrs` / `--leis` path).

Arelle must be installed for `--ch-bulk` to function. If Arelle is absent the command
raises a clear `ImportError` pointing to `pip install '.[eu-financials]'`. The first
filing in a batch triggers a ~14 s FRC taxonomy download; subsequent files are ~0.7 s
each (taxonomy cached in the shared Arelle controller).

## Honest caveats — UK

### ~25% of accounts are paper or PDF

Companies House estimates roughly a quarter of annual accounts are filed as scanned PDFs
or paper with no machine-readable XBRL content. These appear in the bulk ZIP as `.html`
wrappers with no inline XBRL tags. The engine records them as `status="no-financials"`
and **never attempts OCR extraction**. Only structured iXBRL filings are processed.

### One period per entity per bulk file

The monthly bulk product contains each company's most recently filed accounts as of the
cut date — one filing, one period. Multi-year history requires iterating the monthly
archive. Historic backfill is deferred.

### LLP, charity, and CIC frameworks not fully handled

Limited Liability Partnerships (LLPs), charities (FRS 102 charities), and Community
Interest Companies (CICs) file under distinct FRC sub-frameworks with different local
names. The current mapping (`concepts_uk.py`) targets the standard FRS 105 / FRS 102 /
IFRS company taxonomy. Filings under LLP/charity/CIC frameworks are processed but
typically yield fewer tagged concepts.

### Annual accounts only

UK statutory accounts are annual. `frequency` is always `"annual"`. There are no
quarterly or semi-annual periods.

### No commercial P&L fallback — ever

The corpus is primary open data only. Revenue and income for balance-sheet-only filers
are **never supplemented from commercial databases, press releases, or any non-public
source**. If an iXBRL filing carries no tagged revenue, there is no revenue row. This is
a deliberate design constraint, not a coverage gap to be filled.

---

## Out of scope — future PRs

The current implementation covers **Norway (Brreg)** (JSON, no XBRL) and **UK Companies
House** (iXBRL via Arelle, keyless bulk `--ch-bulk` path).

National registers not yet supported:

- **Belgium (BNB / Banque Nationale)** — XBRL
- **Denmark (Erhvervsstyrelsen / Virk)** — XBRL / iXBRL

These will require the **Tier B Arelle bridge** (already built for the ESEF pillar and
reused for UK; see [`EU_FINANCIALS.md`](EU_FINANCIALS.md)) to be adapted to each
register's taxonomy and delivery format.

For the **UK pillar** specifically, the following are deferred to follow-up PRs:

- **Targeted REST API** (named-entity acquisition with a free Companies House developer
  key — an alternative to bulk for targeted runs)
- **Historic monthly backfill** (iterating the monthly archive to build multi-year
  history per entity)
- **Consolidated-accounts detection** (distinguishing group accounts from
  legal-entity-only accounts within the iXBRL filing)
- **LEI resolution** (populating `lei` for GB entities via GLEIF `registeredAs`)

Coverage enrichment for the NO register (e.g. mapping orgnr to LEI for the full Brreg
population) is also deferred.
