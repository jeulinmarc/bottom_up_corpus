# Register Financials — statutory accounts from national business registers

`bottom_up_corpus/registers/` ingests **open-data statutory accounts** from national
business registers and writes them in the same curated schema as the SEC and EU pillars.
This document covers the purpose, source, schema, honest caveats, and CLI for three
registers: **Norway's Brønnøysund Register Centre (Brreg)**, **UK Companies House**,
and **Belgium's BNB Central Balance Sheet Office (CBSO)**.

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

## Source — Belgium BNB CBSO annual accounts (dimensional XBRL)

Belgium's **Banque Nationale de Belgique Central Balance Sheet Office (BNB CBSO)**
collects mandatory annual statutory accounts from virtually all Belgian legal entities
and makes them available as **dimensional XBRL** instance documents. The XBRL model is
EBA/DPM-style: every monetary fact is qualified by its context's dimension members
(`bas`, `part`, `prd`, `ntr`, `rst`, `typ`, …) rather than carrying the meaning in the
element name. The CBSO uses a **version-stable `dict` member namespace**, so the
member-to-meaning mapping does not change across taxonomy releases.

The parser (`registers/bnb_xbrl.py`) uses **stdlib `xml.etree.ElementTree` only — no
Arelle, no taxonomy bundle**. Because meaning lives in the dimension members (not the
element names), the parser needs only to read context dimension members and numeric
values; it does not need the taxonomy at all. This makes it fast, dependency-free, and
resilient to taxonomy version changes.

### Two acquisition paths

| Path | Key required | Flag |
|---|---|---|
| **CBSO Authentic Data API** (targeted, per KBO) | Free subscription key | `--be-numbers` |
| **Local file** (downloaded `.xbrl` or deposit `.zip`) | None | `--be-file` |

**Authentic Data API:** The CBSO provides a free REST API at `https://ws.cbso.nbb.be`.
Registration is self-service at `https://developer.cbso.nbb.be`; the subscription key
is passed as `NBB-CBSO-Subscription-Key` header. The engine calls
`/authentic/legalEntity/{kbo}/references` to list available deposits, picks the most
recent by `DepositDate`, then fetches the accounting data. Live/scale validation —
rate limits, pagination, and quota behaviour for entities with large deposit histories —
is a **maintainer step** and is intentionally out of scope; the parser and pack are
validated against the public example filings.

**Keyless local-file path (`--be-file`):** A BNB deposit is either a bare `-data.xbrl`
file or a three-member deposit `.zip` (`*-contact.xbrl`, `*-data.xbrl`, `*-vendor.xbrl`).
Both are handled: the engine extracts the `*-data.xbrl` member automatically when it
receives a `.zip`. The KBO number is derived from the last underscore-delimited token
of the filename stem (e.g. `m02_full_0648822310.xbrl` → `"0648822310"`).

### Why BE is the richest register — borrowings-based leverage

BE is the **richest register in this corpus**. Unlike Norway (Brreg) and UK (Companies
House), which expose only broad liabilities aggregates, the BNB CBSO instance tags **real
financial borrowings** under the `bas:m51` rubric — bonds, bank loans, leases —
disaggregated by maturity (`rst`: long-term `m1` / short-term `m2`) and instrument type
(`typ`). The engine sums the validated `m51` tranches to compute `long_term_debt` and
`short_term_debt`, so:

- **`total_debt`** = financial borrowings (not total liabilities)
- **`debt_to_equity`** and **`debt_to_assets`** are **borrowings-based gearing**, not
  liabilities-based approximations

This is a qualitatively different and more informative metric than the liabilities-based
approximation produced for NO and UK. The `source` field (`"bnb"` vs `"brreg"` vs
`"companies_house"`) distinguishes the regimes in every output row. **Never compare
`debt_to_equity` from a BE row to a NO or UK row without first confirming that both
rest on the same debt basis.** The `source` field is the guard.

Additional richness: `revenue`, `dep_amort` (depreciation + amortisation), and
`net_income` + `income_tax` are all mapped — BE carries the full set of building blocks
for EBITDA derivation once `operating_profit` (m44) is unblocked (see Caveats). Depth
reaches approximately 18 years (2007 onwards). For comparison: Brreg typically provides
5–10 years and the UK bulk product provides one period per entity per monthly file.

