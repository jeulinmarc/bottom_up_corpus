# Register Financials — statutory accounts from national business registers

`bottom_up_corpus/registers/` ingests **open-data statutory accounts** from national
business registers and writes them in the same curated schema as the SEC and EU pillars.
This document covers the purpose, source, schema, honest caveats, and CLI for the
first register: **Norway's Brønnøysund Register Centre (Brreg)**.

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

Beyond the identity/period columns, each row is one of the three standard row kinds:
`kind="reported"` (the mapped curated concepts with their source Brreg field name as
`tag`), `kind="derived"` (single-period metrics), and `kind="derived_ttm"` (TTM ratios
— annual-only, so equivalent to `derived` here). The same shared engine
(`rows_from_base` → `compute_derived`) runs verbatim.

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

As a result:

- `debt_to_equity`, `net_debt_to_ebitda`, `cfo_to_debt` and other leverage metrics are
  **liabilities-based approximations**, not pure-borrowings ratios.
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

## Out of scope — future PRs

The current implementation covers **Norway (Brreg) only**, using a direct JSON-to-key
mapping with no XBRL processing.

Several other national registers expose statutory accounts in **structured XBRL or iXBRL**
format — most notably:

- **Belgium (BNB / Banque Nationale)** — XBRL
- **Denmark (Erhvervsstyrelsen / Virk)** — XBRL / iXBRL
- **United Kingdom (Companies House)** — iXBRL

These registers will require the **Tier B Arelle bridge** (already built for the ESEF
pillar; see [`EU_FINANCIALS.md`](EU_FINANCIALS.md)) to parse the XBRL/iXBRL packages
into the OIM-JSON fact format that the shared financial engine consumes. That extension
is earmarked for a dedicated follow-up PR and is **not** part of the current
implementation.

Bulk crawling (fetching accounts for a full register extract rather than named
org numbers) and coverage enrichment (e.g. mapping orgnr to LEI for the full
register population) are also deferred.
