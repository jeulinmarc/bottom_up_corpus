# EU Financials — IFRS structured financials from ESEF (Pillar B)

`bottom_up_corpus/eu/financials.py` extracts structured IFRS financial data from
the ESEF (European Single Electronic Format) reports indexed on
**filings.xbrl.org** — the closest European counterpart to the SEC's
`companyfacts` feed — and writes them in the same per-period row schema as the
SEC pillar. This is the reference for what is produced, how, and where coverage
ends.

See also: [`FINANCIALS.md`](FINANCIALS.md) (the SEC/US-GAAP reference that defines
the shared schema and engine) and [`EU_PILLAR.md`](EU_PILLAR.md) (the full EU
filing pillar — discovery, download, and identity resolution).

## Source — filings.xbrl.org OIM JSON

Every ESEF annual report is required by EU regulation to embed its IFRS facts as
machine-readable iXBRL. **filings.xbrl.org** (run by XBRL International) indexes
those reports and exposes each filing's facts as **OIM xBRL-JSON** via a
`json_url` — this is the "European companyfacts". The field is surfaced by the
`FilingsXbrlOrg` backend as a `kind="json_url"` file on each `Document`.

`facts_for_entity` iterates an issuer's filings, fetches each `json_url`, flattens
the facts with `flatten_oim_json`, and unions the resulting points across filings.
Because each annual report typically includes the current and prior-year comparative,
the union yields a multi-year time series; the engine's latest-filed rule resolves
restatements transparently.

## Schema — unified with the SEC pillar

The EU output uses the **same row model** as [`FINANCIALS.md`](FINANCIALS.md):
`kind="reported"`, `kind="derived"`, and `kind="derived_ttm"` rows (see that
document for full definitions of the ~60 curated concepts and all derived metrics).
The EU identity and period columns are mapped as follows:

| SEC pillar column | EU pillar column | Notes |
|---|---|---|
| `cik` | `lei` | GLEIF Legal Entity Identifier |
| `sec_form` | `doc_type` | e.g. `annual_report` |
| `accession` | `source` | filings.xbrl.org filing ID (`fxo_id`) |
| `sic` | — | not emitted (no EU SIC equivalent) |
| `is_financial` | `is_financial=null` | always null (no SIC to classify by) |

All other columns (`fy`, `frequency`, `currency`, `period_end`,
`publication_date`, concept keys, `tag`, `value`, `unit`, `sector_relevant`, …)
are identical. EU and SEC rows are therefore directly comparable in a single
analytical table.

Output path: `data/financials_eu/<LEI>.jsonl`

A coverage report is written to `data/reports/eu_financials_coverage.jsonl` for
every entity processed, listing its status (`ok` / `no-financials` / `unresolved`),
the period count, and the fiscal-year range. An issuer absent from filings.xbrl.org
appears as `no-financials` — never a silent drop.

## IFRS concept pack

The `IFRS_CONCEPTS` pack in `bottom_up_corpus/eu/ifrs_concepts.py` maps the same
curated keys as the SEC pack to `ifrs-full` tags (e.g. `Revenue`,
`ProfitLossFromOperatingActivities`, `CashFlowsFromUsedInOperatingActivities`,
`TotalEquity`). The shared engine (`summaries_from_flat`, `compute_derived`,
`attach_ttm_from_flat`) runs verbatim — the IFRS pack is a drop-in replacement for
the US-GAAP pack.

## Annual-only

ESEF mandates structured iXBRL for **annual reports only**. Half-year reports are
filed in human-readable PDF or HTML; pre-2020 financials predate the ESEF mandate.
Consequently:

- `frequency` is always `"annual"` for EU IFRS rows.
- TTM ratios (`kind="derived_ttm"`) are computed from the annual point only (no
  quarterly roll-up), so they equal the annual derived value — they are retained
  for schema consistency but carry no additional information beyond the annual.
- Half-year and pre-2020 coverage is a **deferred follow-up** (see below).