## Schema — `data/financials_register/<kbo>.jsonl`

Output follows the same per-period row model as the NO and UK registers and the SEC/EU
pillars (see [`FINANCIALS.md`](FINANCIALS.md) for the full curated-concepts definition):

| Column | Value |
|---|---|
| `entity_id` | KBO enterprise number (10-digit zero-padded string) |
| `lei` | GLEIF LEI if resolved; `null` for the keyless `--be-file` path |
| `country` | `"BE"` |
| `source` | `"bnb"` |
| `basis` | `"company"` (statutory individual accounts; consolidated detection deferred) |
| `fy` | Fiscal year (integer, derived from `period_end`) |
| `frequency` | `"annual"` (CBSO holds only annual statutory accounts) |
| `currency` | Reporting currency from the XBRL unit (virtually always `"EUR"`) |
| `period_end` | ISO-8601 date (the maximum `endDate`/`instant` across all XBRL contexts) |
| `publication_date` | `null` (not extracted from the deposit) |

The concept pack mapped from the `m`-member dimensional space covers:

| Curated key | XBRL rubric | Notes |
|---|---|---|
| `assets` | `m25/m1` | Total balance-sheet assets (= total passif anchor) |
| `assets_fixed` | `m2/m1` | Fixed assets |
| `assets_current` | `m12/m1` | Current assets (BE-GAAP: includes receivables >1yr — see Caveats) |
| `cash` | `m23/m1` | Cash and cash equivalents |
| `inventory` | `m14/m1 sts=m2` | On-balance-sheet inventory |
| `receivables` | `m9/m1 rst=m2` | Short-term receivables |
| `equity` | `m37/m3 ntr=m4` | Shareholders' equity |
| `provisions` | `m47/m3` | Provisions (BE-GAAP: between equity and liabilities) |
| `liabilities` | `m50/m3` | Total liabilities |
| `liabilities_current` | `m50/m3 rst=m2` | Current liabilities |
| `revenue` | `m53/m4 ntr=m6` | Turnover |
| `net_income` | `m59/m4` | Period net result (breakdown-free; see note on pre-tax below) |
| `income_tax` | `m60/m4 spec=m17` | Income tax |
| `dep_amort` | `m2/m4 ntr=m6 mdp=m1` | Depreciation and amortisation |
| `long_term_debt` | derived from `m51` | Real financial borrowings, LT — see Confidence Gate |
| `short_term_debt` | derived from `m51` | Real financial borrowings, ST — see Confidence Gate |

Each `kind="reported"` row carries the source dimension selector as its `tag`
(e.g. `"m51 (derived, x-checked)"`), so downstream consumers can inspect exactly which
XBRL rubric fed each value.

The derived block produced from this pack includes (for a full filer):

- **Aggregates:** `total_debt` (borrowings), `net_debt`, `net_cash`, `working_capital`,
  `invested_capital`
- **Profitability:** `net_margin`, `roe`, `roa`
- **Leverage / liquidity:** `debt_to_equity` (borrowings), `debt_to_assets` (borrowings),
  `current_ratio`, `quick_ratio`, `cash_ratio`
- **Efficiency:** `asset_turnover`, `dso`

EBITDA and EBITDA-dependent metrics (`ebitda`, `ebitda_margin`, `net_debt_to_ebitda`)
are **not emitted** in the current release — `operating_profit` (m44) is suppressed
pending a second real validated example. When it is unblocked, the `dep_amort` mapping
already in place means EBITDA will be automatically computable without further work.

A coverage report is written to `data/reports/register_coverage.jsonl` for every entity
processed: `status="ok"` with a period count, `"no-financials"` (no usable facts after
gating), `"unbalanced"` (primary gate rejected the filing), or `"error"` (parse
exception). When items are suppressed by the confidence gate or the always-suppress
list, a `suppressed` list records each key and the reason.

## The confidence gate — no false data

