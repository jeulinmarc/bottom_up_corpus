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

## Usage (Phase 0)

```bash
python -m bottom_up_corpus list-forms          # show the taxonomy
python -m bottom_up_corpus list-forms --forms A  # filter by family/codes
python -m bottom_up_corpus config              # effective runtime config
```

Discovery (`discover`, `fts`), download/decompose, XBRL (`xbrl`), completeness
(`report`), and `render-pdf` land in subsequent phases (see `docs/`).

## SEC fair access

The HTTP client sends a declared, contact-carrying `User-Agent` and throttles to
stay at/under the SEC's published limit of 10 requests/second.
