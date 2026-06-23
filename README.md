# bottom_up_corpus

An exhaustive, replicable, **open-data** corpus of **company** primary-source
documents — the *bottom-up / micro* layer that complements
[`cb_corpus`](https://github.com/jeulinmarc/cb_corpus) (the central-bank *macro*
layer). Both corpora feed the same downstream RAG stack
(`mvp-graph-rag` / `eigenmind`) via `RAGDataOrchestrator`.

The anchor source is **SEC EDGAR** (all filings are public-domain open data),
with the architecture designed to add other open company-disclosure systems
later (Japan EDINET, Korea DART, EU ESEF / filings.xbrl.org, UK Companies House,
France INPI, Brazil CVM, …).

## Core principles (shared with cb_corpus)

- **Official primary sources only** — every document comes from the issuer's
  regulator of record (here, `*.sec.gov`). Provenance is recorded per filing.
- **Replicability** — stable, date-independent document ids; idempotent,
  convergent multi-round crawls; deterministic on-disk layout.
- **Exhaustivity** — discovery via EDGAR's own indices/APIs; a completeness
  matrix reconciles downloaded vs. expected per issuer/form/year; discovery
  errors are logged, never silently dropped.
- **Open data** — no proprietary datasets, machine translations, or
  model-generated text.

## Filing taxonomy (families, analog of cb_corpus A–G)

| Family | Codes | Forms |
|---|---|---|
| A. Periodic reports | A1–A4 | 10-K, 10-Q, 20-F, 40-F |
| B. Current / material events | B1–B2 | 8-K, 6-K (incl. earnings-release exhibits) |
| C. Governance | C1–C2 | DEF 14A, other proxy |
| D. Registration / offering | D1–D3 | S-1, S-4, 424B |
| E. Ownership (structured, opt-in) | E1–E3 | Forms 3/4/5, 13F, SC 13D/G |
| F. Structured financials (opt-in) | F1 | XBRL company facts / financial statement datasets |

Default crawl scope (`FULL_SCOPE`) is the narrative families **A–D**; E and F
are opt-in.

## Storage layout & file naming

```
data/
├── manifest/<cik>.jsonl        # INDEX: one JSON line per filing (committed) — the map
├── universe/<name>.jsonl       # curated issuer lists, ticker<->cik (committed)
│   └── <name>_changes.jsonl    # dated index changes (e.g. sp500_changes.jsonl)
├── financials/<cik>.jsonl      # normalized XBRL metrics, one row per metric (committed)
├── ownership/<cik>.jsonl       # normalized insider/13F rows (committed)
├── discovery_errors.jsonl      # append-only audit trail
├── reports/                    # completeness matrices, CSV exports
└── raw/<cik>/<code>/<year>/    # the actual documents (git-ignored, regenerable)
```

The layout is *machine-first* (stable ids, short codes), so **use the manifest as
your entry point — don't browse `raw/` by hand**: every manifest line already
contains the exact file paths.

### The path `raw/<cik>/<code>/<year>/<doc_id>.<type>`

- **`<cik>`** — SEC Central Index Key, **zero-padded to 10 digits** (Apple =
  `0000320193`). Permanent (never changes on rename); also the manifest filename.
- **`<code>`** — the **internal family code** (not the raw SEC form):

  | Code | Form | | Code | Form |
  |---|---|---|---|---|
  | A1 | 10-K | | C1 | DEF 14A |
  | A2 | 10-Q | | C2 | other proxy |
  | A3 | 20-F | | D1/D2/D3 | S-1 / S-4 / 424B |
  | A4 | 40-F | | E1 | Form 3/4/5 (insider) |
  | B1 | 8-K | | E2 | 13F (holdings) |
  | B2 | 6-K | | E3 | SC 13D/G |
  |  |  | | F1 | XBRL financials |

