# Dated name→CIK resolution tier + durable ledger — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a name(+date)→CIK resolution tier — built from the SEC `cik-lookup-data.txt` file — that recovers historical/delisted issuers the current ticker map misses (the ~139 unresolved S&P 500 historical members), with a durable auto-written `name→CIK` ledger.

**Architecture:** A new last-resort tier in the existing resolution chain (authority `CIK > CUSIP6 > ticker > name(+date)`). Source is a single cached SEC file (`cik-lookup-data.txt`, former names included). Matching is exact-after-strict-normalization; a name mapping to several CIKs is a collision, optionally broken by a dated submissions `formerNames` lookup when a date window is known. Resolved decisions accumulate in a CSV ledger.

**Tech Stack:** Python 3, stdlib `csv`, the existing `Fetcher`/`FakeFetcher`, `naming.parse_former_names`/`name_as_of`, pytest.

Spec: `docs/superpowers/specs/2026-06-24-name-cik-resolver-design.md`

## Global Constraints

- Branch: `feat/name-cik-resolver`. Never commit to `main` (user merges PRs himself).
- SEC fair access: ≤10 req/s; the name tier's source is **one GET** (`cik-lookup-data.txt`), cached to disk thereafter.
- Universe `.jsonl` output stays **dry-run by default** (gated by `--write`); the ledger and the raw-file cache are written independently of `--write`.
- Tests use `FakeFetcher` (route by URL substring) + `tmp_path`; mirror existing `tests/test_universe.py` / `tests/conftest.py` patterns. No live network in tests.
- `Issuer` is a frozen dataclass; CIKs normalized to 10 digits via `normalize_cik`.
- DRY, YAGNI, TDD, frequent commits.

## File Structure

- **Create** `bottom_up_corpus/sources/cik_lookup.py` — fetch + parse the SEC `cik-lookup-data.txt` into `{canonical_name: {cik}}`. Only network unit.
- **Modify** `bottom_up_corpus/naming.py` — add `canonical_name` (strict normalization) + the legal-suffix set.
- **Modify** `bottom_up_corpus/universe.py` — `resolve_names`, `_resolve_one_name`, `_tiebreak_collision_by_date`, `_coerce_date`, `resolve_member_names`, `load_name_cache`, `write_name_cache`; name-tier wiring in `reconcile_identifiers` and `issuers_from_sp500`.
- **Modify** `bottom_up_corpus/config.py` — `reference_dir`, `cik_lookup_path`, `name_cache_path` properties.
- **Modify** `bottom_up_corpus/cli.py` — `--no-name-resolution`, `--name-cache` flags; wire the tier into both `build-universe` branches; auto-write the ledger.
- **Create** `tests/test_cik_lookup.py`; **Create** `tests/test_naming.py`; **Modify** `tests/test_universe.py`, `tests/test_cli.py`.
- **Modify** `README.md`, `docs/ROADMAP.md`; **Create** `examples/15_name_resolution.py`.

---

### Task 1: `canonical_name` strict name normalization

**Files:**
- Modify: `bottom_up_corpus/naming.py`
- Test: `tests/test_naming.py` (create)

**Interfaces:**
- Produces: `naming.canonical_name(name: str) -> str` — uppercase, non-alphanumerics → spaces, legal-form suffix tokens dropped, whitespace collapsed. Idempotent. Applied symmetrically to both index keys and query names.

- [ ] **Step 1: Write the failing test**

Create `tests/test_naming.py`:

```python
from __future__ import annotations

from bottom_up_corpus.naming import canonical_name


def test_canonical_name_drops_legal_suffixes_and_punctuation():
    assert canonical_name("Apple Inc.") == "APPLE"
    assert canonical_name("MICROSOFT CORP") == "MICROSOFT"
    assert canonical_name("The Coca-Cola Company") == "COCA COLA"
    assert canonical_name("Berkshire Hathaway Inc.") == "BERKSHIRE HATHAWAY"


def test_canonical_name_is_idempotent():
    once = canonical_name("Sunrise Corporation")
    assert once == "SUNRISE"
    assert canonical_name(once) == once


def test_canonical_name_keeps_meaningful_words():
    # GROUP / HOLDINGS distinguish issuers and must NOT be dropped.
    assert canonical_name("Carlyle Group Inc") == "CARLYLE GROUP"
    assert canonical_name("Loews Holdings") == "LOEWS HOLDINGS"


def test_canonical_name_pure_noise_is_empty():
    assert canonical_name("The Co.") == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_naming.py -q`
Expected: FAIL with `ImportError: cannot import name 'canonical_name'`.

- [ ] **Step 3: Implement `canonical_name`**

Append to `bottom_up_corpus/naming.py`:

```python
# Legal-form / article tokens dropped when canonicalizing a company name for
# name->CIK matching. Deliberately conservative: only legal forms and the
# article "THE" -- meaningful words (GROUP, HOLDINGS, FINANCIAL, ...) are kept
# because they distinguish issuers.
_LEGAL_SUFFIXES = {
    "INC", "INCORPORATED", "CORP", "CORPORATION", "CO", "COMPANY", "COS",
    "LLC", "LLP", "LP", "PLC", "SA", "NV", "AG", "AB", "LTD", "LIMITED", "THE",
}


def canonical_name(name: str) -> str:
    """Strict canonical form of a company name for exact name->CIK matching.

    Upper-cases, turns every non-alphanumeric character into a space, drops
    legal-form suffix tokens (see ``_LEGAL_SUFFIXES``), and collapses
    whitespace. Idempotent, and applied symmetrically to index keys and query
    names so a match is exact-after-normalization. Returns ``""`` for a name
    made only of noise words.
    """
    cleaned = "".join(ch if ch.isalnum() else " " for ch in str(name).upper())
    tokens = [t for t in cleaned.split() if t and t not in _LEGAL_SUFFIXES]
    return " ".join(tokens)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/python -m pytest tests/test_naming.py -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add bottom_up_corpus/naming.py tests/test_naming.py
git commit -m "feat(naming): canonical_name for exact name->CIK matching"
```

---

