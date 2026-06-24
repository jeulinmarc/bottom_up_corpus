# Dated name→CIK resolution tier + durable resolution ledger — Design

**Date:** 2026-06-24
**Status:** Approved (brainstorming)
**Branch:** `feat/name-cik-resolver`

## Problem

`build-universe` resolves issuers from **tickers** via the SEC's official
`company_tickers.json`. That map lists only *currently trading* issuers, so it
carries **survivorship bias**: delisted, acquired, or renamed members do not
resolve. Concretely, building the S&P 500 as a historical union leaves ~139
members unresolved (`cik=""`) — every company that was once in the index but has
since been removed and dropped off the current ticker map. Tickers cannot fix
this: the SEC dates **name↔CIK** (via submissions `formerNames` ranges and each
filing's point-in-time name) but never **ticker↔CIK** (tickers are a current,
recyclable snapshot — the same symbol is reassigned after a delisting, which is
how we already got homonym collisions like `DT`, `S`, `GP`).

The robust anchor is therefore **CIK + dated name**, not a dated ticker map.

## Goal

Add a **name→CIK resolution tier** that makes historical/delisted issuers
resolvable by name, wired transversally into the existing resolution chain, with
a durable ledger that pins decisions across runs. It must resolve the ~139
unresolved S&P 500 historical members, and serve any name-bearing universe.

## Key decisions (from brainstorming)

1. **Index source: `cik-lookup-data.txt`.** The SEC publishes a single public
   file listing `COMPANY NAME:CIK:` for *all* filers, **former names included**.
   One GET (a few MB), cached to disk, covers all of EDGAR — delisted and
   renamed entities too. It is *undated* (an accumulation of every name ever
   borne, with no ranges); dates are used only as a second-tier tie-breaker for
   collisions. Rejected: crawling the quarterly full-index (`master.idx`) — fully
   dated but far costlier and only covers names that filed in the crawled window.
   Rejected: submissions `formerNames` as the index — it is keyed *by CIK*, so it
   cannot answer "which CIK bore this name"; it is used only to *date* an
   already-resolved candidate (the tie-breaker).

2. **Matching: exact-normalized, collision → unresolved.** Conservative
   canonicalization (uppercase, punctuation stripped, legal-form suffixes
   canonicalized, whitespace collapsed). A **unique** match resolves; **zero**
   matches → `unresolved` (recoverable, reported); **several** CIKs → treated as a
   **collision** (candidates listed, not resolved by default). This favours *zero
   false positives*: a miss is recoverable, but attaching the wrong company's
   financials pollutes the corpus silently. No fuzzy matching (rejected — it
   reintroduces exactly the false-positive risk we avoid).

3. **Activation: on by default for name-bearing universes.** The name tier
   activates automatically when a name is present and ticker/CUSIP resolution
   fails, so the 139 S&P members resolve with no extra flag. This is acceptable
   under SEC fair-access precisely because the source is **one cached file**, not
   a crawl. A `--no-name-resolution` escape hatch disables it. Dry-run-by-default
   remains the rule for the universe output itself.

4. **Durable ledger: default path, auto-written.** The `name→CIK` resolution
   ledger is written/updated automatically at a default path
   (`data/reference/name_cik_cache.csv`), no flag required, so pinned decisions
   accumulate run to run. `--name-cache PATH` overrides the default. A name in the
   ledger short-circuits both the index lookup and any collision flagging — once a
   collision is resolved (by date or by hand), it is never re-flagged.

   *Note:* `cik-lookup-data.txt` already retains delisted/former names
   permanently, so the *index itself* survives delisting. The ledger's value is
   therefore (a) avoiding re-download/re-match each run and (b) **pinning a
   resolved collision** so it is not re-surfaced.

## Architecture

A new **resolution tier** inserted as a last resort in the existing chain. The
authority order gains one rung:

```
provided CIK  >  CUSIP6→CIK  >  current ticker map  >  name(+date)→CIK   [> fts cusip→cik, unchanged]
```

Within the existing `reconcile_identifiers` "else" branch (where ticker and CUSIP
both failed), the name tier runs **before** the `--fts` tier — name resolution is
cache/local (the index is fetched once and cached), whereas fts is per-row
network.

## Components (isolated units)

### `bottom_up_corpus/sources/cik_lookup.py` *(new)*
The only unit that touches the network.

- `fetch_cik_lookup(fetcher: Fetcher, cache_path: Path) -> str` — return the raw
  `cik-lookup-data.txt` text; read `cache_path` if it exists, else one GET (via
  `Fetcher`) and persist it. Source URL:
  `https://www.sec.gov/Archives/edgar/cik-lookup-data.txt`.
- `parse_cik_lookup(text: str) -> dict[str, set[str]]` — parse `NAME:CIK:` lines
  into `{canonical_name: {cik, ...}}`. A CIK with former names appears under
  several names (all → same CIK); two distinct companies can share one canonical
  name (→ a set with >1 CIK = a collision). CIKs normalized to 10 digits;
  names keyed via `naming.canonical_name`.

### `bottom_up_corpus/naming.py` — add `canonical_name`
- `canonical_name(name: str) -> str` — **strict** canonicalization: uppercase,
  every non-alphanumeric char → space, legal-form suffixes
  (`INC CORP CORPORATION CO COMPANY LLC LP PLC SA NV AG AB LTD LIMITED THE …`)
  dropped, whitespace collapsed, trimmed. Distinct from `universe._names_match`,
  which is deliberately *loose* (token-overlap) and only downgrades a collision's
  review priority. `canonical_name` must be applied symmetrically to both index
  keys and query names so a match is exact-after-normalization.

