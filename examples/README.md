# Examples

One runnable script per file, each showing a single piece of `bottom_up_corpus`.
Run any of them from the repo root, e.g.:

```bash
# Set a contact so the SEC User-Agent carries one (fair-access politeness).
export BOTTOM_UP_CORPUS_CONTACT="you@example.com"

./venv/bin/python examples/01_resolve_universe.py
```

Scripts that hit SEC EDGAR are bounded (one issuer, a filing or two) and write any
artifacts to a temporary directory, so they leave the repo's `data/` untouched.
Several run **fully offline** (no network): `06`, `08`, `11`, `15`.

There is one runnable example per capability. See [`../docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md)
for how the pieces fit together.

**Core pipeline**

| Script | Shows |
|---|---|
| `01_resolve_universe.py` | Resolve tickers → CIKs via the official SEC ticker map |
| `02_discover_filings.py` | List an issuer's family-A filings (metadata only) |
| `03_download_and_extract.py` | Download one filing and extract clean RAG-ready text |
| `04_xbrl_financials.py` | Pull XBRL facts → a period summary + derived metrics |
| `05_rag_items.py` | Build a tiny corpus and iterate the `SourceItem`s the RAG ingests |

**Credit-universe resolution** (build a universe from an identifier file)

| Script | Shows |
|---|---|
| `06_universe_from_file.py` | Resolve a CSV of CIK/Ticker/CUSIP/ISIN — authority CIK > CUSIP6 > ticker + collision detection *(offline)* |
| `07_fts_resolution.py` | Reverse-lookup a CUSIP → issuer CIK via EDGAR full-text search (offering forms only) |
| `08_fts_cache.py` | The reusable CUSIP6→CIK cache `--fts-cache` reads/writes (merge + dedup) *(offline)* |
| `09_openfigi_enrich.py` | Enrich + triage ISIN/CUSIP via OpenFIGI (`registry_candidate` vs `private_placement`) |

**Other capabilities**

| Script | Shows |
|---|---|
| `10_ownership.py` | Structure insider Forms 3/4/5 + 13F into normalized rows (family E) |
| `11_entities.py` | Cross-CIK identity — expand one issuer to all its CIKs *(offline)* |
| `12_sp500_historical.py` | Build the S&P 500 as a historical union (`first_seen`/`last_seen`) |
| `13_discover_index.py` | Exhaustive discovery via the quarterly full-index, incl. delisted *(downloads a large index)* |
| `14_completeness_report.py` | Audit coverage with the completeness matrix (ok / partial / missing) |
| `15_name_resolution.py` | Resolve company names → CIKs via the SEC name index (former names, collision detection) *(offline)* |