### Task 2: `cik_lookup` source — fetch + parse `cik-lookup-data.txt`

**Files:**
- Create: `bottom_up_corpus/sources/cik_lookup.py`
- Test: `tests/test_cik_lookup.py` (create)

**Interfaces:**
- Consumes: `naming.canonical_name`, `config.normalize_cik`, a `Fetcher`/`FakeFetcher` (`get_text`).
- Produces:
  - `cik_lookup.CIK_LOOKUP_URL: str`
  - `cik_lookup.fetch_cik_lookup(fetcher, cache_path) -> str` — read `cache_path` if present, else one GET + persist.
  - `cik_lookup.parse_cik_lookup(text: str) -> dict[str, set[str]]` — `{canonical_name: {cik, ...}}`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_cik_lookup.py`:

```python
from __future__ import annotations

from pathlib import Path

from bottom_up_corpus.sources.cik_lookup import (
    CIK_LOOKUP_URL,
    fetch_cik_lookup,
    parse_cik_lookup,
)

SAMPLE = (
    "APPLE INC:0000320193:\n"
    "APPLE COMPUTER INC:0000320193:\n"   # former name -> same CIK, different key
    "META PLATFORMS INC:0001326801:\n"
    "FACEBOOK INC:0001326801:\n"
    "SUNRISE CORP:0000111111:\n"
    "SUNRISE CORPORATION:0000222222:\n"  # same canonical key -> two CIKs (collision)
    "GARBAGE LINE WITHOUT COLON\n"        # skipped
    ":0000000001:\n"                       # empty name -> skipped
)


def test_parse_groups_former_names_and_collisions():
    index = parse_cik_lookup(SAMPLE)
    assert index["APPLE"] == {"0000320193"}
    assert index["APPLE COMPUTER"] == {"0000320193"}
    assert index["META PLATFORMS"] == {"0001326801"}
    assert index["FACEBOOK"] == {"0001326801"}
    assert index["SUNRISE"] == {"0000111111", "0000222222"}  # collision
    assert "GARBAGE LINE WITHOUT COLON" not in index


def test_fetch_uses_cache_on_second_call(make_fetcher, tmp_path):
    cache = tmp_path / "ref" / "cik-lookup-data.txt"
    fetcher = make_fetcher({"cik-lookup-data.txt": SAMPLE})
    first = fetch_cik_lookup(fetcher, cache)
    assert first == SAMPLE
    assert cache.exists()
    assert len(fetcher.calls) == 1
    second = fetch_cik_lookup(fetcher, cache)   # served from disk, no new GET
    assert second == SAMPLE
    assert len(fetcher.calls) == 1
    assert CIK_LOOKUP_URL.startswith("https://www.sec.gov/")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_cik_lookup.py -q`
Expected: FAIL with `ModuleNotFoundError: bottom_up_corpus.sources.cik_lookup`.

- [ ] **Step 3: Implement the source**

Create `bottom_up_corpus/sources/cik_lookup.py`:

```python
"""Name -> CIK index from the SEC ``cik-lookup-data.txt`` master file.

The SEC publishes a single file listing ``COMPANY NAME:CIK:`` for *every* filer,
**former names included**, so it covers delisted/renamed entities the current
ticker map omits. One GET, cached to disk thereafter. The file is undated (an
accumulation of every name ever borne); dates disambiguate collisions in a
second tier (see ``universe._tiebreak_collision_by_date``).
"""

from __future__ import annotations

from pathlib import Path

from ..config import normalize_cik
from ..http import Fetcher
from ..naming import canonical_name

CIK_LOOKUP_URL = "https://www.sec.gov/Archives/edgar/cik-lookup-data.txt"


def fetch_cik_lookup(fetcher: Fetcher, cache_path: Path | str) -> str:
    """Return the raw ``cik-lookup-data.txt`` text, reading the cache if present.

    On a cache miss, one GET via the shared (fair-access throttled) ``Fetcher``,
    then the body is persisted to ``cache_path``.
    """
    p = Path(cache_path)
    if p.exists():
        return p.read_text(encoding="utf-8")
    text = fetcher.get_text(CIK_LOOKUP_URL)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return text


def parse_cik_lookup(text: str) -> dict[str, set[str]]:
    """Parse ``NAME:CIK:`` lines into ``{canonical_name: {cik, ...}}``.

    A CIK with former names appears under several names (all -> same CIK); two
    distinct companies can share one canonical name (-> a set with >1 CIK = a
    collision). Malformed lines and empty names are skipped.
    """
    index: dict[str, set[str]] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.endswith(":"):
            line = line[:-1]
        name, sep, cik_raw = line.rpartition(":")
        if not sep or not name.strip():
            continue
        try:
            cik = normalize_cik(cik_raw)
        except ValueError:
            continue
        key = canonical_name(name)
        if not key:
            continue
        index.setdefault(key, set()).add(cik)
    return index
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/python -m pytest tests/test_cik_lookup.py -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add bottom_up_corpus/sources/cik_lookup.py tests/test_cik_lookup.py
git commit -m "feat(cik_lookup): fetch+parse SEC cik-lookup-data.txt into name->CIK index"
```

---

### Task 3: Config reference paths

**Files:**
- Modify: `bottom_up_corpus/config.py` (after `financials_dir`/`ownership_dir`, near lines 93-99)
- Test: `tests/test_config.py` (create)

**Interfaces:**
- Produces: `Config.reference_dir -> Path` (`data/reference`), `Config.cik_lookup_path -> Path` (`data/reference/cik-lookup-data.txt`), `Config.name_cache_path -> Path` (`data/reference/name_cik_cache.csv`).

- [ ] **Step 1: Write the failing test**

Create `tests/test_config.py`:

```python
from __future__ import annotations

from pathlib import Path

from bottom_up_corpus.config import Config