**Governing principle:** a number known to be wrong must never be emitted; a missing
number is strictly better than a wrong one. The BNB instance is dimensional — meaning
lives in the dimension members, not the element names — so the central risk is **picking
a disaggregated fact instead of the total**. For example, `m59/m4 spec=m16` is the
pre-tax result while the **breakdown-free** `m59/m4` (no `spec` member) is the true
net result. The engine guards against this systematically.

### Canonical-member selection

For every curated key, the engine selects the **unique** fact whose `dims` equals
exactly `{bas, part, prd:m1} ∪ required-members` and carries **no other dimension**.
A disaggregated sub-breakdown (which carries an extra member like `spec=m16` or
`rst=m1`) can never match this exact set, so it can never masquerade as the total.
If 0 or more than 1 such fact exists — the total is ambiguous — the key is suppressed
and the reason recorded. The engine never defaults a missing value to zero.

### Primary gate: `m25/m1 == m25/m3`

Total assets (`m25/m1`) and total passif (`m25/m3`) are independently tagged in the
instance document. Under BE-GAAP, passif = equity + provisions + liabilities (note:
provisions sit between equity and liabilities, unlike the IFRS balance sheet where they
sit within liabilities — do not confuse the structures). If total assets and total passif
disagree beyond `max(2 EUR, 0.5% of the larger figure)`, the **entire filing is
rejected**: `status="unbalanced"`, no values emitted. A balance sheet that does not close
is untrustworthy by construction.

### Financial-debt cross-check — the borrowings guarantee

The `m51` rubric tags financial borrowings disaggregated by maturity (`rst`) and
instrument type (`typ`). The engine:

1. Collects all balance-sheet `m51` tranches (`bas=m51, ntr=m3, part=m3, prd=m1`),
   excluding the subordinated `sts` cross-cut that would double-count across tranches.
2. Checks that every remaining fact has **exactly** the expected dimension set
   `{bas, ntr, part, prd, rst, typ}` and a maturity bucket in `{m1, m2}`. A deviating
   structure (a subtotal without `typ`, a further sub-breakdown, an unexpected bucket)
   means the total cannot be confirmed — suppress the entire debt block.
3. Sums LT (`rst=m1`) and ST (`rst=m2`) tranches separately.
4. **Independent cross-check:** the financial-nature slice of total liabilities
   (`m50[ntr=m3]`, breakdown-free `rst=m1 + rst=m2` on the passif) is a **different
   rubric** from `m51`, so it is a genuinely independent witness. The `m51` sum must
   reconcile with this witness within `max(2 EUR, 0.5%)`.
5. Only if the cross-check passes are `long_term_debt` and `short_term_debt` emitted —
   **atomically**: if either half fails, neither is emitted. When the block is
   suppressed, the engine falls back to liabilities-based leverage (from the `m50`
   pack members) rather than emit a possibly-wrong borrowings figure.

### `operating_profit` — always suppressed

`operating_profit` (m44) is in the always-suppress list because its label is ambiguous
on the one validated real filing available — a second real example is required before
this concept can be safely unblocked. The reason is recorded in every filing's
`suppressed` list. This has no knock-on effect on `net_income` (independently tagged as
`m59/m4` breakdown-free) or any other concept.

### Pre-tax result is not net income

The dimensional model makes this explicit: `m59/m4 spec=m16` is the pre-tax result
(a disaggregation); the breakdown-free `m59/m4` is the net result. Because the
canonical-member selector requires an exact dimension match, the pre-tax breakout
cannot match the `net_income` selector and is automatically excluded. There is no risk
of the pre-tax figure being misreported as net income.

## The `basis` field — BE

All BE rows carry `basis="company"`. The BNB CBSO instance document embeds a
consolidation-model indicator (`m120`) that distinguishes individual accounts from
consolidated group accounts, but consolidated-model detection is deferred to a follow-up
PR. Until then, both individual and consolidated filings are emitted with
`basis="company"`.

Note that `basis="company"` rows from the BNB CBSO register and
`basis="consolidated"` rows from the EU ESEF pillar (`data/financials_eu/`) are already
separated by output directory. They must **not** be merged without an explicit accounting
and scope reconciliation: BE-GAAP differs materially from IFRS on lease accounting,
pension, deferred tax, and the treatment of provisions; and the obligor universe covered
by the register (all Belgian legal entities above the filing threshold) overlaps only
partly with the ESEF pillar (listed EU groups that issued regulated debt or equity).

