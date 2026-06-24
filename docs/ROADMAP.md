# bottom_up_corpus — Roadmap & next steps

> Working notes, **not** committed. Living plan for what's done and what's next.

## Status — SEC-EDGAR pillar complete

All merged to `main`; 170 tests pass.

| Area | Status |
|---|---|
| Phase 0 — Scaffold (config, http, models, taxonomy) | ✅ |
| Phase 1 — Curated discovery (universe, edgar_submissions/index, report) | ✅ |
| Phase 2 — Download + decompose (storage, submission, extract, pipeline) | ✅ |
| Phase 3 — RAG handoff (`rag.iter_items`, `docs/INGESTION_RAG.md`) | ✅ |
| Phase 4 — XBRL financials F1 (financials, edgar_xbrl, `xbrl`) | ✅ |
| Phase 4b — Ownership E (ownership, `ownership`) | ✅ |
| Phase 6 — PDF batch (render, `render-pdf`) | ✅ |
| Cross-CIK entities; S&P 500 historical universe; CODEOWNERS | ✅ |
| Hardening (PRs #15–18) + docs reconciliation (#19) | ✅ |

Not started: **Phase 5 (international sources)**. Unbuilt SEC niceties:
`edgar_fts` (full-text search), `wayback` (dead-doc recovery), `adapters/` layer.

## Immediate operational task — S&P 500 family-A download

Validate the download pipeline at scale on real SEC data.

1. Build the S&P 500 universe: `build-universe --index sp500 --current-only --write`.
2. **Dry-run discover** family A to measure scope (issuers × filings) before any download.
3. Bounded real download (`--limit`) to confirm artifacts land correctly (submission +
   primary + clean text + manifest rows, sha256 stable).
4. Decide window/scope, then scale (respecting SEC ≤10 req/s; a real contact in the
   User-Agent is required).

Notes: family A = A1 10-K, A2 10-Q, A3 20-F, A4 40-F. Default discovery window is the
last 20 years; full-history A-forms for ~500 issuers is large (tens of thousands of
multi-MB submissions) — bound by `--years`/`--since` and/or `--limit` as needed.

## Phase 5 — first international source (next major work)

Add a non-US open EDGAR-equivalent behind the same `FilingRecord` schema + pipeline.

1. **Prep:** add `language` (default `en`) + `jurisdiction` fields to
   `models.FilingRecord`; thread into `rag._payload` + ingestion doc; add a free-API-key
   config knob (mirroring `BOTTOM_UP_CORPUS_CONTACT`). Confirm `sources/base.Source`
   is jurisdiction-agnostic (it is).
2. **Adapter:** new `sources/edinet.py` (Japan) or `dart.py` (Korea) — document-list
   endpoint → `FilingRecord`; map their form families to A–F (or a scoped set). Reuse
   `storage`, `pipeline`, `completeness`, `rag`.
3. **Universe + CLI:** committed issuer list (or all-filers-in-window) + a `--source`
   selector / subcommand mirroring `discover`/`download`.
4. **Tests:** fixture-driven parser + fake-fetcher pipeline tests (as `test_edgar_*`).

Lead adapter: **TBD (EDINET vs DART)** — both open APIs with XBRL; free API key each.

## Backlog (optional, in-scope SEC)
- `sources/edgar_fts.py` — efts.sec.gov full-text search discovery (targeted, 2001+).
- `sources/wayback.py` — recover dead/superseded primary docs.
- Grow curated universe → full S&P 500 and run an end-to-end RAG round-trip on a subset.

## CUSIP-based universe resolution (bond indices)
Motivation: bond-index exports (iBoxx, credit indices) key on **CUSIP/ISIN**, and
their issuer "ticker" is a debt ticker, not the SEC equity ticker — resolving by
ticker alone misses ~25% of names and silently mis-maps recycled tickers
(DT→Dynatrace≠Deutsche Telekom, S→SentinelOne≠Sprint).
- **Phase A (done):** `build-universe --from-file CSV --crosswalk` — waterfall
  ticker∪CUSIP6→CIK + collision detector (ticker-CIK vs CUSIP6-CIK disagreement →
  `<name>_collisions.jsonl`). Crosswalk consumed offline (`leoliu0/cik-cusip-mapping`).
- **Phase B (done):** build our own debt-side CUSIP6→CIK crosswalk from EDGAR.
  Added `FormType` D3 (extended 424B1-8) / D4 (FWP) / D5 (S-3); `cusip.py`
  (check-digit-validated extraction + `build_debt_crosswalk`); `pipeline.
  build_cusip_crosswalk` + CLI `build-cusip-crosswalk`. Live-validated on Apple
  (3 offering docs → 037833, no false positives). Next: scale + merge with a
  public crosswalk; tune doc selection (issuer CUSIP6 = most frequent in doc).
- **Phase C (done):** name→CIK resolution tier. `sources/cik_lookup.py` parses
  the SEC `cik-lookup-data.txt` name index (all filers, former names); strict
  canonical normalization in `universe.canonical_name`; `resolve_names` does
  exact-match resolution with collision detection and a dated `formerNames`
  tie-breaker; durable ledger (`data/reference/name_cik_cache.csv`); integrated
  into `reconcile_identifiers` + `issuers_from_sp500`; CLI flags `--no-name-resolution`
  / `--name-cache` on `build-universe` (on by default). Recovers ~139 historical
  S&P 500 names the ticker map drops.
- **Later:** optional OpenFIGI adapter (isolated, external API) to backfill the tail.