## Coverage bound

Coverage equals what **filings.xbrl.org** indexes. The main known gaps:

- **Germany (DE): absent.** The Bundesanzeiger does not expose structured ESEF OIM;
  German filers are not indexed on filings.xbrl.org in the `json_url` form, so DE
  returns `no-financials` in the coverage report.
- **Italy (IT): partial.** CONSOB's enforcement of the ESEF mandate has been
  uneven; a subset of Italian issuers appear on filings.xbrl.org, but coverage is
  incomplete relative to the CONSOB register.

Both gaps are **visible** in the coverage report (`data/reports/eu_financials_coverage.jsonl`),
never silent. The deferred Arelle Tier B PR (see below) is the intended path to
closing DE.

## CLI usage

```
bottom_up_corpus eu-financials --leis <LEI,...>           # dry-run: print summary, nothing written
bottom_up_corpus eu-financials --leis <LEI,...> --write   # write data/financials_eu/<LEI>.jsonl
bottom_up_corpus eu-financials --isins <ISIN,...> --write # resolve ISINs first, then write
```

Multiple LEIs or ISINs are comma-separated. `--write` is the only side-effecting
flag; omitting it is a safe dry-run that prints the entity/period count and the
coverage path without touching disk.

### One-issuer live validation

```
./venv/bin/python scripts/validate_eu_financials.py <LEI> --contact you@example.com
```

Prints the entity resolution, how many IFRS concepts mapped, and headline values
(`revenue`, `operating_income`, `net_income`, `assets`, `equity`, `cash`) for the
four most recent annual periods — suitable for eyeballing against the issuer's
published annual report. Pick a large, clean ifrs-full filer indexed on
filings.xbrl.org (check the `json_url` presence in its filings.xbrl.org record
first).

## Deferred follow-up PRs

Three known gaps are recorded as out-of-scope for the current implementation and
are earmarked for dedicated follow-up PRs:

**1. Arelle Tier B — close the DE (and other) coverage gap.**
Parse the structured ESEF `.xhtml`/`.zip` packages directly using Arelle's
iXBRL processor, rather than relying on the pre-extracted OIM JSON from
filings.xbrl.org. This would recover German issuers (and any others the aggregator
misses) from the raw ESEF packages that the `FilingsXbrlOrg` backend already
discovers.

**2. Phase 2 OCR — half-year reports, Switzerland, and pre-2020 financials.**
Half-year reports (PDF) and pre-ESEF filings (pre-2020) are not structured data;
extracting them requires OCR or PDF-table parsing. Swiss issuers have no OAM and
their historical filings predate ESEF entirely. A future OCR-based extraction
phase would extend the time series back and add semi-annual frequency rows.

**3. Register open-data backends — statutory accounts for the credit/private universe.**
For the credit and private-company universe (non-listed issuers, bank
counterparties), statutory accounts filed with national business registers
(Companies House in the UK, Infogreffe/BODACC in France, Handelsregister in DE,
etc.) are the primary data source. Wiring those as `OamSource` backends — or as a
parallel "statutory accounts" pillar — is the path to structured financials for
issuers that never file ESEF.

## Honest limitations

- **Coverage = filings.xbrl.org only.** An issuer not indexed there returns
  `no-financials`; it is recorded, never silently dropped.
- **Annual periods only.** Half-year, quarterly, and pre-2020 coverage requires
  the Phase 2 OCR work.
- **`is_financial` is always `null`** for EU rows: without a SIC code there is no
  automated way to detect financial-sector issuers. The `sector_relevant` flag on
  derived rows therefore defaults to `True` for all EU issuers; consumers of EU
  EBITDA/coverage ratios for banks should apply their own sector filter.
- **Currency is issuer-reported.** Most euro-area issuers report in EUR; UK/CH/SE
  issuers report in GBP/CHF/SEK. The engine filters to the dominant currency per
  filing and never mixes currencies across periods.