## Identity — KBO and LEI

**KBO enterprise number (`entity_id`):** The KBO (Kruispuntbank van Ondernemingen)
enterprise number is the canonical Belgian legal-entity identifier — a 10-digit
zero-padded string (e.g. `"0648822310"`). It is always normalised to 10 digits
(stripping non-digits, left-zero-padding).

- **`--be-file` path:** the KBO is derived from the filename (last
  underscore-delimited stem token), so the file must be named consistently with the
  CBSO deposit convention.
- **`--be-numbers` path:** KBO numbers are passed directly on the command line and
  normalised to 10 digits.

**LEI resolution (library level):** When a spec carries a GLEIF LEI, the engine calls
`GET https://api.gleif.org/api/v1/lei-records/{lei}` and extracts
`entity.registeredAs`. This is accepted as the KBO **only when**
`entity.legalAddress.country == "BE"` — there is no guess, no fuzzy match. A LEI
from a non-Belgian jurisdiction returns `status="unresolved"` in the coverage report.
LEI resolution is available at the library level (`build_be_financials`); the CLI
`--be-numbers` flag takes KBO numbers directly and does not call GLEIF. In both modes,
the resolved `lei` (if available) is carried through to every output row.

## CLI usage — BE

```bash
# keyless path: parse one or more local .xbrl or deposit .zip files (dry-run)
bottom_up_corpus register-financials --be-file m02_full_0648822310.xbrl

# keyless path: multiple files, persist to disk
bottom_up_corpus register-financials \
  --be-file deposit_0200068636.zip deposit_0648822310.zip --write

# API path: fetch via CBSO Authentic Data API (requires free subscription key)
export BNB_CBSO_KEY=<your-key>
bottom_up_corpus register-financials --be-numbers 0648822310 0200068636

# API path: persist
bottom_up_corpus register-financials --be-numbers 0648822310 --write
```

`--write` is the only side-effecting flag. Omitting it is a safe dry-run that prints
the entity / period / unbalanced counts without touching disk.

The `--be-file` and `--be-numbers` flags are mutually exclusive with each other and
with `--orgnrs`, `--leis`, and `--ch-bulk`.

**CBSO API key:** Register at `https://developer.cbso.nbb.be` (self-service, free). Set
the key in the `BNB_CBSO_KEY` environment variable before calling `--be-numbers`. The
key is passed as the `NBB-CBSO-Subscription-Key` header; the engine also sends a unique
`X-Request-Id` per request (CBSO requirement).

## Honest caveats — BE

### Leverage is borrowings-based (but compare with source-awareness)

The BE register is unique in this corpus in providing genuine financial-borrowings data
via `m51`. The derived `total_debt`, `debt_to_equity`, `debt_to_assets`, and `net_debt`
all rest on this borrowings basis — more precise than the total-liabilities basis used
for NO and UK. However:

- The `m50`-based fallback (liabilities-based) is used when the `m51` cross-check
  fails or the `m51` tranche structure deviates. The `source` and `tag` fields let
  downstream consumers identify which basis applies to a given row.
- **Never compare leverage metrics across registers without checking `source`:** a
  `debt_to_equity` from `source="bnb"` (borrowings) is not directly comparable to one
  from `source="brreg"` or `source="companies_house"` (liabilities-based).

### BE-GAAP statutory — separate from ESEF

BNB CBSO filings follow **BE-GAAP** (Belgian Generally Accepted Accounting Principles),
the statutory accounting standard for non-listed Belgian legal entities. Belgian listed
groups also file **ESEF consolidated accounts** under IFRS — those are ingested by the
EU ESEF pillar (`data/financials_eu/`) and must **never be merged** with BNB CBSO rows:
the GAAP regime, the consolidation scope, and the obligor population differ.

### BE-GAAP current-assets perimeter

Under BE-GAAP, **all trade receivables — including those due beyond one year — are
classified in current assets** (the `m12/m1` rubric). This means `assets_current`
includes long-dated receivables that IFRS would classify as non-current. Users comparing
BE current-asset figures to IFRS-based current assets for the same entity should be
aware of this classification difference; it affects `working_capital`, `current_ratio`,
`quick_ratio`, and any metric built on current assets.