def test_reference_paths():
    cfg = Config(data_dir=Path("/tmp/x/data"))
    assert cfg.reference_dir == Path("/tmp/x/data/reference")
    assert cfg.cik_lookup_path == Path("/tmp/x/data/reference/cik-lookup-data.txt")
    assert cfg.name_cache_path == Path("/tmp/x/data/reference/name_cik_cache.csv")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_config.py -q`
Expected: FAIL with `AttributeError: 'Config' object has no attribute 'reference_dir'`.

- [ ] **Step 3: Implement the properties**

In `bottom_up_corpus/config.py`, add after the `ownership_dir` property (line 99):

```python
    @property
    def reference_dir(self) -> Path:
        return self.data_dir / "reference"

    @property
    def cik_lookup_path(self) -> Path:
        return self.reference_dir / "cik-lookup-data.txt"

    @property
    def name_cache_path(self) -> Path:
        return self.reference_dir / "name_cik_cache.csv"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/python -m pytest tests/test_config.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bottom_up_corpus/config.py tests/test_config.py
git commit -m "feat(config): reference_dir + cik_lookup_path + name_cache_path"
```

---

### Task 4: `resolve_names` + ledger I/O (no date tier yet)

**Files:**
- Modify: `bottom_up_corpus/universe.py` (add after `resolve_cusips`, around line 382; imports at top)
- Test: `tests/test_universe.py`

**Interfaces:**
- Consumes: `naming.canonical_name`, `config.normalize_cik`.
- Produces:
  - `universe._resolve_one_name(name, index, cache) -> tuple[str, object]` — `("resolved", cik)` | `("collision", sorted_candidates)` | `("unresolved", None)`.
  - `universe.resolve_names(names, index, *, cache=None) -> tuple[dict[str,str], list[dict], list[str]]` — `(resolved{name->cik}, collisions[{name, candidates}], unresolved[name])`. (The `dates`/`fetcher` date tier is added in Task 5.)
  - `universe.load_name_cache(path) -> dict[str,str]` (canonical_name -> cik).
  - `universe.write_name_cache(path, pairs) -> int` (merge `(name, cik)` pairs, canonicalize keys, dedup, sorted rewrite).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_universe.py` (and extend the top import from `bottom_up_corpus.universe` to include `resolve_names`, `load_name_cache`, `write_name_cache`):

```python
def test_resolve_names_unique_collision_unresolved():
    index = {"WIDGET": {"0000999999"}, "SUNRISE": {"0000111111", "0000222222"}}
    resolved, collisions, unresolved = resolve_names(
        ["Widget Inc", "Sunrise Corp", "Nobody LLC"], index)
    assert resolved == {"Widget Inc": "0000999999"}
    assert collisions == [{"name": "Sunrise Corp",
                           "candidates": ["0000111111", "0000222222"]}]
    assert unresolved == ["Nobody LLC"]


def test_resolve_names_cache_short_circuits_index_and_collision():
    index = {"SUNRISE": {"0000111111", "0000222222"}}
    cache = {"SUNRISE": "0000111111"}  # pinned decision, keyed by canonical name
    resolved, collisions, unresolved = resolve_names(
        ["Sunrise Corp"], index, cache=cache)
    assert resolved == {"Sunrise Corp": "0000111111"}
    assert collisions == [] and unresolved == []


def test_name_cache_roundtrip_merges_and_dedups(tmp_path):
    path = tmp_path / "ref" / "name_cik_cache.csv"
    assert load_name_cache(path) == {}  # absent -> empty
    n1 = write_name_cache(path, [("Apple Inc.", "320193"), ("Sunrise Corp", "111111")])
    assert n1 == 2
    n2 = write_name_cache(path, [("Apple Inc.", "320193")])  # dup, no growth
    assert n2 == 2
    loaded = load_name_cache(path)
    assert loaded["APPLE"] == "0000320193"
    assert loaded["SUNRISE"] == "0000111111"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_universe.py -k "resolve_names or name_cache" -q`
Expected: FAIL with `ImportError: cannot import name 'resolve_names'`.

- [ ] **Step 3: Implement the functions**

At the top of `bottom_up_corpus/universe.py`, extend the imports:

```python
from .config import Config, cusip6 as to_cusip6, cusip_full as to_cusip_full, normalize_cik
from .http import Fetcher
from .naming import canonical_name, name_as_of, parse_former_names
```

Add after `resolve_cusips` (after line 382):

```python
def _resolve_one_name(
    name: str, index: dict[str, set[str]], cache: dict[str, str]
) -> tuple[str, object]:
    """Resolve a single name against the cache then the index (no date tier).

    Returns ``("resolved", cik)``, ``("collision", [cik, ...])`` (sorted), or
    ``("unresolved", None)``. The cache (keyed by canonical name) wins and also
    short-circuits collision flagging.
    """
    key = canonical_name(name)
    if not key:
        return ("unresolved", None)
    if key in cache:
        return ("resolved", cache[key])
    ciks = index.get(key)
    if not ciks:
        return ("unresolved", None)
    if len(ciks) == 1:
        return ("resolved", next(iter(ciks)))
    return ("collision", sorted(ciks))


def resolve_names(
    names: Iterable[str],
    index: dict[str, set[str]],
    *,
    cache: dict[str, str] | None = None,
) -> tuple[dict[str, str], list[dict], list[str]]:
    """Resolve names to CIKs via ``index`` (and a pinned ``cache``).

    Returns ``(resolved, collisions, unresolved)``: ``resolved`` maps the input
    name to its CIK (unique match or cache hit); ``collisions`` lists
    ``{name, candidates}`` for names mapping to several CIKs; ``unresolved`` is
    the names with no canonical match. Exact-after-normalization only -- no
    fuzzy matching (a false positive would attach the wrong issuer's data).
    """
    cache = cache or {}
    resolved: dict[str, str] = {}
    collisions: list[dict] = []
    unresolved: list[str] = []
    for raw in names:
        name = str(raw).strip()
        if not name:
            continue
        status, val = _resolve_one_name(name, index, cache)
        if status == "resolved":
            resolved[name] = val  # type: ignore[assignment]
        elif status == "collision":
            collisions.append({"name": name, "candidates": val})
        else:
            unresolved.append(name)
    return resolved, collisions, unresolved


def load_name_cache(path: Path | str) -> dict[str, str]:
    """Load the ``name,cik`` ledger CSV into ``{canonical_name: cik}``.

    Keys are re-canonicalized on load (idempotent) so a hand-edited file still
    matches queries. A CIK serialized as a float string (``"320193.0"``) is
    tolerated, mirroring :func:`load_cusip_crosswalk`.
    """
    import csv

    out: dict[str, str] = {}
    p = Path(path)
    if not p.exists():
        return out
    with p.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            name = (row.get("name") or "").strip()
            raw_cik = (row.get("cik") or "").split(".")[0]
            key = canonical_name(name)
            if not key or not raw_cik.strip():
                continue
            try:
                out[key] = normalize_cik(raw_cik)
            except ValueError:
                continue
    return out


def write_name_cache(path: Path | str, pairs: Iterable[tuple[str, str]]) -> int:
    """Merge ``(name, cik)`` pairs into the ``name,cik`` ledger CSV (dedup).

    Names are canonicalized to the file's key form; the last CIK written for a
    key wins (a pin updates). Returns the total row count. Transparent
    optimization artifact -- written independently of ``--write``.
    """
    import csv

    p = Path(path)
    rows: dict[str, str] = {}
    if p.exists():
        rows.update(load_name_cache(p))
    for name, cik in pairs:
        key = canonical_name(name)
        if not key:
            continue
        try:
            rows[key] = normalize_cik(cik)
        except ValueError:
            continue
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["name", "cik"])
        for key in sorted(rows):
            w.writerow([key, rows[key]])
    return len(rows)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/python -m pytest tests/test_universe.py -k "resolve_names or name_cache" -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add bottom_up_corpus/universe.py tests/test_universe.py
git commit -m "feat(universe): resolve_names + durable name->CIK ledger I/O"
```