### `bottom_up_corpus/universe.py` — `resolve_names`
- `resolve_names(names, index, *, cache=None) -> tuple[dict[str, str], list[dict], list[str]]`
  → `(resolved, collisions, unresolved)`. Mirror of `resolve_cusips`. For each
  name: consult `cache` first (hit → pinned CIK, done); else look up
  `canonical_name(name)` in `index`. One CIK → `resolved[name] = cik`; zero →
  `unresolved`; several → a `collision` dict `{name, candidates: [cik, ...]}`
  (left unresolved). Pure/offline given a prebuilt `index` and `cache`.

### `bottom_up_corpus/universe.py` — date tie-breaker (collision tier 2)
Only invoked when a collision has a **known date window** (e.g. S&P
`first_seen`/`last_seen`). For each candidate CIK, read its dated `formerNames`
via the submissions API and keep the candidate whose name range covers the
window — reusing `naming.parse_former_names` / `naming.name_as_of`. If it does not
single out exactly one, the collision stands. This is the only place that issues
per-candidate submissions requests, and only for ambiguous names.

### `bottom_up_corpus/universe.py` — ledger I/O
- `load_name_cache(path) -> dict[str, str]` and
  `write_name_cache(path, pairs) -> int` — mirror of
  `load_cusip_crosswalk` / `write_cusip_crosswalk`: a `name,cik` CSV, merge +
  dedup + sorted rewrite. Keyed by `canonical_name`.

### `bottom_up_corpus/config.py` — reference dir
- A `reference_dir` (`data/reference/`) for the cached `cik-lookup-data.txt` and
  the default `name_cik_cache.csv`, alongside the existing `universe_dir`.

## Data flow

```
name to resolve
   │
   ├─ ledger hit? ───────────────► pinned CIK (resolution="name:cache")     [done]
   │
   ├─ canonical_name → index lookup
   │      0 CIK  ───────────────► unresolved (reported)
   │      1 CIK  ───────────────► resolved (resolution="name")
   │      >1 CIK ─┐
   │              └─ date window known?
   │                   yes → submissions formerNames tie-break
   │                          1 survivor → resolved (resolution="name:dated")
   │                          else       → collision (candidates listed)
   │                   no  → collision (candidates listed)
   │
   └─ (in reconcile: if still unresolved, the existing --fts tier runs next)
```

## Integration

### `reconcile_identifiers`
Add the name tier in the final `else` branch (ticker and CUSIP both failed),
**before** the fts block. When the row has a `name`, attempt `resolve_names`
(against the loaded index + ledger). On a hit → `Issuer(..., resolution="name")`.
On a collision → a `collision` dict (same shape/discipline as existing
collisions). Only if name resolution yields nothing does fts run.

### `issuers_from_sp500`
After the existing ticker-map pass, members still at `cik=""` that carry a
`company` name are run through `resolve_names`, using the membership window
(`first_seen`/`last_seen`) as the date hint for the tie-breaker. Resolved members
get their CIK; genuinely unresolved/collision members keep `cik=""` and are
reported. This is where the 139 are recovered.

### CLI `build-universe`
- Name tier **on by default** whenever names are present.
- `--no-name-resolution` — disable the tier (revert to current behaviour).
- `--name-cache PATH` — override the default ledger path
  (`data/reference/name_cik_cache.csv`).
- The raw `cik-lookup-data.txt` is cached at
  `data/reference/cik-lookup-data.txt`.
- Summary line (stderr, like the existing `resolved / collisions / unresolved`)
  reports how many names the tier resolved and how many remain collisions.

## Caching & writes

- **Raw index file** (`cik-lookup-data.txt`): a fetched reference cache, written
  whenever absent. One GET per machine until refreshed.
- **Resolution ledger** (`name_cik_cache.csv`): written/updated automatically at
  the default path (or `--name-cache`), **not gated by `--write`** — a
  transparent accumulation of pinned decisions. The universe `.jsonl` output
  itself remains gated by `--write` (dry-run by default).

## Error handling

- Network failure fetching `cik-lookup-data.txt`: surface a clear error; the name
  tier yields nothing for that run (universe still builds from ticker/CUSIP). The
  `--no-name-resolution` hatch lets a user proceed offline deliberately.
- Malformed `NAME:CIK:` lines: skipped (mirrors `load_cusip_crosswalk`'s
  tolerance), counted, not fatal.
- A name canonicalizing to empty (pure noise words): skipped → `unresolved`.

## Testing (FakeFetcher + tmp_path, matching existing patterns)

- `parse_cik_lookup`: former names → same CIK; two companies → same canonical
  name → set of 2 (collision).
- `canonical_name`: suffixes dropped, punctuation/casing folded, index/query
  symmetry.
- `resolve_names`: unique → resolved; absent → unresolved; multi → collision;
  ledger hit short-circuits index and collision.
- Date tie-breaker: 2 candidate CIKs, a window that singles out one / a window
  that does not (collision stands).
- `load_name_cache` / `write_name_cache`: merge + dedup + sorted rewrite.
- `reconcile_identifiers`: a name resolved by the name tier when ticker+CUSIP
  fail; name tier runs before fts.
- `issuers_from_sp500`: a `cik=""` member with a name is resolved (FakeFetcher
  routes `cik-lookup-data.txt`).
- CLI: `--no-name-resolution` disables the tier; default path ledger is written.

## Out of scope

- Crawling the quarterly full-index to build a *fully* dated name↔CIK index
  (costlier; revisit only if `cik-lookup-data.txt` + per-collision tie-break
  proves insufficient).
- Fuzzy / near-match name resolution.
- Backfilling the ledger from sources other than this tier's own resolutions.