### `operating_profit` suppressed

`operating_profit` (m44) is always suppressed in the current release — its label is
ambiguous on the one validated real filing and a second real example is required before
it can be safely unblocked. Consequently, `operating_margin`, `ebitda`, `ebitda_margin`,
`nopat`, `roic`, `interest_coverage`, and `net_debt_to_ebitda` are not produced for BE
rows. The `dep_amort` concept is mapped and ready; EBITDA will be automatically
derivable once `operating_profit` is validated.

### Consolidated-model detection deferred

All rows carry `basis="company"`. The CBSO instance embeds a consolidation indicator
(`m120`) that can distinguish individual from consolidated accounts, but reading it is
deferred to a follow-up PR. Until then, a consolidated filing processed via the BE
path will be tagged `basis="company"` — an inaccurate label that may mislead downstream
queries filtering by `basis`.

### Annual accounts only

BNB CBSO holds statutory annual accounts. There are no quarterly or semi-annual periods.
`frequency` is always `"annual"`.

### Live/scale validation requires the free CBSO key

The `--be-numbers` path requires the CBSO Authentic Data API subscription key, which is
free but requires self-service registration. The parser and concept pack are validated
against the public example filings published by the BNB; rate-limit behaviour,
pagination for entities with large deposit histories, and key-quota handling are
maintainer steps, not exercised in the unit tests.

---

## Source — Finland PRH avoindata XBRL open API

Finland's **Patent and Registration Office (PRH)** publishes statutory annual accounts
as **dimensional XBRL** instance documents on its **avoindata open-data platform**. The
API is fully public:

```
GET https://avoindata.prh.fi/opendata-xbrl-api/v3/financial?businessId=…&financialDate=…
```

**No API key, no registration, no authentication required** — every endpoint is
keyless. Two supporting endpoints cover entity history and population traversal:

| Endpoint | Purpose |
|---|---|
| `GET /v3/financials?businessId=…` | List all available `financialDate` strings for one entity |
| `GET /v3/all_financials?financialDate=…` | Iterate all companies that filed for a given date (paginated, 100/page) |

The XBRL model is dimensional: every monetary fact sits in an element of the
`fi_met` / `fi_md103` namespace whose `contextRef` attribute links to a context
carrying the metric code as a `fi_dim:MCY` member (e.g. `fi_MC:x673` → revenue).
Comparative-period facts are distinguished by a `fi_dim:REF` member in the scenario;
the parser (`registers/fi_prh_xbrl.py`) keeps **current-period facts only**.

The parser uses **stdlib `xml.etree.ElementTree` only — no Arelle, no taxonomy
bundle**. Because meaning lives in the MCY dimension members (not the element names),
the parser needs only to read the MCY integer and the numeric value. This makes it
fast, dependency-free, and resilient to future taxonomy version changes.

Accounts filed at the PRH follow **Finnish GAAP (FAS)** — the statutory accounting
standard for Finnish legal entities filing individual (non-consolidated) accounts. FAS
differs materially from IFRS in the treatment of appropriations, tax, and provisions
(see the confidence gate below). The PRH register covers the **SME and private
universe**; large listed Finnish groups file consolidated ESEF accounts separately
(ingested by the EU ESEF pillar — never merged here).

## Schema — `data/financials_register/<ytunnus>.jsonl`

Output follows the same per-period row model as the NO/UK/BE registers and the
SEC/EU pillars (see [`FINANCIALS.md`](FINANCIALS.md) for the full curated-concepts
definition):

| Column | Value |
|---|---|
| `entity_id` | Y-tunnus (string; `NNNNNNN-N` format preserved, e.g. `"2919415-2"`) |
| `lei` | GLEIF LEI if resolved; `null` for the keyless `--fi-file` path |
| `country` | `"FI"` |
| `source` | `"prh"` |
| `basis` | `"company"` (FAS individual statutory accounts) |
| `fy` | Fiscal year (integer, derived from `period_end`) |
| `frequency` | `"annual"` (PRH holds only annual statutory accounts) |
| `currency` | Reporting currency from the XBRL unit (virtually always `"EUR"`) |
| `period_end` | ISO-8601 date string (instant of the current-period context) |
| `publication_date` | `null` (not exposed by the PRH API) |