---

### Task 5: Date tie-breaker for name collisions

**Files:**
- Modify: `bottom_up_corpus/universe.py` (add `_coerce_date`, `_tiebreak_collision_by_date`; extend `resolve_names` signature)
- Test: `tests/test_universe.py`

**Interfaces:**
- Consumes: `naming.name_as_of`, `naming.parse_former_names`, `naming.canonical_name`, `sources.edgar_submissions.SUBMISSIONS_URL`, a `Fetcher` (`get_json`).
- Produces:
  - `universe._coerce_date(value) -> date | None` (ISO string / `date` / `""` / `"current"` → `date|None`).
  - `universe._tiebreak_collision_by_date(key, candidates, target, fetcher) -> str` — the single CIK whose name-in-effect on `target` canonicalizes to `key`, else `""`.
  - `resolve_names(..., *, cache=None, dates=None, fetcher=None)` — when a collision has a `dates[name]` and a `fetcher`, the tie-breaker may promote it to resolved.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_universe.py`:

```python
def test_name_collision_resolved_by_date_window(make_fetcher):
    # "SUNRISE" -> {111111, 222222}. On 2015, only 111111 still bears the name;
    # 222222 had renamed to NEWCO by 2010, so the date singles out 111111.
    index = {"SUNRISE": {"0000111111", "0000222222"}}
    routes = {
        "CIK0000111111.json": {"name": "SUNRISE CORP", "formerNames": []},
        "CIK0000222222.json": {"name": "NEWCO INC", "formerNames": [
            {"name": "Sunrise Corporation",
             "from": "2000-01-01T00:00:00.000Z", "to": "2010-01-01T00:00:00.000Z"}]},
    }
    fetcher = make_fetcher(routes)
    resolved, collisions, unresolved = resolve_names(
        ["Sunrise Corp"], index,
        dates={"Sunrise Corp": "2015-06-01"}, fetcher=fetcher)
    assert resolved == {"Sunrise Corp": "0000111111"}
    assert collisions == []


def test_name_collision_unbroken_when_date_does_not_separate(make_fetcher):
    # On 2005 BOTH bore the name -> the collision stands.
    index = {"SUNRISE": {"0000111111", "0000222222"}}
    routes = {
        "CIK0000111111.json": {"name": "SUNRISE CORP", "formerNames": []},
        "CIK0000222222.json": {"name": "NEWCO INC", "formerNames": [
            {"name": "Sunrise Corporation",
             "from": "2000-01-01T00:00:00.000Z", "to": "2010-01-01T00:00:00.000Z"}]},
    }
    fetcher = make_fetcher(routes)
    resolved, collisions, unresolved = resolve_names(
        ["Sunrise Corp"], index,
        dates={"Sunrise Corp": "2005-06-01"}, fetcher=fetcher)
    assert resolved == {}
    assert collisions == [{"name": "Sunrise Corp",
                           "candidates": ["0000111111", "0000222222"]}]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_universe.py -k "name_collision_resolved_by_date or unbroken" -q`
Expected: FAIL with `TypeError: resolve_names() got an unexpected keyword argument 'dates'`.

- [ ] **Step 3: Implement the date tier**

Add a `date` import at the top of `bottom_up_corpus/universe.py`:

```python
from datetime import date
```

Add before `resolve_names`:

```python
def _coerce_date(value) -> date | None:
    """Best-effort ISO date from a string/``date``; ``None`` for ``""``/``"current"``."""
    if isinstance(value, date):
        return value
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _tiebreak_collision_by_date(
    key: str, candidates: list[str], target: date, fetcher: Fetcher
) -> str:
    """Return the single candidate CIK whose name-in-effect on ``target``
    canonicalizes to ``key``; ``""`` if zero or more than one qualify.

    Reads each candidate's dated ``formerNames`` via the submissions API and
    applies :func:`naming.name_as_of`. One submissions GET per candidate -- only
    reached for an already-ambiguous name with a known date.
    """
    from .sources.edgar_submissions import SUBMISSIONS_URL

    survivors: list[str] = []
    for cik in candidates:
        try:
            data = fetcher.get_json(SUBMISSIONS_URL.format(cik=cik))
        except Exception:  # noqa: BLE001 - a failed lookup just can't vouch for this CIK
            continue
        eff = name_as_of(target, data.get("name", ""),
                         parse_former_names(data.get("formerNames")))
        if canonical_name(eff) == key:
            survivors.append(cik)
    return survivors[0] if len(survivors) == 1 else ""
