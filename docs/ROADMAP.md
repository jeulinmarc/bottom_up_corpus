# bottom_up_corpus — Roadmap & next steps

> Living plan for what's done and what's next.

## Status — document pillars + structured financials + register pillar

🇺🇸 **SEC** pillar complete + 🇪🇺 **EU** document pillar built (14 jurisdictions) +
EU ESEF structured financials done (Pillar B, PRs #55–56) + register-financials
pillar started. All merged to `main` except UK (#58, open). See
[`SEC_PILLAR.md`](SEC_PILLAR.md), [`EU_PILLAR.md`](EU_PILLAR.md),
[`EU_FINANCIALS.md`](EU_FINANCIALS.md), [`REGISTER_FINANCIALS.md`](REGISTER_FINANCIALS.md).

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
| **EU ESEF/IFRS structured financials (Pillar B)** — json_url stdlib (#55) + Arelle Tier B (#56) | ✅ |
| **Register financials** — 🇳🇴 NO Brreg JSON (#57, merged) | ✅ |
| **Register financials** — 🇬🇧 UK Companies House iXBRL, `--ch-bulk` (#58, open) | 🔄 |

**Phase 5 (international) — EU pillar built.** The `bottom_up_corpus/eu/` package
federates 13 national OAMs + Euronext + filings.xbrl.org behind a pluggable
`OamSource` interface, keyed on GLEIF LEI/ISIN (a parallel pillar to the SEC one,
with its own `Document`/`acquire` model). See [`EU_PILLAR.md`](EU_PILLAR.md) +
[`EU_BACKENDS.md`](EU_BACKENDS.md). Unbuilt SEC niceties: `edgar_fts`, `wayback`.

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

## EU pillar — done, with follow-ups

The European pillar (`bottom_up_corpus/eu/`) is built and live-validated across 14
jurisdictions ([`EU_PILLAR.md`](EU_PILLAR.md)). Remaining EU follow-ups:

- ✅ **Pillar B — structured ESEF/IFRS extraction.** Done: json_url stdlib path (#55, merged) + Arelle Tier B (#56, merged). See [`EU_FINANCIALS.md`](EU_FINANCIALS.md). Remaining follow-up: acquisition-side fix for DE/FR/SE (fetching ESEF zips for backends that do not yet download them) — a separate PR.
- **Euronext `company-news`** — issuer press releases (results, incl. PDFs) on top
  of the corporate-event *notices* the Euronext backend already captures.
- **CMVM Portugal-direct** — only if an authenticated route to its OutSystems
  portal becomes available (PT is covered via Euronext today).
- **Richer Oslo coverage** — a reliable ISIN→issuerSign mapping (OpenFIGI's Oslo
  ticker coverage is spotty); foreign-domiciled Oslo issuers are covered today via
  the corroborated name path.

## Register-financials pillar

National business registers → the same curated financial schema as SEC XBRL and EU ESEF,
targeting the **private / credit universe** (non-listed issuers that never file ESEF).
Output: `data/financials_register/`, labelled by `basis`. Governed by a **no-false-data**
confidence gate — a derived value that cannot be confirmed from structural anchors is
suppressed, not guessed; the reason is recorded per key in the coverage report.
Registers are balance-sheet-primary; leverage is liabilities-based (total liabilities,
not pure financial borrowings).

| Register | Method | Status |
|---|---|---|
| 🇳🇴 NO Brønnøysund (Brreg) | structured JSON, no XBRL | ✅ merged #57 |
| 🇬🇧 UK Companies House | iXBRL via Arelle, `--ch-bulk` | 🔄 PR #58 open |

Next: 🇧🇪 BE BNB / 🇩🇰 DK Erhvervsstyrelsen (both XBRL; reuse the Arelle path); UK
targeted REST API (per-CH-number named-entity lookup); historic monthly backfill
(multi-year history per entity); OCR for the non-ESEF / pre-2020 tail.

## Other jurisdictions (future)

Beyond the EU/UK, the same approach extends to other open disclosure systems —
**Japan EDINET**, **Korea DART** (both open XBRL APIs) — as further `OamSource`
backends (or, for the US-style `FilingRecord` pipeline, dedicated `Source`
adapters). Not started.

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