The concept pack mapped from the `fi_MC` dimensional space covers:

| Curated key | fi_MC code | Notes |
|---|---|---|
| `revenue` | x673 | Turnover |
| `operating_income` | x689 | Operating result |
| `net_income` | x740 | **Final result after appropriations — never x738** (see gate) |
| `interest_expense` | abs(x4046) | Stored negative in the filing; taken as absolute value |
| `assets` | x360 | Total balance-sheet assets |
| `equity` | x435 | Shareholders' equity |
| `liabilities` | x513 | Total liabilities (confirmed total; maturity split suppressed — see gate) |
| `personnel_costs` | x1869 | Staff costs |
| `non_current_assets` | x376 | Fixed / non-current assets (may be negative; see gate) |
| `assets_current` | x424 | Current assets |

Each `kind="reported"` row carries its `fi_MC` code as `tag` (e.g. `"fi_MC:x360"`).

The derived block produced from this pack includes (for a full filer):

- **Margins:** `operating_margin`, `net_margin`
- **Returns:** `roa`, `roe`
- **Coverage:** `interest_coverage`
- **Efficiency:** `asset_turnover`

FI produces **no** `total_debt`, `net_debt`, `net_cash`, `debt_to_equity`,
`debt_to_assets`, `working_capital`, `current_ratio`, `quick_ratio`, or `cash_ratio`
— see the confidence gate below for the precise reasons. There is no `ebitda` (no
D&A in the pack) and no `effective_tax_rate` (income tax suppressed).

A coverage report is written to `data/reports/register_coverage.jsonl` for every
entity processed: `status="ok"` with a period count, `"no-financials"` (no usable
facts after gating), `"unbalanced"` (primary balance gate failed), `"unresolved"`
(identity lookup failed), or `"error"` (parse exception). Suppressed items and their
reasons are always recorded in the `suppressed` list.

## The confidence gate — no false data

**Governing principle (from `concepts_fi.py`):** a number we cannot confirm must
never be emitted; a missing number is strictly better than a wrong one. Two
Finnish-specific traps shape the gate:

### The net-income trap — x740, never x738

Under Finnish GAAP, the P&L routes tax-like charges through **appropriations**
(`x541`). The pre-appropriations result `x738` is therefore **not** net income; the
final bottom line is `x740` (result after appropriations): `x740 = x738 + x541`.
The engine maps `net_income` to `x740` exclusively and verifies a **two-leg waterfall**
before emitting it:

- **Leg 1** (when both `x12` net financial items and `x738` are present):
  `|x689 + x12 − x738| ≤ tol` — if this fails, the P&L is untrusted and
  `net_income` is suppressed immediately.
- **Leg 2** (when `x738` is present):
  `|x738 + (x541 or 0) − x740| ≤ tol` — if this fails, the appropriations step is
  inconsistent and `net_income` is suppressed.
- When `x738` is absent both legs are skipped and `x740` is emitted directly (many
  abbreviated filings do not tag the intermediate result).

If either leg fails, `net_income` is suppressed. The engine **never falls back to
`x738`** — a pre-appropriations value labeled as net income would be a false figure
for any entity carrying appropriations.

Tolerance: `max(2 EUR, 0.5% of the scale)` — calibrated to the three real
fixtures (full, abbreviated, housing-company).

### Primary gate: `x360 == x435 + (x513 or 0)`

Total assets must equal equity plus total liabilities within tolerance. Mismatch →
the entire filing is untrustworthy: `unbalanced=True`, no values emitted at all.
When `x360` or `x435` is absent the gate cannot run and the parser proceeds
(directly-tagged values still stand), mirroring the BE/UK siblings.

### Asset decomposition gate: `x376 + x424 == x360`