```

Then update `resolve_names` to accept `dates`/`fetcher` and run the tie-breaker on collisions:

```python
def resolve_names(
    names: Iterable[str],
    index: dict[str, set[str]],
    *,
    cache: dict[str, str] | None = None,
    dates: dict[str, object] | None = None,
    fetcher: Fetcher | None = None,
) -> tuple[dict[str, str], list[dict], list[str]]:
    """Resolve names to CIKs via ``index`` (and a pinned ``cache``).

    Returns ``(resolved, collisions, unresolved)``. A name mapping to several
    CIKs is a collision unless ``dates[name]`` plus ``fetcher`` let the dated
    ``formerNames`` tie-breaker single out one CIK (then it is ``resolved``).
    Exact-after-normalization only -- no fuzzy matching.
    """
    cache = cache or {}
    dates = dates or {}
    resolved: dict[str, str] = {}
    collisions: list[dict] = []
    unresolved: list[str] = []
    for raw in names:
        name = str(raw).strip()
        if not name:
            continue
        status, val = _resolve_one_name(name, index, cache)
        if status == "resolved":
            resolved[name] = val  # type: ignore[assignment]
        elif status == "collision":
            pick = ""
            target = _coerce_date(dates.get(name))
            if target is not None and fetcher is not None:
                pick = _tiebreak_collision_by_date(
                    canonical_name(name), val, target, fetcher)  # type: ignore[arg-type]
            if pick:
                resolved[name] = pick
            else:
                collisions.append({"name": name, "candidates": val})
        else:
            unresolved.append(name)
    return resolved, collisions, unresolved
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/python -m pytest tests/test_universe.py -k "name" -q`
Expected: PASS (collision/date tests + earlier name tests).

- [ ] **Step 5: Commit**

```bash
git add bottom_up_corpus/universe.py tests/test_universe.py
git commit -m "feat(universe): dated formerNames tie-breaker for name collisions"
```

---

### Task 6: Name tier in `reconcile_identifiers`

**Files:**
- Modify: `bottom_up_corpus/universe.py` (`reconcile_identifiers`, lines 212-291)
- Test: `tests/test_universe.py`

**Interfaces:**
- Consumes: `_resolve_one_name`, `resolve_names` machinery.
- Produces: `reconcile_identifiers(rows, ticker_table, crosswalk, *, fts=None, fts_limit=None, name_index=None, name_cache=None)` — when ticker and CUSIP both fail, a unique/cached name match resolves the row (`resolution="name"`) **before** the fts tier; name collisions fall through (left to fts/unresolved — CSV rows carry no date window).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_universe.py`:

```python
class _NoFTS:
    """An fts stub that must never be called when the name tier resolves first."""
    def resolve(self, cusip):
        raise AssertionError("fts must not run when the name tier resolves the row")


def test_reconcile_name_tier_resolves_before_fts():
    rows = [{"cik": "", "ticker": "", "cusip6": "", "cusip": "12345678",
             "name": "Widget Inc"}]
    name_index = {"WIDGET": {"0000999999"}}
    issuers, collisions, unresolved = reconcile_identifiers(
        rows, {}, {}, fts=_NoFTS(), name_index=name_index)
    assert len(issuers) == 1
    assert issuers[0].cik == "0000999999"
    assert issuers[0].resolution == "name"
    assert collisions == [] and unresolved == []


def test_reconcile_name_collision_falls_through_to_unresolved():
    rows = [{"cik": "", "ticker": "", "cusip6": "", "cusip": "",
             "name": "Sunrise Corp"}]
    name_index = {"SUNRISE": {"0000111111", "0000222222"}}
    issuers, collisions, unresolved = reconcile_identifiers(
        rows, {}, {}, name_index=name_index)
    assert issuers == []
    assert unresolved == ["Sunrise Corp"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_universe.py -k "reconcile_name" -q`
Expected: FAIL with `TypeError: reconcile_identifiers() got an unexpected keyword argument 'name_index'`.

- [ ] **Step 3: Implement the name tier**

Change the `reconcile_identifiers` signature (line 212) to add the two keyword args:

```python
def reconcile_identifiers(
    rows: Iterable[dict],
    ticker_table: dict[str, Issuer],
    crosswalk: dict[str, set[str]],
    *,
    fts=None,
    fts_limit: int | None = None,
    name_index: dict[str, set[str]] | None = None,
    name_cache: dict[str, str] | None = None,
) -> tuple[list[Issuer], list[dict], list[str]]:
```

Replace the final `else:` block (lines 278-290, the fts/unresolved fallthrough) with a name-tier-first version:

```python
        else:
            # Name tier (local/cached) before fts (per-row network).
            name_cik = ""
            if name_index is not None and name:
                status, val = _resolve_one_name(name, name_index, name_cache or {})
                if status == "resolved":
                    name_cik = val  # type: ignore[assignment]
            if name_cik:
                issuers.append(Issuer(cik=name_cik, ticker=ticker, company=company,
                                      cusip6=c6, resolution="name"))
                continue
            full_cusip = (row.get("cusip") or "").strip().upper()
            hit = None
            if fts is not None and full_cusip and (fts_limit is None or fts_calls < fts_limit):
                fts_calls += 1
                hit = fts.resolve(full_cusip)
            if hit:
                hit_cik, hit_name = hit
                kind = "confirmed" if _names_match(name, hit_name) else "unverified"
                issuers.append(Issuer(cik=hit_cik, ticker=ticker, company=name,
                                      cusip6=c6, resolution=f"fts:{kind}"))
            else:
                unresolved.append(name or ticker or c6)
```

