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

## Storage layout

```
data/
├── manifest/<cik>.jsonl       # per-issuer manifest (committed)
├── universe/                  # curated issuer lists (committed)
├── raw/<cik>/<form>/<year>/   # full submission + primary doc + text (git-ignored)
├── discovery_errors.jsonl     # append-only audit trail
└── reports/                   # completeness matrices, CSV exports
```

Each filing is stored as up to three layered artifacts: the **full
complete-submission `.txt`** (the exhaustive canonical artifact — primary
document + all exhibits + XBRL), the decomposed **primary document**, and a
cleaned **extracted-text** file for RAG. PDF rendering is a **separate batch
step** (like cb_corpus's `convert-html`), run only on chosen subsets.

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

## Usage

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
```

Discovery (metadata into per-issuer manifests; **dry-run by default**, `--write`
to persist):

```bash
# Dry-run: see what would be indexed.
python -m bottom_up_corpus discover --universe sp_curated --years 2006-2025
# Persist manifests, multi-round until convergence:
python -m bottom_up_corpus discover --universe sp_curated --years 2006-2025 --write --rounds 3
# Or target specific CIKs / a wider scope (e.g. add ownership family E):
python -m bottom_up_corpus discover --ciks 320193 --forms A,B,C,D,E --write
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

This fetches SEC XBRL company facts and writes, per issuer: the raw
`companyfacts.json` (canonical), a normalized `data/financials/<cik>.jsonl`
table, and an HTML financial summary per period. Each summary is an F1 record
keyed on its **period end** (so prior-year comparatives land in their own period,
not the report's year) and stamped with the **publication date**. The summaries
flow through `render-pdf` and `rag-items` like any other document.

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

Still to come: XBRL structured financials (`xbrl`) and the full international
source adapters (EDINET, DART, ESEF, …).

## SEC fair access

The HTTP client sends a declared, contact-carrying `User-Agent` and throttles to
stay at/under the SEC's published limit of 10 requests/second.