When both asset components are present, the engine verifies their sum equals total
assets. On mismatch, **both `non_current_assets` and `assets_current` are suppressed**
(the gate-verified total `x360` still stands). Importantly: **`x376` (non-current
assets) may be negative** — Finnish housing companies regularly carry negative fixed
assets due to accumulated depreciation exceeding gross cost. This is structurally
correct under FAS; the engine accepts negative `x376` without a positivity check.

### The leverage trap — maturity split suppressed, `liabilities` reported as-is

The PRH instance documents carry `x583` and `x816` as the two maturity buckets of
total liabilities (`x513`), and these buckets reconcile to `x513` to the cent. The
problem is that the **PRH instance carries no label linkbase** — the taxonomy that
names which bucket is long-term vs short-term is external and not shipped with the
filing. There is therefore **no way to confirm from the data which of `x583`/`x816`
is long-term and which is short-term**.

Emitting a guessed `long_term_debt` / `short_term_debt` split would be a false
maturity claim. Mapping the total into a single maturity bucket would be an equally
false "all one maturity" assertion. Per the no-false-data governing principle the
engine therefore **suppresses the maturity split entirely** and records the reason.

Consequences:

- FI emits `liabilities` (`x513`) as a directly-tagged reported value.
- **No** `long_term_debt` or `short_term_debt` are emitted.
- **No** `total_debt`, `net_debt`, `net_cash`, `debt_to_equity`, or `debt_to_assets`
  are produced (the engine's leverage engine requires the maturity split).
- An authoritative PRH codelist confirming the `x583`/`x816` label assignment would
  re-enable the split under the existing reconciliation gate without any schema
  changes — this is the natural fast-follow.

### Always suppressed

| Key | Reason |
|---|---|
| `income_tax` | FAS routes tax through appropriations (x541); no confirmed income-tax line (x448 is not it) |
| `cash` | x438 cash semantics are ambiguous under FAS |
| `financial_debt` | No reliable bank-borrowings vs trade-payables split — suppressed |
| `provisions` | No confirmed provisions code on FAS filings |

## The `basis` field — FI

All FI rows carry `basis="company"`. PRH statutory accounts are individual legal-entity
accounts under FAS. The register does not expose consolidated group accounts; Finnish
groups file consolidated ESEF accounts separately.

Note that `basis="company"` rows from the PRH register and `basis="consolidated"` rows
from the EU ESEF pillar (`data/financials_eu/`) are already separated by output
directory. They must **never** be merged: FAS differs materially from IFRS (treatment
of appropriations, depreciation, pension, and deferred tax); the obligor population
(Finnish private companies) overlaps only partly with the ESEF pillar (Finnish listed
groups that issued regulated securities).

## Identity — Y-tunnus and LEI

**Y-tunnus (`entity_id`):** The Finnish business identifier — format `NNNNNNN-N`
(7 digits, hyphen, 1 check digit, e.g. `"2919415-2"`). Whitespace is stripped; the
hyphen and check digit are preserved verbatim. No leading-zero normalisation is
applied (the check digit is structurally distinct from the 7-digit body).

Two input modes:

- **`--fi-file` path:** the Y-tunnus is extracted from the filename using the
  `NNNNNNN-N` pattern (e.g. `fi_2919415-2_full_2024.xml` → `"2919415-2"`).
- **`--fi-businessid` path:** Y-tunnus values are passed directly on the command line.

**LEI resolution:** When a spec carries a GLEIF LEI, the engine calls
`GET https://api.gleif.org/api/v1/lei-records/{lei}` and extracts
`entity.registeredAs`. This is accepted as the Y-tunnus **only when**
`entity.legalAddress.country == "FI"` — the same country guard as the NO/BE
registers. A LEI from a non-Finnish jurisdiction returns `status="unresolved"` in
the coverage report. In both modes, the resolved `lei` (if available) is carried
through to every output row.

## CLI usage — FI

```bash
# keyless local path: parse one or more PRH XBRL .xml files (dry-run)
bottom_up_corpus register-financials --fi-file fi_2919415-2_full_2024.xml

# keyless local path: multiple files, persist to disk
bottom_up_corpus register-financials \
  --fi-file fi_2919415-2_full_2024.xml fi_0100379-9_abbrev_2023.xml --write

# keyless API path: fetch latest period via PRH open API (no key required)
bottom_up_corpus register-financials --fi-businessid 2919415-2

# keyless API path: multiple entities, persist
bottom_up_corpus register-financials --fi-businessid 2919415-2 0100379-9 --write
```

`--write` is the only side-effecting flag. Omitting it is a safe dry-run that prints
the entity / period / unbalanced counts without touching disk. The `--fi-file` and
`--fi-businessid` flags are mutually exclusive with each other and with `--orgnrs`,
`--leis`, `--ch-bulk`, `--be-file`, and `--be-numbers`.

**API path behaviour:** for each Y-tunnus, the engine calls `GET /v3/financials` to
list available dates, picks the latest, then fetches that period's XBRL via
`GET /v3/financial`. A missing or erroring entity is recorded as `"no-financials"`
or `"error"` in the coverage report and never silently dropped.

## Honest caveats — FI

### Finnish GAAP (FAS), not IFRS — never merge with ESEF

PRH statutory accounts follow **Finnish GAAP (FAS)** — individual legal-entity
accounts for the private and SME universe. Finnish listed groups also file
**ESEF consolidated accounts** under IFRS; those are ingested by the EU ESEF pillar
(`data/financials_eu/`) and must **never be merged** with PRH rows. The GAAP regime,
consolidation scope, and entity population are all different.

### No leverage ratios (maturity-split fast-follow)

FI produces `liabilities` but **no `total_debt`, `debt_to_equity`, or
`debt_to_assets`** (see the confidence gate above). This is not a permanent
constraint: an authoritative PRH codelist confirming the `x583`/`x816` long/short
assignment would re-enable the maturity split under the existing reconciliation gate.
Until that codelist is confirmed, cross-register leverage comparisons should note that
FI rows carry no borrowings-based or liabilities-split gearing metric.

### Digital XBRL coverage is growing (~5% of registered companies)

The PRH avoindata platform holds XBRL filings from 2019 onwards. At present,
roughly 5% of Finnish registered companies have filed at least one digital XBRL
return — the remainder file PDF accounts. Coverage is growing year-on-year as larger
companies are brought into scope, but the universe today is a subset, not a census,
of Finnish companies.

### SME and private universe — large listed companies use ESEF

Finnish listed companies with securities admitted to trading on a regulated market are
required to file ESEF iXBRL consolidated accounts under IFRS. Their consolidated
figures appear in `data/financials_eu/`. The PRH avoindata register captures their
**individual (statutory entity) accounts** under FAS — a distinct and narrower filing
that is the obligor entity's own accounts, not the economic group.

### Annual accounts only

PRH statutory accounts are annual. `frequency` is always `"annual"`. There are no
quarterly or semi-annual periods in the avoindata XBRL API.

### No commercial fallback — ever

If the PRH API returns no XBRL filing for an entity, the entity is recorded as
`"no-financials"`. Revenue, income, and balance-sheet figures are **never
supplemented from commercial databases or any non-public source**. Open data only.

See also: [NO (Brreg)](#source--brreg-regnskapsregisteret-norway),
[UK (Companies House)](#source--uk-companies-house-statutory-accounts-ixbrl),
[BE (BNB CBSO)](#source--belgium-bnb-cbso-annual-accounts-dimensional-xbrl),
[`FINANCIALS.md`](FINANCIALS.md), [`EU_FINANCIALS.md`](EU_FINANCIALS.md).

---

## Out of scope — future PRs

The current implementation covers **Norway (Brreg)** (JSON, no XBRL), **UK Companies
House** (iXBRL via Arelle, keyless bulk `--ch-bulk` path), **Belgium BNB CBSO**
(dimensional XBRL, stdlib only; keyless `--be-file` + `--be-numbers` API path), and
**Finland PRH** (dimensional XBRL, stdlib only; fully keyless `--fi-file` +
`--fi-businessid` paths).

National registers not yet supported:

- **Denmark (Erhvervsstyrelsen / Virk)** — XBRL / iXBRL

Denmark will require the **Tier B Arelle bridge** (already built for the ESEF pillar and
reused for UK; see [`EU_FINANCIALS.md`](EU_FINANCIALS.md)) to be adapted to the Danish
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