Note: the unresolved key now prefers `name` (then ticker, then c6) so a name-only row surfaces by name.

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/python -m pytest tests/test_universe.py -k "reconcile" -q`
Expected: PASS (new tests + existing reconcile tests unchanged).

- [ ] **Step 5: Commit**

```bash
git add bottom_up_corpus/universe.py tests/test_universe.py
git commit -m "feat(universe): name tier in reconcile_identifiers (before fts)"
```

---

### Task 7: Name tier in `issuers_from_sp500` via `resolve_member_names`

**Files:**
- Modify: `bottom_up_corpus/universe.py` (`issuers_from_sp500`, lines 411-447; add `resolve_member_names`)
- Test: `tests/test_universe.py`

**Interfaces:**
- Consumes: `resolve_names` (with `dates`/`fetcher`).
- Produces:
  - `universe.resolve_member_names(members, name_index, *, name_cache=None, fetcher=None) -> tuple[list[dict], dict[str,str]]` — fills `cik` on members still missing one (that carry a `company`), using `first_seen`/`last_seen` as the date hint; returns `(members, resolved{company->cik})`.
  - `issuers_from_sp500(fetcher, *, start=None, current_only=False, name_index=None, name_cache=None)` — runs the name tier on still-unresolved members; resolved members get `resolution="name"`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_universe.py`:

```python
def test_resolve_member_names_fills_missing_cik():
    members = [
        {"ticker": "AAPL", "company": "Apple Inc.", "cik": "0000320193",
         "first_seen": "", "last_seen": "current"},
        {"ticker": "OLDCO", "company": "Sunrise Corp", "cik": "",
         "first_seen": "1998-01-02", "last_seen": "2012-03-04"},
        {"ticker": "GHOST", "company": "Phantom Industries", "cik": "",
         "first_seen": "", "last_seen": ""},
    ]
    index = {"SUNRISE": {"0000111111"}}  # unique -> no date lookup needed
    out, resolved = resolve_member_names(members, index)
    by_ticker = {m["ticker"]: m for m in out}
    assert by_ticker["OLDCO"]["cik"] == "0000111111"
    assert by_ticker["AAPL"]["cik"] == "0000320193"   # already had one, untouched
    assert by_ticker["GHOST"]["cik"] == ""            # not in index
    assert resolved == {"Sunrise Corp": "0000111111"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_universe.py -k "resolve_member_names" -q`
Expected: FAIL with `ImportError: cannot import name 'resolve_member_names'` (add it to the test-file import list).

- [ ] **Step 3: Implement `resolve_member_names` and wire into `issuers_from_sp500`**

Add before `issuers_from_sp500`:

```python
def resolve_member_names(
    members: list[dict],
    name_index: dict[str, set[str]],
    *,
    name_cache: dict[str, str] | None = None,
    fetcher: Fetcher | None = None,
) -> tuple[list[dict], dict[str, str]]:
    """Fill ``cik`` on members still missing one via the name tier.

    Only members with an empty ``cik`` and a non-empty ``company`` are
    considered; their membership window (``first_seen`` preferred, else
    ``last_seen``) is the date hint for the collision tie-breaker. Mutates the
    member dicts in place and returns ``(members, resolved{company->cik})``.
    """
    pending = [m for m in members if not m.get("cik") and m.get("company")]
    if not pending:
        return members, {}
    names = [m["company"] for m in pending]
    dates = {m["company"]: (m.get("first_seen") or m.get("last_seen") or "")
             for m in pending}
    resolved, _collisions, _unresolved = resolve_names(
        names, name_index, cache=name_cache, dates=dates, fetcher=fetcher)
    for m in members:
        if not m.get("cik") and m.get("company") in resolved:
            m["cik"] = resolved[m["company"]]
    return members, resolved
```

Replace the tail of `issuers_from_sp500` (lines 432-447, from the `# Resolve missing CIKs` comment to the `return`) with:

```python
    # Resolve missing CIKs (since-removed members): first the SEC ticker map...
    need = [m["ticker"] for m in members if not m.get("cik")]
    if need:
        table = load_company_tickers(fetcher)
        for m in members:
            if not m.get("cik") and m["ticker"] in table:
                m["cik"] = table[m["ticker"]].cik

    # ...then the name tier for whatever the ticker map still misses.
    name_resolved: dict[str, str] = {}
    if name_index is not None:
        members, name_resolved = resolve_member_names(
            members, name_index, name_cache=name_cache, fetcher=fetcher)

    issuers: list[Issuer] = []
    unresolved: list[str] = []
    for m in members:
        cik = m.get("cik") or ""
        if not cik:
            unresolved.append(m["ticker"])
        res = "name" if (cik and m.get("company") in name_resolved) else ""
        issuers.append(Issuer(cik=cik, ticker=m["ticker"], company=m.get("company", ""),
                              first_seen=m.get("first_seen", ""),
                              last_seen=m.get("last_seen", ""), resolution=res))
    return issuers, changes, unresolved
```

Update the `issuers_from_sp500` signature (line 411-413):

```python
def issuers_from_sp500(
    fetcher: Fetcher, *, start: str | None = None, current_only: bool = False,
    name_index: dict[str, set[str]] | None = None,
    name_cache: dict[str, str] | None = None,
) -> tuple[list[Issuer], list[dict], list[str]]:
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/python -m pytest tests/test_universe.py -k "resolve_member_names or sp500" -q`
Expected: PASS. Then run the whole universe suite: `./venv/bin/python -m pytest tests/test_universe.py -q` — all green (no regressions).

- [ ] **Step 5: Commit**

```bash
git add bottom_up_corpus/universe.py tests/test_universe.py
git commit -m "feat(universe): name tier in issuers_from_sp500 (recovers delisted members)"
```

---

### Task 8: CLI wiring — flags, both branches, ledger auto-write