- **`<year>`** — filing year (`filing_date`).
- **`<doc_id>`** — `sha1(cik | code | accession)` truncated to 16 hex. **Stable**
  (date corrections don't change it) and unique per filing; it's the shared stem
  of all that filing's files.

### Files for one filing (shared `<doc_id>`)

| Suffix | Contents |
|---|---|
| `<doc_id>.submission.txt` | the **complete submission** (SGML: primary doc + all exhibits + XBRL) — canonical archive |
| `<doc_id>.primary.{htm,html,xml}` | the decomposed **primary document** (or the generated summary for F1/E) |
| `<doc_id>.txt` | the cleaned **extracted text** (what the RAG reads) |
| `<doc_id>.pdf` | the rendered **PDF** (after `render-pdf`) |

Special cases: **F1** (financials) has no submission — instead
`raw/<cik>/F1/companyfacts.json` (raw XBRL, one per issuer) plus a generated
`.primary.html`/`.txt` summary per period. **E1/E2** (ownership) download the
submission, then replace the primary/text with a structured summary
(insider transactions, or 13F holdings).

### The manifest is the map

`data/manifest/<cik>.jsonl` — one JSON object per filing, carrying the metadata
**and the exact file paths** so you never decode a hash by hand:

```jsonc
{
  "cik":"0000320193", "ticker":"AAPL",
  "company":"Apple Inc.",          // name as of the filing date (point-in-time)
  "company_current":"Apple Inc.", "entity_id":"",
  "form_type":"A1", "family":"A", "sec_form":"10-K",   // internal code + raw form
  "accession":"0000320193-25-000079", "title":"Apple Inc. 10-K ...",
  "filing_date":"2025-10-31", "period_of_report":"2025-09-27", "year":2025,
  "primary_doc_url":"https://www.sec.gov/Archives/edgar/.../aapl-...htm",  // EDGAR link
  "submission_url":"...0000320193-25-000079.txt",
  "sha256":"…", "provenance":"edgar_submissions",
  "local_path":"raw/0000320193/A1/2025/<doc_id>.submission.txt",
  "primary_path":"raw/0000320193/A1/2025/<doc_id>.primary.htm",
  "text_path":"raw/0000320193/A1/2025/<doc_id>.txt",
  "pdf_path":"raw/0000320193/A1/2025/<doc_id>.pdf",
  "doc_id":"<16-hex>"
}
```

The `financials/<cik>.jsonl` and `ownership/<cik>.jsonl` files are flat,
queryable tables (one row per metric / transaction / holding) — for analysis,
not navigation.

### Navigation recipes

```bash
# ticker -> CIK
grep -i '"AAPL"' data/universe/sp500.jsonl

# list a company's 10-Ks with their cleaned-text paths
jq -r 'select(.sec_form=="10-K") | "\(.year)  \(.text_path)"' data/manifest/0000320193.jsonl

# all files of one filing share the doc_id stem:
ls data/raw/0000320193/A1/2025/<doc_id>.*
```

## Install & test

```bash
pip install -r requirements.txt
python -m pytest -q
```

Before any live crawl, set a real contact address for SEC fair-access
compliance:

```bash
export BOTTOM_UP_CORPUS_CONTACT="you@example.com"
```

There is **no default contact**. If `BOTTOM_UP_CORPUS_CONTACT` is unset, the
`User-Agent` carries only the tool name (`bottom_up_corpus/0.1`) and no email
address is sent — so cloning the repo never leaks anyone's address. The SEC asks
for a real contact, so set it before crawling.

## Usage

### Global options

These come **before** the subcommand and apply to every command:

```bash
python -m bottom_up_corpus --data-dir /data/corpus --contact you@example.com discover ...
```

- `--data-dir PATH` — corpus root holding `manifest/`, `raw/`, `universe/`, …
  (default: `./data`). Use it to target a corpus outside the working directory.
- `--contact EMAIL` — contact for the SEC `User-Agent`; overrides
  `$BOTTOM_UP_CORPUS_CONTACT`.
- `--insecure` — disable TLS certificate verification. Use **only** behind a
  trusted SSL-inspection proxy (e.g. a corporate network that re-signs HTTPS); it
  turns off certificate validation for every request.

Inspection:

```bash
python -m bottom_up_corpus list-forms          # show the taxonomy
python -m bottom_up_corpus list-forms --forms A  # filter by family/codes
python -m bottom_up_corpus config              # effective runtime config
```

Issuer universe (curated tier, version-controlled under `data/universe/`):

```bash
# Resolve tickers -> CIKs via the official SEC map (dry-run, then --write).
python -m bottom_up_corpus build-universe --tickers AAPL,MSFT,GOOGL --name sp_curated --write
python -m bottom_up_corpus list-universe --name sp_curated

# Build the S&P 500 from its composition (the only index with open dated history):
python -m bottom_up_corpus build-universe --index sp500 --current-only --write   # today's ~500 members
python -m bottom_up_corpus build-universe --index sp500 --since 2010 --write      # historical UNION (all
#   companies that were ever members since 2010) + a dated data/universe/sp500_changes.jsonl
```

`--index sp500` reconstructs membership from Wikipedia (current table + the dated
changes table), so the historical union is **not survivorship-biased on
selection**. CIKs are filled for current/active members; since-delisted members
are kept with `cik=""` and reported (open data can't reliably map reused/retired
tickers). For survivorship-free *filing* coverage, `discover-index` (all filers)
remains the lever. Russell 1000 / Nasdaq-100 have no open dated source (not
supported).

Discovery (metadata into per-issuer manifests; **dry-run by default**, `--write`
to persist):

```bash
# Dry-run: see what would be indexed.
python -m bottom_up_corpus discover --universe sp_curated --years 2006-2025
# Persist manifests, multi-round until convergence:
python -m bottom_up_corpus discover --universe sp_curated --years 2006-2025 --write --rounds 3
# Or target specific CIKs / a wider scope (e.g. add ownership family E):
python -m bottom_up_corpus discover --ciks 320193 --forms A,B,C,D,E --write
# Discover then download in one step; --since/--until bound the download window
# (otherwise it downloads everything discovered — no implicit year cap):
python -m bottom_up_corpus discover --universe sp_curated --download --since 2015-01-01 --write
# Exhaustive (incl. delisted/merged filers) via the quarterly full-index:
python -m bottom_up_corpus discover-index --universe sp_curated --years 2006-2025 --write
```

### Company identity (rename / rebrand / merger)

A company's **CIK is permanent** — it never changes on a rename or rebrand — so
everything is keyed on CIK (manifests, `doc_id = sha1(cik|form|accession)`). On
top of that anchor:

- **Point-in-time naming.** Each filing is attributed to the name **in effect on
  its filing date** (from EDGAR `formerNames`), with the current name kept in
  `company_current`. A 2015 filing reads `Facebook Inc`; a 2023 one reads
  `Meta Platforms, Inc.` — same CIK throughout.
- **Cross-CIK entities.** Some events (holding-company restructures, mergers)
  span multiple CIKs that EDGAR does not link — e.g. Alphabet (CIK 1652044) is
  the successor to Google (CIK 1288776). A committed alias map
  (`data/entities/aliases.jsonl`) groups these; discovery expands a universe
  through it so one issuer pulls all its CIKs, and stamps each record with
  `entity_id`.

  ```bash
  python -m bottom_up_corpus entities                 # list grouped entities
  python -m bottom_up_corpus entities --cik 1288776   # resolve a CIK
  ```
- **Survivorship.** `company_tickers.json` lists *current* issuers only, so a
  ticker-built universe omits delisted/merged/failed companies. For historical
  coverage, anchor the universe on CIK or crawl the full index:

  ```bash
  # Include delisted/historical issuers by CIK (works when tickers no longer resolve):
  python -m bottom_up_corpus build-universe --ciks 1288776,320193 --name historical --write
  ```

Download + decompose (full submission → primary document → cleaned text):

```bash
python -m bottom_up_corpus download --universe sp_curated --write          # or: discover ... --download
# filter by period (filing year and/or exact dates) — also on render-pdf / ownership:
python -m bottom_up_corpus download --universe sp_curated --years 2010-2020 --write
python -m bottom_up_corpus download --universe sp_curated --since 2015-01-01 --until 2019-12-31 --write
```

Render PDFs (separate batch; needs Chrome/Chromium via `BOTTOM_UP_CORPUS_CHROME`
or PATH) and preview what the RAG would ingest:

```bash
python -m bottom_up_corpus render-pdf --universe sp_curated --years 2015-2025 --write
python -m bottom_up_corpus rag-items  --universe sp_curated --prefer pdf
```

Completeness matrix (discovered vs. expected per issuer/form/year):

```bash
python -m bottom_up_corpus report --universe sp_curated --years 2015-2025 --csv data/reports/matrix.csv
```

Structured financials (family F1) — curated XBRL metrics, one summary per
reporting period (annual/quarterly) with its publication date:

```bash
python -m bottom_up_corpus xbrl --universe sp_curated --years 2015-2025 --write
```

`--years` is an inclusive fiscal-year range (`2015-2025`) or a single year
(`2024`); omit it to keep every period.

This fetches SEC XBRL company facts and writes, per issuer: the raw
`companyfacts.json` (canonical), a normalized `data/financials/<cik>.jsonl`
table, and an HTML financial summary per period. Each summary is an F1 record
keyed on its **period end** (so prior-year comparatives land in their own period,
not the report's year) and stamped with the **publication date**. The summaries
flow through `render-pdf` and `rag-items` like any other document.

Each period carries ~40 **reported** line items (income statement, balance sheet
incl. all debt components and lease liabilities, cash flow, per-share) plus a
block of **derived metrics** computed from them and stored as `kind="derived"`
rows: total debt, total debt incl. leases, net debt, EBITDA, free cash flow,
working capital, tangible book value, the margin set (gross/operating/net/EBITDA/
FCF), returns (ROE/ROA), effective tax rate, leverage & coverage ratios
(debt/equity, debt/assets, net debt/EBITDA, interest coverage) and liquidity
ratios (current/quick/cash). A derived metric is emitted only when all of its
inputs are present, so a missing component is never treated as zero. Returns are
period-scoped (a quarter's ROE is the quarter's, not annualised), and ratios that
divide a balance-sheet stock by a period flow (net debt/EBITDA, asset turnover)
are emitted for annual periods only. Monetary values carry the issuer's reporting
currency (each row also records a `currency` field), so a non-USD filer is not
mislabelled as USD.

Ownership filings (family E) — structured insider transactions and institutional
holdings:

```bash
python -m bottom_up_corpus discover  --universe sp_curated --forms E --write
python -m bottom_up_corpus ownership --universe sp_curated --write
```

`ownership` downloads each E filing (canonical submission) and, for **Form 3/4/5**
(E1) and **13F** (E2), parses the structured XML into a readable summary
(insider/role/transactions; holdings/top-positions/portfolio value) that flows
through `render-pdf`/`rag-items`, plus a normalized `data/ownership/<cik>.jsonl`
table. **SC 13D/G** (E3) are narrative and pass through as text. Bounded to the
curated tier by default (Form 4 alone is ~4.6M filings universe-wide); use
`--limit` to cap a run.

## Feeding the RAG

The corpus plugs into the RAG stack (`mvp-graph-rag` / `eigenmind`) via
`RAGDataOrchestrator`. `bottom_up_corpus.rag.iter_items()` yields
`SourceItem(doc_id, path, payload)` straight from the manifests (PDF by default,
text fallback). The full contract + the ready-to-paste orchestrator connector are
in [`docs/INGESTION_RAG.md`](docs/INGESTION_RAG.md).

SEC EDGAR coverage is complete — narrative families A–D, ownership (E), and
structured XBRL financials (F1) all flow end-to-end. Still to come: the
international source adapters (Japan EDINET, Korea DART, EU ESEF / filings.xbrl.org,
UK Companies House, France INPI, Brazil CVM), each mapping to the same
`FilingRecord` schema.

## SEC fair access

The HTTP client sends a declared, contact-carrying `User-Agent` and throttles to
stay at/under the SEC's published limit of 10 requests/second.