**Files:**
- Modify: `bottom_up_corpus/cli.py` (imports; argparse lines 573-602; `_cmd_build_universe` sp500 branch lines 205-233; `_build_universe_from_file` lines 259-321)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `cik_lookup.fetch_cik_lookup`/`parse_cik_lookup`, `universe.load_name_cache`/`write_name_cache`, the new `name_index`/`name_cache` params on `issuers_from_sp500` and `reconcile_identifiers`, `Config.cik_lookup_path`/`name_cache_path`.
- Produces: `build-universe --no-name-resolution` (disable tier) and `--name-cache PATH` (override default ledger path). Tier on by default for name-bearing universes.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_cli.py` (reuse its existing `main`/`capsys`/`tmp_path` patterns; the file already imports `from bottom_up_corpus.cli import main` and uses `monkeypatch` for network):

```python
def test_build_universe_from_file_name_tier(tmp_path, monkeypatch, capsys):
    # A row whose ticker doesn't resolve falls through to the name tier; the
    # default ledger is written under data/reference/. The ticker column is
    # required (read_identifier_csv needs a CIK/Ticker/CUSIP column); the ticker
    # table is stubbed empty so ticker resolution misses and never hits network.
    monkeypatch.setattr("bottom_up_corpus.cli.load_company_tickers",
                        lambda fetcher: {})
    monkeypatch.setattr("bottom_up_corpus.cli.fetch_cik_lookup",
                        lambda fetcher, path: "WIDGET INC:0000999999:\n")

    csv_path = tmp_path / "names.csv"
    csv_path.write_text("Ticker,Name\nZZZZ,Widget Inc\n", encoding="utf-8")
    data_dir = tmp_path / "data"
    rc = main(["--data-dir", str(data_dir), "build-universe",
               "--from-file", str(csv_path), "--name", "names", "--write"])
    assert rc == 0
    ledger = data_dir / "reference" / "name_cik_cache.csv"
    assert ledger.exists()
    assert "WIDGET" in ledger.read_text(encoding="utf-8")
    out = (data_dir / "universe" / "names.jsonl").read_text(encoding="utf-8")
    assert "0000999999" in out


def test_build_universe_from_file_no_name_resolution(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("bottom_up_corpus.cli.load_company_tickers",
                        lambda fetcher: {})
    called = {"n": 0}
    def _boom(fetcher, path):
        called["n"] += 1
        return ""
    monkeypatch.setattr("bottom_up_corpus.cli.fetch_cik_lookup", _boom)

    csv_path = tmp_path / "names.csv"
    csv_path.write_text("Ticker,Name\nZZZZ,Widget Inc\n", encoding="utf-8")
    data_dir = tmp_path / "data"
    rc = main(["--data-dir", str(data_dir), "build-universe",
               "--from-file", str(csv_path), "--name", "names",
               "--no-name-resolution"])
    assert rc == 0
    assert called["n"] == 0  # tier disabled -> the lookup file is never fetched
    assert not (data_dir / "reference" / "name_cik_cache.csv").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./venv/bin/python -m pytest tests/test_cli.py -k "name_tier or no_name_resolution" -q`
Expected: FAIL (unrecognized argument `--no-name-resolution`, or `fetch_cik_lookup` not importable in `cli`).

- [ ] **Step 3: Implement the CLI wiring**

In `bottom_up_corpus/cli.py`, add imports near the other source/universe imports (top of file, alongside line 33 and the `universe` import block at lines ~42-47):

```python
from .sources.cik_lookup import fetch_cik_lookup, parse_cik_lookup
```

and extend the `from .universe import (...)` block to also import:

```python
    load_name_cache,
    write_name_cache,
```

Add the two argparse options to the `build-universe` parser (after line 593, the `--fts-cache` arg):

```python
    bu.add_argument("--no-name-resolution", action="store_true",
                    help="disable the name->CIK tier (default: on for name-bearing universes)")
    bu.add_argument("--name-cache", default=None,
                    help="name->CIK ledger CSV (default: data/reference/name_cik_cache.csv); "
                         "written whenever the name tier runs, independent of --write")
```

Add a small helper near the top of the command handlers (e.g. before `_cmd_build_universe`, line 193):

```python
def _name_tier(args, cfg, fetcher):
    """Return ``(name_index, name_cache, ledger_path)`` for the name->CIK tier,
    or ``(None, None, ledger_path)`` when disabled. Fetches the cached
    cik-lookup file (one GET) and loads the durable ledger."""
    ledger_path = args.name_cache or str(cfg.name_cache_path)
    if getattr(args, "no_name_resolution", False):
        return None, None, ledger_path
    text = fetch_cik_lookup(fetcher, cfg.cik_lookup_path)
    return parse_cik_lookup(text), load_name_cache(ledger_path), ledger_path
```

In the **sp500 branch** of `_cmd_build_universe` (lines 205-233), replace the `issuers_from_sp500(...)` call and add the ledger write. Change:

```python
        issuers, changes, unresolved = issuers_from_sp500(
            fetcher, start=args.since, current_only=args.current_only)
```

to:

```python
        name_index, name_cache, ledger_path = _name_tier(args, cfg, fetcher)
        issuers, changes, unresolved = issuers_from_sp500(
            fetcher, start=args.since, current_only=args.current_only,
            name_index=name_index, name_cache=name_cache)
        if name_index is not None:
            pairs = [(it.company, it.cik) for it in issuers
                     if it.resolution == "name" and it.cik and it.company]
            if pairs:
                total = write_name_cache(ledger_path, pairs)
                print(f"name-cache: merged {len(pairs)} name->CIK pin(s) -> "
                      f"{ledger_path} ({total} total)", file=sys.stderr)
```

In `_build_universe_from_file` (lines 280-283), build the name tier and pass it to `reconcile_identifiers`, then write the ledger after the summary. Change:

```python
    ticker_table = load_company_tickers(fetcher) if any(r["ticker"] for r in rows) else {}
    fts = EdgarFTS(fetcher) if args.fts else None
    issuers, collisions, unresolved = reconcile_identifiers(
        rows, ticker_table, crosswalk, fts=fts, fts_limit=args.fts_limit)
```

to:

```python
    ticker_table = load_company_tickers(fetcher) if any(r["ticker"] for r in rows) else {}
    fts = EdgarFTS(fetcher) if args.fts else None
    has_name = any(r["name"] for r in rows)
    name_index = name_cache = None
    ledger_path = args.name_cache or str(cfg.name_cache_path)
    if has_name and not args.no_name_resolution:
        name_index, name_cache, ledger_path = _name_tier(args, cfg, fetcher)
    issuers, collisions, unresolved = reconcile_identifiers(
        rows, ticker_table, crosswalk, fts=fts, fts_limit=args.fts_limit,
        name_index=name_index, name_cache=name_cache)
    if name_index is not None:
        pairs = [(i.company, i.cik) for i in issuers
                 if i.resolution == "name" and i.cik and i.company]
        if pairs:
            total = write_name_cache(ledger_path, pairs)
            print(f"name-cache: merged {len(pairs)} name->CIK pin(s) -> "
                  f"{ledger_path} ({total} total)", file=sys.stderr)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./venv/bin/python -m pytest tests/test_cli.py -k "name_tier or no_name_resolution" -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add bottom_up_corpus/cli.py tests/test_cli.py
git commit -m "feat(cli): build-universe name->CIK tier (default-on) + ledger auto-write"
```

---

### Task 9: Docs + runnable example + full-suite gate

**Files:**
- Modify: `README.md` (the *Issuer universe* / `build-universe` section), `docs/ROADMAP.md`, `examples/README.md`
- Create: `examples/15_name_resolution.py`

**Interfaces:**
- Consumes: `universe.resolve_names`, `sources.cik_lookup.parse_cik_lookup` (offline example).

- [ ] **Step 1: Write the runnable example**

Create `examples/15_name_resolution.py`:

```python
"""Resolve company names to CIKs via the SEC cik-lookup-data.txt index.

The current ticker map lists only *trading* issuers, so delisted/renamed members
don't resolve by ticker. The name tier matches on a strict canonical name against
the SEC's full name->CIK file (former names included), and breaks a name borne by
two companies using their dated formerNames. Fully offline (a tiny inline index).

    ./venv/bin/python examples/15_name_resolution.py
"""
from __future__ import annotations

from bottom_up_corpus.sources.cik_lookup import parse_cik_lookup
from bottom_up_corpus.universe import resolve_names

# A slice of cik-lookup-data.txt: APPLE (one CIK) and a recycled "SUNRISE" name.
index = parse_cik_lookup(
    "APPLE INC:0000320193:\n"
    "APPLE COMPUTER INC:0000320193:\n"   # former name -> same CIK
    "SUNRISE CORP:0000111111:\n"
    "SUNRISE CORPORATION:0000222222:\n"  # same canonical name -> collision
)

resolved, collisions, unresolved = resolve_names(
    ["Apple Computer, Inc.", "Sunrise Corp", "Nonesuch Holdings"], index)

print("resolved   :", resolved)       # Apple's old name -> current CIK
print("collisions :", collisions)     # Sunrise -> two CIKs (needs a date to break)
print("unresolved :", unresolved)     # not in the index
```

- [ ] **Step 2: Run the example to verify it works**

Run: `./venv/bin/python examples/15_name_resolution.py`
Expected output:
```
resolved   : {'Apple Computer, Inc.': '0000320193'}
collisions : [{'name': 'Sunrise Corp', 'candidates': ['0000111111', '0000222222']}]
unresolved : ['Nonesuch Holdings']
```

- [ ] **Step 3: Update the docs**

In `examples/README.md`, add a row under **Other capabilities**:

```markdown
| `15_name_resolution.py` | Resolve company names → CIKs via the SEC name index (former names, collision detection) *(offline)* |
```

In `README.md`, in the `build-universe` section, add a short paragraph:

```markdown
**Name→CIK resolution (on by default).** When ticker and CUSIP both miss a
name-bearing row, `build-universe` resolves it by *name* against the SEC
`cik-lookup-data.txt` file (a single cached download covering all filers,
former names included) — this recovers delisted/renamed members such as the
~139 historical S&P 500 names the current ticker map drops. Matching is exact
after strict normalization; a name borne by two companies is a collision,
broken by their dated `formerNames` when a membership date is known. Resolved
decisions accumulate in `data/reference/name_cik_cache.csv` (override with
`--name-cache`). Disable the tier with `--no-name-resolution`.
```

In `docs/ROADMAP.md`, mark the name→CIK resolver as delivered (move it from backlog to done, mirroring how earlier tiers are recorded).

- [ ] **Step 4: Run the full test suite (regression gate)**

Run: `./venv/bin/python -m pytest -q`
Expected: all tests pass (no regressions across the suite).

- [ ] **Step 5: Commit**

```bash
git add examples/15_name_resolution.py examples/README.md README.md docs/ROADMAP.md
git commit -m "docs: document the name->CIK resolution tier + runnable example"
```

---

## Self-Review

**Spec coverage:**
- Index source `cik-lookup-data.txt` → Task 2. ✓
- `canonical_name` strict normalization → Task 1. ✓
- `resolve_names` exact match, collision→unresolved → Task 4. ✓
- Date tie-breaker via dated `formerNames` → Task 5. ✓
- Durable ledger (`load/write_name_cache`, default path, auto-write) → Tasks 4 + 8. ✓
- `reference_dir`/paths → Task 3. ✓
- Integration `reconcile_identifiers` (name before fts) → Task 6. ✓
- Integration `issuers_from_sp500` (recover the 139) → Task 7. ✓
- CLI default-on + `--no-name-resolution` + `--name-cache` → Task 8. ✓
- Tests via FakeFetcher + tmp_path → every task. ✓
- Docs + example → Task 9. ✓

**Placeholder scan:** No TBD/TODO; every code step shows full code. ✓

**Type consistency:** `resolve_names(names, index, *, cache, dates, fetcher)` defined in Task 4 (cache) and extended in Task 5 (dates/fetcher) — callers in Tasks 6/7 use the final signature. `_resolve_one_name` returns `(status, value)` used identically in Tasks 4 and 6. `name_index: dict[str, set[str]]` and `name_cache: dict[str, str]` types consistent across Tasks 4-8. Ledger keyed by `canonical_name` everywhere (`load/write_name_cache` + `resolve_names` cache lookup). `resolution="name"` label consistent across Tasks 6/7/8.

**Simplification note (vs spec):** the spec floated `resolution` sub-labels `name:dated` / `name:cache`; the plan uses a single `"name"` label (YAGNI — the universe row records the CIK; provenance granularity adds churn with no consumer). Flagged for the reviewer.
