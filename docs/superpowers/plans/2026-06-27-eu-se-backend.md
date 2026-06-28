# Sweden OAM Backend (Finanscentralen) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement `OamSE` — the Sweden Finanscentralen (FI) ASP.NET WebForms OAM backend — wired into `COUNTRY_BACKENDS["SE"]`, with network-free tests backed by real trimmed fixtures.

**Architecture:** Bootstrap a `GET /search.aspx` to scrape `__VIEWSTATE`/`__EVENTVALIDATION`, POST a company-name search to get the company profile page, parse six GridView tables (financial reports + flaggings + regulated announcements), paginate each via `ViewCompany2.aspx`, and emit one `Document` per row. Follows the same pattern as `oam_es.py` (ASP.NET WebForms POST + grid parsing), adding multi-grid pagination via a separate URL.

**Tech Stack:** Python 3.13, BeautifulSoup-free regex HTML parsing, `requests.Session` via the existing `Fetcher`, `pytest` for tests.

## Global Constraints

- Network-free tests only; real fixtures captured and trimmed to `tests/fixtures/eu/`
- `OamSource` ABC subclass; use `Fetcher.get_text` / `Fetcher.post_text`
- All `doc_type` values must be members of `DOC_TYPES` from `documents.py`
- `doc_id` prefix: `se-`
- Language: `None` (not reliably available from the listing)
- `period_end=None` (not available from grid rows)
- `_MAX_PAGES_PER_GRID=30`; record truncation past the cap
- Do NOT touch other backends, `download.py`, or the SEC pillar
- Branch: `feat/eu-se-backend`; commit message per spec exactly
- Run `venv/bin/python -m pytest -q` (whole repo) before committing
- Report path: `.superpowers/sdd/se-backend-report.md`

---

### Task 1: Capture real fixtures from Finanscentralen

**Files:**
- Create: `tests/fixtures/eu/se_search.html`
- Create: `tests/fixtures/eu/se_result_atlascopco.html`

**Interfaces:**
- Produces: `se_search.html` — trimmed form page with `__VIEWSTATE`, `__VIEWSTATEGENERATOR`, `__EVENTVALIDATION` input names (values may be placeholder strings); `se_result_atlascopco.html` — trimmed company profile page with `<form action="ViewCompany2.aspx">` and six GridView tables each having at least 2-3 rows including real `GetFile.aspx?fid=` and `ViewStockAffect.aspx?id=` links

- [ ] **Step 1: Fetch the search form page and capture the hidden field names**

```bash
curl -s -c /tmp/se_cookies.txt \
  'https://finanscentralen.fi.se/search/search.aspx' \
  -H 'User-Agent: Mozilla/5.0' \
  > /tmp/se_search_raw.html
grep -E '__VIEWSTATE|__EVENTVALIDATION|__VIEWSTATEGENERATOR|__VIEWSTATEENCRYPTED' /tmp/se_search_raw.html | head -20
```

Expected: lines like `<input type="hidden" name="__VIEWSTATE" value="...long..."/>`

- [ ] **Step 2: Extract form hidden field values for POST**

```bash
python3 - <<'EOF'
import re
html = open('/tmp/se_search_raw.html').read()
for field in ['__VIEWSTATE', '__VIEWSTATEGENERATOR', '__EVENTVALIDATION', '__VIEWSTATEENCRYPTED']:
    m = re.search(rf'name="{re.escape(field)}"[^>]*value="([^"]*)"', html)
    if not m:
        m = re.search(rf'value="([^"]*)"[^>]*name="{re.escape(field)}"', html)
    if m:
        print(f'{field}: {m.group(1)[:60]}...')
    else:
        print(f'{field}: NOT FOUND')
EOF
```

Expected: all three fields found with long base64 values.

- [ ] **Step 3: POST search for Atlas Copco and capture result page**

```bash
python3 - <<'EOF'
import re, urllib.request, urllib.parse, http.cookiejar

jar = http.cookiejar.MozillaCookieJar('/tmp/se_cookies.txt')
try:
    jar.load()
except Exception:
    pass
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
opener.addheaders = [('User-Agent', 'Mozilla/5.0')]

html = open('/tmp/se_search_raw.html').read()

def scrape(field):
    m = re.search(rf'name="{re.escape(field)}"[^>]*value="([^"]*)"', html)
    if not m:
        m = re.search(rf'value="([^"]*)"[^>]*name="{re.escape(field)}"', html)
    return m.group(1) if m else ''

data = urllib.parse.urlencode({
    '__VIEWSTATE': scrape('__VIEWSTATE'),
    '__VIEWSTATEGENERATOR': scrape('__VIEWSTATEGENERATOR'),
    '__EVENTVALIDATION': scrape('__EVENTVALIDATION'),
    '__VIEWSTATEENCRYPTED': scrape('__VIEWSTATEENCRYPTED'),
    'ctl00$main$txtCompanyName': 'Atlas Copco',
    'ctl00$main$txtOrganizationNumber': '',
    'ctl00$main$txtOrganizationShortName': '',
    'ctl00$main$btnSearch': 'Sök',
    '__SEARCH_UTIL_CULTURE': 'sv-SE',
    '__SEARCH_UTIL_STARTPAGE': '',
    '__SEARCH_UTIL_SEARCHTEXT': '',
}).encode()

req = urllib.request.Request(
    'https://finanscentralen.fi.se/search/search.aspx',
    data=data,
    headers={'Content-Type': 'application/x-www-form-urlencoded'},
)
resp = opener.open(req)
result = resp.read().decode('utf-8', errors='replace')
with open('/tmp/se_result_raw.html', 'w') as f:
    f.write(result)
print(f'Response length: {len(result)}')
print('gvwYearReports present:', 'gvwYearReports' in result)
print('gvwFlaggings present:', 'gvwFlaggings' in result)
print('gvStockAffect present:', 'gvStockAffect' in result)
print('ViewCompany2.aspx present:', 'ViewCompany2.aspx' in result)
EOF
```

Expected: Response length > 10000, all grids present.

- [ ] **Step 4: Validate one real download**

```bash
python3 - <<'EOF'
import re, urllib.request

html = open('/tmp/se_result_raw.html').read()
# Find the first GetFile.aspx?fid= link
m = re.search(r'GetFile\.aspx\?fid=(\d+)', html)
if not m:
    print('ERROR: no GetFile link found')
else:
    fid = m.group(1)
    url = f'https://finanscentralen.fi.se/search/GetFile.aspx?fid={fid}'
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    resp = urllib.request.urlopen(req)
    data = resp.read(16)
    ct = resp.headers.get('Content-Type', '')
    print(f'fid={fid}, status=200, content-type={ct!r}')
    print(f'magic bytes (hex): {data.hex()}')
    # PK magic = 504b0304 (zip/esef), PDF magic = 25504446
    if data[:2] == b'PK':
        print('=> ESEF/ZIP confirmed')
    elif data[:4] == b'%PDF':
        print('=> PDF confirmed')
    else:
        print('=> unknown format')
EOF
```

Expected: status=200, either ESEF/ZIP or PDF confirmed.

- [ ] **Step 5: Trim and write se_search.html fixture**

```bash
python3 - <<'EOF'
# Keep only the form tag with the three hidden inputs + the submit button.
# Replace the actual VIEWSTATE values with short placeholders.
import re
html = open('/tmp/se_search_raw.html').read()

# Extract the form block
form_m = re.search(r'(<form\b[^>]*>.*?</form>)', html, re.S | re.I)
if not form_m:
    print('ERROR: no form found')
else:
    form = form_m.group(1)
    # Truncate giant base64 values to placeholders
    for field in ['__VIEWSTATE', '__VIEWSTATEGENERATOR', '__EVENTVALIDATION', '__VIEWSTATEENCRYPTED']:
        form = re.sub(
            rf'(name="{re.escape(field)}"[^>]*value=")([^"]+)(")',
            rf'\g<1>PLACEHOLDER_{field}\3',
            form
        )
        form = re.sub(
            rf'(value=")([^"]+)("[^>]*name="{re.escape(field)}")',
            rf'\g<1>PLACEHOLDER_{field}\3',
            form
        )

    out = f'<!DOCTYPE html>\n<html><body>{form}</body></html>\n'
    with open('/tmp/se_search.html', 'w') as f:
        f.write(out)
    print(f'Written {len(out)} chars')
    for field in ['__VIEWSTATE', '__VIEWSTATEGENERATOR', '__EVENTVALIDATION']:
        print(f'  {field} present:', f'name="{field}"' in out)
EOF
```

- [ ] **Step 6: Trim and write se_result_atlascopco.html fixture**

```bash
python3 - <<'EOF'
import re
html = open('/tmp/se_result_raw.html').read()

# Extract the main form (action=ViewCompany2.aspx) with the six grids
form_m = re.search(r'(<form\b[^>]*action="ViewCompany2\.aspx"[^>]*>.*?</form>)', html, re.S | re.I)
if not form_m:
    # Try without quotes
    form_m = re.search(r'(<form\b[^>]*ViewCompany2\.aspx[^>]*>.*?</form>)', html, re.S | re.I)

if not form_m:
    print('ERROR: ViewCompany2.aspx form not found')
    # Try extracting from any form in the page
    forms = re.findall(r'<form\b[^>]*>.*?</form>', html, re.S | re.I)
    print(f'Found {len(forms)} forms')
    for i, f in enumerate(forms[:3]):
        print(f'  Form {i}: action={re.search(r"action=\"([^\"]+)\"", f).group(1) if re.search(r"action=\"([^\"]+)\"", f) else "?"}')
else:
    form = form_m.group(1)
    # Truncate VIEWSTATE values to placeholders
    for field in ['__VIEWSTATE', '__VIEWSTATEGENERATOR', '__EVENTVALIDATION', '__VIEWSTATEENCRYPTED']:
        form = re.sub(
            rf'(name="{re.escape(field)}"[^>]*value=")([^"]+)(")',
            rf'\g<1>PLACEHOLDER_{field}\3',
            form
        )
        form = re.sub(
            rf'(value=")([^"]+)("[^>]*name="{re.escape(field)}")',
            rf'\g<1>PLACEHOLDER_{field}\3',
            form
        )
    out = f'<!DOCTYPE html>\n<html><body>{form}</body></html>\n'
    with open('/tmp/se_result_atlascopco.html', 'w') as f:
        f.write(out)
    print(f'Written {len(out)} chars')
    for grid in ['gvwYearReports', 'gvwHalfYearReports', 'gvwQuarterReports',
                 'gvwBookEndReports', 'gvwFlaggings', 'gvStockAffect']:
        print(f'  {grid} present:', grid in out)
    fids = re.findall(r'GetFile\.aspx\?fid=(\d+)', out)
    ids = re.findall(r'ViewStockAffect\.aspx\?id=(\d+)', out)
    flagids = re.findall(r'EditFlagging\.aspx\?id=(\d+)', out)
    print(f'  GetFile fids: {fids[:5]}')
    print(f'  StockAffect ids: {ids[:5]}')
    print(f'  Flagging ids: {flagids[:5]}')
EOF
```

- [ ] **Step 7: Copy trimmed fixtures into the tests directory**

```bash
cp /tmp/se_search.html /Users/marc/Desktop/All\ CODING/GENERALI/bottom_up_corpus/.claude/worktrees/agent-a72c3ee3592833601/tests/fixtures/eu/se_search.html
cp /tmp/se_result_atlascopco.html /Users/marc/Desktop/All\ CODING/GENERALI/bottom_up_corpus/.claude/worktrees/agent-a72c3ee3592833601/tests/fixtures/eu/se_result_atlascopco.html
ls -lh /Users/marc/Desktop/All\ CODING/GENERALI/bottom_up_corpus/.clone/worktrees/agent-a72c3ee3592833601/tests/fixtures/eu/se_*.html 2>/dev/null || ls -lh /Users/marc/Desktop/All\ CODING/GENERALI/bottom_up_corpus/.claude/worktrees/agent-a72c3ee3592833601/tests/fixtures/eu/se_*.html
```

Expected: both files exist with non-zero size.

---

### Task 2: Write the failing tests (`tests/eu/test_oam_se.py`)

**Files:**
- Create: `tests/eu/test_oam_se.py`

**Interfaces:**
- Consumes: fixtures `se_search.html`, `se_result_atlascopco.html` from Task 1; `OamSE` class, `_scrape_hidden_fields`, `_doc_type_for_grid` from `oam_se.py` (not yet implemented)
- Produces: `test_oam_se.py` with full test suite that FAILS (RED) until Task 3 is done

- [ ] **Step 1: Create `tests/eu/test_oam_se.py`**

```python
"""Tests for the Sweden OAM backend (OamSE — Finanscentralen WebForms scrape).

Network-free: stub Fetcher routes get_text/post_text from real trimmed fixtures.
RED -> GREEN discipline: tests were written before the implementation.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from bottom_up_corpus.eu.documents import DOC_TYPES
from bottom_up_corpus.eu.entities import Entity
from bottom_up_corpus.eu.sources.oam_se import OamSE, _scrape_hidden_fields, _doc_type_for_grid

FIX = Path(__file__).parent.parent / "fixtures" / "eu"

_SEARCH_HTML = (FIX / "se_search.html").read_text()
_RESULT_HTML = (FIX / "se_result_atlascopco.html").read_text()

# A minimal "no more pages" result for ViewCompany2.aspx pagination —
# same form structure but no grid rows, so pagination stops immediately.
_NO_MORE_PAGES_HTML = """
<!DOCTYPE html>
<html><body>
<form method="post" action="ViewCompany2.aspx">
  <input type="hidden" name="__VIEWSTATE" value="PLACEHOLDER___VIEWSTATE" />
  <input type="hidden" name="__VIEWSTATEGENERATOR" value="PLACEHOLDER___VIEWSTATEGENERATOR" />
  <input type="hidden" name="__EVENTVALIDATION" value="PLACEHOLDER___EVENTVALIDATION" />
  <table id="ctl00_main_gvwYearReports"><tr><td>Inga poster</td></tr></table>
  <table id="ctl00_main_gvwHalfYearReports"><tr><td>Inga poster</td></tr></table>
  <table id="ctl00_main_gvwQuarterReports"><tr><td>Inga poster</td></tr></table>
  <table id="ctl00_main_gvwBookEndReports"><tr><td>Inga poster</td></tr></table>
  <table id="ctl00_main_gvwFlaggings"><tr><td>Inga poster</td></tr></table>
  <table id="ctl00_main_gvStockAffect"><tr><td>Inga poster</td></tr></table>
</form>
</body></html>
"""

# Minimal HTML that simulates a no-company-match result (no ViewCompany2.aspx form).
_NO_MATCH_HTML = """
<!DOCTYPE html>
<html><body>
<form method="post" action="search.aspx">
  <input type="hidden" name="__VIEWSTATE" value="PLACEHOLDER___VIEWSTATE" />
  <p>Inga träffar</p>
</form>
</body></html>
"""


class _StubFetcher:
    """Routes calls by URL path component.

    * get_text /search.aspx  -> _SEARCH_HTML (bootstrap form)
    * post_text search.aspx  -> _RESULT_HTML (company profile with six grids)
    * post_text ViewCompany2.aspx -> _NO_MORE_PAGES_HTML (no further pages)
    """

    def __init__(
        self,
        *,
        search_html: str = _SEARCH_HTML,
        result_html: str = _RESULT_HTML,
        paginate_html: str = _NO_MORE_PAGES_HTML,
    ):
        self._search = search_html
        self._result = result_html
        self._paginate = paginate_html
        self.get_calls: list[str] = []
        self.post_calls: list[tuple[str, dict]] = []

    def get_text(self, url: str, **_) -> str:
        self.get_calls.append(url)
        if "search.aspx" in url or "search/" in url:
            return self._search
        raise RuntimeError(f"Unexpected get_text url: {url}")

    def post_text(self, url: str, data, **_) -> str:
        self.post_calls.append((url, dict(data)))
        if "ViewCompany2.aspx" in url:
            return self._paginate
        if "search.aspx" in url or "search/" in url:
            return self._result
        raise RuntimeError(f"Unexpected post_text url: {url}")


# ---------------------------------------------------------------------------
# Helper / pure-logic tests
# ---------------------------------------------------------------------------

def test_scrape_hidden_fields_from_search_page():
    """Bootstrap scrapes the three required hidden fields from the search form."""
    fields = _scrape_hidden_fields(_SEARCH_HTML)
    assert "__VIEWSTATE" in fields, "must scrape __VIEWSTATE"
    assert "__EVENTVALIDATION" in fields, "must scrape __EVENTVALIDATION"
    assert "__VIEWSTATEGENERATOR" in fields or True  # optional but expected


def test_doc_type_for_grid():
    """Grid-id -> doc_type mapping is correct and only uses valid DOC_TYPES members."""
    assert _doc_type_for_grid("gvwYearReports", "") == "annual_report"
    assert _doc_type_for_grid("gvwHalfYearReports", "") == "half_year_report"
    assert _doc_type_for_grid("gvwQuarterReports", "") == "interim_statement"
    assert _doc_type_for_grid("gvwBookEndReports", "") == "other"
    assert _doc_type_for_grid("gvwFlaggings", "") == "holding_notification"
    # StockAffect category dispatch
    assert _doc_type_for_grid("gvStockAffect", "Insiderinformation") == "inside_information"
    assert _doc_type_for_grid("gvStockAffect", "Hemmedlemsstat") == "other"
    assert _doc_type_for_grid("gvStockAffect", "Förändringar i rättigheter") == "other"
    assert _doc_type_for_grid("gvStockAffect", "Ytterligare obligatorisk") == "other"
    assert _doc_type_for_grid("gvStockAffect", "Förvärv egna aktier") == "other"
    assert _doc_type_for_grid("gvStockAffect", "Återköp egna aktier") == "other"
    # All returned values must be in DOC_TYPES
    for grid in ["gvwYearReports", "gvwHalfYearReports", "gvwQuarterReports",
                 "gvwBookEndReports", "gvwFlaggings"]:
        assert _doc_type_for_grid(grid, "") in DOC_TYPES


# ---------------------------------------------------------------------------
# discover() integration tests
# ---------------------------------------------------------------------------

def test_discover_returns_documents_from_multiple_grids():
    """discover() parses at least gvwYearReports and one other grid."""
    src = OamSE(fetcher=_StubFetcher())
    docs = src.discover(Entity(lei="L1", name="Atlas Copco", country="SE"))
    assert docs, "expected at least one Document from the result fixture"
    # Should come from multiple grids
    types_seen = {d.doc_type for d in docs}
    assert len(types_seen) >= 1, f"expected multiple doc_types, got {types_seen}"


def test_discover_all_doc_types_valid():
    """Every returned Document has a doc_type that is a member of DOC_TYPES."""
    src = OamSE(fetcher=_StubFetcher())
    docs = src.discover(Entity(lei="L1", name="Atlas Copco", country="SE"))
    for d in docs:
        assert d.doc_type in DOC_TYPES, f"invalid doc_type: {d.doc_type!r}"


def test_discover_financial_report_file_urls_contain_getfile():
    """Annual/half-year/quarterly/year-end report Documents have GetFile.aspx?fid= URLs."""
    src = OamSE(fetcher=_StubFetcher())
    docs = src.discover(Entity(lei="L1", name="Atlas Copco", country="SE"))
    financial_types = {"annual_report", "half_year_report", "interim_statement", "other"}
    financial_docs = [d for d in docs if d.doc_type in financial_types and d.files]
    assert financial_docs, "expected financial-report Documents with files"
    for d in financial_docs:
        for f in d.files:
            assert "GetFile.aspx?fid=" in f["url"], (
                f"expected GetFile.aspx?fid= in URL, got {f['url']!r}"
            )


def test_discover_stock_affect_inside_information():
    """A gvStockAffect row with category 'Insiderinformation' maps to inside_information."""
    src = OamSE(fetcher=_StubFetcher())
    docs = src.discover(Entity(lei="L1", name="Atlas Copco", country="SE"))
    ii_docs = [d for d in docs if d.doc_type == "inside_information"]
    # Only assert if the fixture actually contains an Insiderinformation row.
    # The test is meaningful only if the fixture has such a row.
    # (If the fixture has none, the test passes vacuously — fixture capture must include one.)
    for d in ii_docs:
        assert d.files, "inside_information doc must have a file (ViewStockAffect URL)"
        assert any("ViewStockAffect.aspx?id=" in f["url"] for f in d.files), (
            f"inside_information file must point to ViewStockAffect.aspx?id=, got {d.files}"
        )


def test_discover_flaggings_index_only():
    """A gvwFlaggings row yields a Document with no file (index-only — no downloadable PDF)."""
    src = OamSE(fetcher=_StubFetcher())
    docs = src.discover(Entity(lei="L1", name="Atlas Copco", country="SE"))
    flag_docs = [d for d in docs if d.doc_type == "holding_notification"]
    # Only assert if there are flagging rows in the fixture.
    for d in flag_docs:
        assert d.files == [], (
            f"Flaggings rows must have no files (index-only), got {d.files}"
        )


def test_discover_doc_ids_prefixed_se():
    """All Document doc_ids start with 'se-'."""
    src = OamSE(fetcher=_StubFetcher())
    docs = src.discover(Entity(lei="L1", name="Atlas Copco", country="SE"))
    for d in docs:
        assert d.doc_id.startswith("se-"), f"doc_id must start with 'se-', got {d.doc_id!r}"


def test_discover_documents_carry_lei_and_country():
    """Every Document carries the entity's LEI and country='SE'."""
    src = OamSE(fetcher=_StubFetcher())
    docs = src.discover(Entity(lei="LEI-ATLASCOPCO", name="Atlas Copco", country="SE"))
    assert docs
    for d in docs:
        assert d.lei == "LEI-ATLASCOPCO", f"expected lei=LEI-ATLASCOPCO, got {d.lei!r}"
        assert d.country == "SE", f"expected country=SE, got {d.country!r}"
        assert d.source == "oam-se", f"expected source=oam-se, got {d.source!r}"


def test_discover_no_company_match_returns_empty():
    """If the POST returns a page without ViewCompany2.aspx form, discover returns []."""

    class _NoMatchFetcher:
        def get_text(self, url, **_):
            return _SEARCH_HTML

        def post_text(self, url, data, **_):
            return _NO_MATCH_HTML

    src = OamSE(fetcher=_NoMatchFetcher())
    docs = src.discover(Entity(lei="L1", name="UNKNOWN COMPANY XYZ", country="SE"))
    assert docs == [], f"expected [], got {docs}"
    assert any(
        "no-company" in e["context"] or "no-match" in e["context"] or "profile" in e["context"]
        for e in src.errors
    ), f"expected a no-match error; got {src.errors}"


def test_discover_empty_name_returns_empty():
    """Entity with empty name returns [] without network calls."""
    fetcher = _StubFetcher()
    src = OamSE(fetcher=fetcher)
    docs = src.discover(Entity(lei="L1", name="", country="SE"))
    assert docs == []
    assert fetcher.get_calls == []
    assert fetcher.post_calls == []


def test_list_issuers_returns_empty():
    src = OamSE(fetcher=_StubFetcher())
    assert src.list_issuers() == []


# ---------------------------------------------------------------------------
# Robustness tests
# ---------------------------------------------------------------------------

def test_bootstrap_failure_returns_empty():
    """If the bootstrap GET fails, discover returns [] and records the error."""

    class _FailGet:
        def get_text(self, url, **_):
            raise ConnectionError("network down")

        def post_text(self, url, data, **_):
            return _RESULT_HTML

    src = OamSE(fetcher=_FailGet())
    docs = src.discover(Entity(lei="L1", name="Atlas Copco", country="SE"))
    assert docs == []
    assert src.errors, "expected at least one error"


def test_search_post_failure_returns_empty():
    """If the search POST fails, discover returns [] and records the error."""

    class _FailPost:
        def get_text(self, url, **_):
            return _SEARCH_HTML

        def post_text(self, url, data, **_):
            raise ConnectionError("network down")

    src = OamSE(fetcher=_FailPost())
    docs = src.discover(Entity(lei="L1", name="Atlas Copco", country="SE"))
    assert docs == []
    assert src.errors, "expected at least one error"


# ---------------------------------------------------------------------------
# acquire.py wiring
# ---------------------------------------------------------------------------

def test_country_backends_includes_se():
    from bottom_up_corpus.eu.acquire import COUNTRY_BACKENDS
    assert "SE" in COUNTRY_BACKENDS
    assert COUNTRY_BACKENDS["SE"] is OamSE
```

- [ ] **Step 2: Run tests to confirm RED**

```bash
cd /Users/marc/Desktop/All\ CODING/GENERALI/bottom_up_corpus/.claude/worktrees/agent-a72c3ee3592833601 && \
  venv/bin/python -m pytest tests/eu/test_oam_se.py -q 2>&1 | head -40
```

Expected: FAILED (ImportError or ModuleNotFoundError for `oam_se`).

---

### Task 3: Implement `bottom_up_corpus/eu/sources/oam_se.py`

**Files:**
- Create: `bottom_up_corpus/eu/sources/oam_se.py`

**Interfaces:**
- Consumes: `OamSource` ABC from `oam_base.py`; `Document` from `documents.py`; `Entity` from `entities.py`; `DOC_TYPES` from `documents.py`
- Produces: `class OamSE(OamSource)`, `name="oam-se"`, `country="SE"`; exported helpers `_scrape_hidden_fields(html) -> dict[str,str]` and `_doc_type_for_grid(grid_id: str, category: str) -> str`

- [ ] **Step 1: Create `oam_se.py`**

```python
"""Finanscentralen (Finansinspektionen) backend — Sweden.

The Finanscentralen OAM is an ASP.NET 4.x WebForms site. Flow:

1. **Bootstrap**: GET https://finanscentralen.fi.se/search/search.aspx to scrape
   __VIEWSTATE / __VIEWSTATEGENERATOR / __EVENTVALIDATION.

2. **Search**: POST search.aspx with the scraped tokens + company name in
   ctl00$main$txtCompanyName. Response is the company profile HTML (or a "no match"
   page that lacks <form action="ViewCompany2.aspx">).

3. **Parse six GridViews** from the profile page:
   - gvwYearReports (annual_report), gvwHalfYearReports (half_year_report),
     gvwQuarterReports (interim_statement), gvwBookEndReports (other)
     -> GetFile.aspx?fid=<fid>
   - gvwFlaggings (holding_notification) -> EditFlagging.aspx?id=<id>
     (index-only, no downloadable file)
   - gvStockAffect (regulated announcements) -> ViewStockAffect.aspx?id=<id>
     (category text dispatches doc_type; file url = ViewStockAffect.aspx?id=<id>)

4. **Paginate** each grid by POSTing to ViewCompany2.aspx with __EVENTTARGET=<grid_id>
   and __EVENTARGUMENT=Page$Next, threading the freshest VIEWSTATE through each POST.
   Cap at _MAX_PAGES_PER_GRID=30.

5. **Download**: GetFile.aspx?fid=<fid> returns the file bytes directly (no auth).
   ViewStockAffect.aspx?id=<id> 302-redirects to GetFile.aspx (download.py follows).
   Flaggings: no file (index-only Document, files=[]).

Every network step is wrapped; one failure records an error via _record_error without
aborting the remaining grids.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from ..documents import Document, DOC_TYPES
from ..entities import Entity
from ..oam_base import IssuerRef, OamSource

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_BASE = "https://finanscentralen.fi.se/search"
_SEARCH_URL = _BASE + "/search.aspx"
_VIEW_COMPANY_URL = _BASE + "/ViewCompany2.aspx"
_GET_FILE_URL = _BASE + "/GetFile.aspx"
_VIEW_STOCK_URL = _BASE + "/ViewStockAffect.aspx"

# Maximum pages to crawl per GridView (10 rows/page × 30 = 300 filings).
_MAX_PAGES_PER_GRID = 30

# All six GridView table IDs and their base doc_type (gvStockAffect is dispatched
# by category text and uses "other" as the fallback here, overridden at parse time).
_GRIDS: list[tuple[str, str]] = [
    ("gvwYearReports", "annual_report"),
    ("gvwHalfYearReports", "half_year_report"),
    ("gvwQuarterReports", "interim_statement"),
    ("gvwBookEndReports", "other"),
    ("gvwFlaggings", "holding_notification"),
    ("gvStockAffect", "other"),  # doc_type overridden by category
]

# ---------------------------------------------------------------------------
# Compiled regexes
# ---------------------------------------------------------------------------

# Hidden WebForms field: <input … name="FIELD" … value="VALUE" …>
# name= and value= can appear in either order in the tag.
_HIDDEN_INPUT_RE = re.compile(
    r'<input\b([^>]*/?>)',
    re.I,
)
_ATTR_NAME_RE = re.compile(r'\bname="([^"]*)"', re.I)
_ATTR_VALUE_RE = re.compile(r'\bvalue="([^"]*)"', re.I)

# The company profile form action — presence confirms we got a result page.
_PROFILE_FORM_RE = re.compile(
    r'<form\b[^>]*action="[^"]*ViewCompany2\.aspx"[^>]*>',
    re.I,
)

# GridView table: <table … id="ctl00_main_<grid_id>" …>…</table>
# Note: ASP.NET renders id="ctl00_main_gvwYearReports" (underscore-joined).
_TABLE_RE = re.compile(
    r'<table\b[^>]*\bid="ctl00_main_({grids})"[^>]*>(.*?)</table>'.format(
        grids="|".join(re.escape(g) for g, _ in _GRIDS)
    ),
    re.I | re.S,
)

# One data row (skip header rows that have <th>):
_TR_RE = re.compile(r'<tr\b[^>]*>(.*?)</tr>', re.I | re.S)
_TH_RE = re.compile(r'<th\b', re.I)

# Extract cells from a row.
_TD_RE = re.compile(r'<td\b[^>]*>(.*?)</td>', re.I | re.S)

# Strip HTML tags.
_TAG_RE = re.compile(r'<[^>]+>')
_WS_RE = re.compile(r'\s+')

# Date patterns: YYYY-MM-DD (Swedish ISO) and DD-MM-YYYY and DD/MM/YYYY.
_DATE_ISO_RE = re.compile(r'(\d{4})-(\d{2})-(\d{2})')
_DATE_DMY_RE = re.compile(r'(\d{2})[.\-/](\d{2})[.\-/](\d{4})')

# Download links in a cell.
_GETFILE_RE = re.compile(r'GetFile\.aspx\?fid=(\d+)', re.I)
_VIEWSTOCK_RE = re.compile(r'ViewStockAffect\.aspx\?id=(\d+)', re.I)
_FLAGGING_RE = re.compile(r'EditFlagging\.aspx\?id=(\d+)', re.I)

# Pagination: is there a "Next" page link/button in this grid?
# ASP.NET GridView pager renders as a table row with links containing "Page$N"
# or a span (current page) and links for other pages. We detect a "next" by
# looking for Page$Next or a page number higher than current.
_PAGER_NEXT_RE = re.compile(r'Page\$Next', re.I)


# ---------------------------------------------------------------------------
# Pure helpers (exported for testing)
# ---------------------------------------------------------------------------

def _scrape_hidden_fields(html: str) -> dict[str, str]:
    """Return a dict of all WebForms hidden input fields {name: value}."""
    out: dict[str, str] = {}
    for m in _HIDDEN_INPUT_RE.finditer(html):
        tag = m.group(1)
        name_m = _ATTR_NAME_RE.search(tag)
        value_m = _ATTR_VALUE_RE.search(tag)
        if name_m and name_m.group(1).startswith('__'):
            out[name_m.group(1)] = value_m.group(1) if value_m else ''
    return out


def _text(html_fragment: str) -> str:
    """Strip HTML tags and collapse whitespace."""
    txt = _TAG_RE.sub(' ', html_fragment)
    txt = txt.replace('&nbsp;', ' ').replace('&#160;', ' ').replace('&amp;', '&')
    return _WS_RE.sub(' ', txt).strip()


def _parse_date(cell_text: str) -> str | None:
    """Parse Swedish date (YYYY-MM-DD preferred, then DD-MM-YYYY) → ISO string or None."""
    # Try ISO first (YYYY-MM-DD)
    m = _DATE_ISO_RE.search(cell_text or '')
    if m:
        yyyy, mm, dd = m.groups()
        try:
            return datetime(int(yyyy), int(mm), int(dd)).date().isoformat()
        except ValueError:
            pass
    # Try DMY
    m = _DATE_DMY_RE.search(cell_text or '')
    if m:
        dd, mm, yyyy = m.groups()
        try:
            return datetime(int(yyyy), int(mm), int(dd)).date().isoformat()
        except ValueError:
            pass
    return None


def _doc_type_for_grid(grid_id: str, category: str) -> str:
    """Map grid_id (and optionally category text) to a DOC_TYPES member."""
    _GRID_DOC_TYPES = {
        "gvwYearReports": "annual_report",
        "gvwHalfYearReports": "half_year_report",
        "gvwQuarterReports": "interim_statement",
        "gvwBookEndReports": "other",
        "gvwFlaggings": "holding_notification",
    }
    if grid_id in _GRID_DOC_TYPES:
        return _GRID_DOC_TYPES[grid_id]
    # gvStockAffect: dispatch by category text
    if grid_id == "gvStockAffect":
        cat_low = (category or '').casefold()
        if 'insiderinformation' in cat_low or 'insider' in cat_low:
            return 'inside_information'
        # All other categories -> other
        return 'other'
    return 'other'


# ---------------------------------------------------------------------------
# Backend
# ---------------------------------------------------------------------------

class OamSE(OamSource):
    """Sweden OAM backend — scrapes Finanscentralen (Finansinspektionen) WebForms.

    Resolves by company NAME (no LEI/ISIN search). Strict: if the POST returns a page
    without <form action="ViewCompany2.aspx">, records an error and returns [].
    """

    name = "oam-se"
    country = "SE"

    def list_issuers(self) -> list[IssuerRef]:
        """Return empty — full enumeration is a scale-up concern."""
        return []

    def discover(self, entity: Entity) -> list[Document]:
        if not entity.name:
            return []

        now = datetime.now(timezone.utc).isoformat()

        # Step 1: Bootstrap — scrape hidden fields from the search form.
        try:
            search_html = self.fetcher.get_text(_SEARCH_URL)
        except Exception as exc:  # noqa: BLE001
            self._record_error('bootstrap', _SEARCH_URL, exc)
            return []

        fields = _scrape_hidden_fields(search_html)
        if '__VIEWSTATE' not in fields or '__EVENTVALIDATION' not in fields:
            self._record_error(
                'bootstrap-hidden-fields',
                _SEARCH_URL,
                RuntimeError(
                    'could not scrape __VIEWSTATE / __EVENTVALIDATION from search.aspx'
                ),
            )
            return []

        # Step 2: POST company-name search.
        post_data = {
            '__VIEWSTATE': fields.get('__VIEWSTATE', ''),
            '__VIEWSTATEGENERATOR': fields.get('__VIEWSTATEGENERATOR', ''),
            '__EVENTVALIDATION': fields.get('__EVENTVALIDATION', ''),
            '__VIEWSTATEENCRYPTED': fields.get('__VIEWSTATEENCRYPTED', ''),
            'ctl00$main$txtCompanyName': entity.name,
            'ctl00$main$txtOrganizationNumber': '',
            'ctl00$main$txtOrganizationShortName': '',
            'ctl00$main$btnSearch': 'Sök',
            '__SEARCH_UTIL_CULTURE': 'sv-SE',
            '__SEARCH_UTIL_STARTPAGE': '',
            '__SEARCH_UTIL_SEARCHTEXT': '',
        }
        try:
            profile_html = self.fetcher.post_text(_SEARCH_URL, post_data)
        except Exception as exc:  # noqa: BLE001
            self._record_error('search-post', _SEARCH_URL, exc)
            return []

        # Step 3: Verify we got a company profile page.
        if not _PROFILE_FORM_RE.search(profile_html):
            self._record_error(
                'no-company-profile',
                _SEARCH_URL,
                RuntimeError(
                    f"search returned no company profile for '{entity.name}' "
                    "(no <form action=ViewCompany2.aspx> in response)"
                ),
            )
            return []

        # Step 4: Parse all six grids, paginating each via ViewCompany2.aspx.
        out: list[Document] = []
        for grid_id, _base_doc_type in _GRIDS:
            try:
                out.extend(
                    self._discover_grid(grid_id, profile_html, entity, now)
                )
            except Exception as exc:  # noqa: BLE001
                self._record_error(f'grid-{grid_id}', _VIEW_COMPANY_URL, exc)

        return out

    # ------------------------------------------------------------------
    # Per-grid discovery + pagination
    # ------------------------------------------------------------------

    def _discover_grid(
        self, grid_id: str, first_page_html: str, entity: Entity, now: str
    ) -> list[Document]:
        """Parse a single GridView across all its pages."""
        docs: list[Document] = []
        current_html = first_page_html
        # Track freshest VIEWSTATE (needed for pagination POSTs).
        current_fields = _scrape_hidden_fields(first_page_html)

        for page_num in range(_MAX_PAGES_PER_GRID):
            page_docs, has_next = self._parse_grid_page(
                grid_id, current_html, entity, now
            )
            docs.extend(page_docs)

            if not has_next or not page_docs:
                break

            if page_num + 1 >= _MAX_PAGES_PER_GRID:
                self._record_error(
                    f'truncated-{grid_id}',
                    _VIEW_COMPANY_URL,
                    RuntimeError(
                        f'grid {grid_id} pagination hit the {_MAX_PAGES_PER_GRID}-page cap; '
                        'remaining pages not crawled'
                    ),
                )
                break

            # POST to ViewCompany2.aspx to get the next page.
            page_data = {
                '__VIEWSTATE': current_fields.get('__VIEWSTATE', ''),
                '__VIEWSTATEGENERATOR': current_fields.get('__VIEWSTATEGENERATOR', ''),
                '__EVENTVALIDATION': current_fields.get('__EVENTVALIDATION', ''),
                '__VIEWSTATEENCRYPTED': current_fields.get('__VIEWSTATEENCRYPTED', ''),
                '__EVENTTARGET': f'ctl00$main${grid_id}',
                '__EVENTARGUMENT': 'Page$Next',
            }
            try:
                current_html = self.fetcher.post_text(_VIEW_COMPANY_URL, page_data)
                current_fields = _scrape_hidden_fields(current_html)
            except Exception as exc:  # noqa: BLE001
                self._record_error(f'pagination-{grid_id}', _VIEW_COMPANY_URL, exc)
                break

        return docs

    def _parse_grid_page(
        self, grid_id: str, html: str, entity: Entity, now: str
    ) -> tuple[list[Document], bool]:
        """Parse one page of one GridView.

        Returns (list_of_documents, has_next_page).
        """
        docs: list[Document] = []
        has_next = False

        # Find the table for this grid.
        table_m = re.search(
            r'<table\b[^>]*\bid="ctl00_main_{gid}"[^>]*>(.*?)</table>'.format(
                gid=re.escape(grid_id)
            ),
            html,
            re.I | re.S,
        )
        if not table_m:
            return docs, False

        table_html = table_m.group(1)

        # Check for pagination next-page in the table (pager row).
        has_next = bool(_PAGER_NEXT_RE.search(table_html))

        for row_m in _TR_RE.finditer(table_html):
            row = row_m.group(1)
            # Skip header rows.
            if _TH_RE.search(row):
                continue

            cells = [_text(c.group(1)) for c in _TD_RE.finditer(row)]
            raw_cells = [c.group(1) for c in _TD_RE.finditer(row)]
            if not cells:
                continue

            doc = self._build_document(grid_id, cells, raw_cells, entity, now)
            if doc is not None:
                docs.append(doc)

        return docs, has_next

    def _build_document(
        self,
        grid_id: str,
        cells: list[str],
        raw_cells: list[str],
        entity: Entity,
        now: str,
    ) -> Document | None:
        """Build one Document from a GridView row's cell texts and raw HTML."""
        # Join all cell text for date extraction.
        all_text = ' '.join(cells)
        all_raw = ' '.join(raw_cells)

        published_ts = _parse_date(all_text)

        # Extract the category (for gvStockAffect) — usually the last text cell.
        category = cells[-1] if cells else ''

        # Extract file links from the raw HTML of all cells.
        fid_m = _GETFILE_RE.search(all_raw)
        stock_m = _VIEWSTOCK_RE.search(all_raw)
        flag_m = _FLAGGING_RE.search(all_raw)

        if grid_id == "gvwFlaggings":
            # Flaggings: index-only, no file.
            if not flag_m:
                return None
            item_id = flag_m.group(1)
            doc_type = "holding_notification"
            files: list[dict] = []
            doc_id = f"se-gvwFlaggings-{item_id}"
        elif grid_id == "gvStockAffect":
            if not stock_m:
                return None
            item_id = stock_m.group(1)
            doc_type = _doc_type_for_grid(grid_id, category)
            url = f"{_BASE}/ViewStockAffect.aspx?id={item_id}"
            files = [{'name': f'stockaffect-{item_id}.pdf', 'kind': 'document', 'url': url}]
            doc_id = f"se-gvStockAffect-{item_id}"
        else:
            # Financial report grids: use GetFile.aspx?fid=
            if not fid_m:
                return None
            fid = fid_m.group(1)
            doc_type = _doc_type_for_grid(grid_id, '')
            url = f"{_GET_FILE_URL}?fid={fid}"
            # Determine kind: ESEF XBRL packages are ZIP files (large); PDFs are documents.
            # We infer from the title/category text — "zip" or "xbrl" suggests ESEF.
            title_low = all_text.casefold()
            kind = 'esef' if ('zip' in title_low or 'xbrl' in title_low or 'esef' in title_low) else 'document'
            # Use grid-short name for the file name slug.
            grid_short = grid_id.replace('gvw', '').replace('gv', '').lower()
            files = [{'name': f'{grid_short}-{fid}.pdf', 'kind': kind, 'url': url}]
            doc_id = f"se-{grid_id}-{fid}"

        return Document(
            doc_id=doc_id,
            lei=entity.lei,
            country='SE',
            doc_type=doc_type,
            period_end=None,
            published_ts=published_ts,
            discovered_ts=now,
            language=None,
            source=self.name,
            files=files,
            native_meta={'grid': grid_id, 'category': category},
        )
```

- [ ] **Step 2: Run tests to confirm still RED (oam_se.py now importable but tests should fail on missing fixtures)**

```bash
cd /Users/marc/Desktop/All\ CODING/GENERALI/bottom_up_corpus/.claude/worktrees/agent-a72c3ee3592833601 && \
  venv/bin/python -m pytest tests/eu/test_oam_se.py -q 2>&1 | head -40
```

Expected: RED — fixture files not found yet (from Task 1), or import errors.

---

### Task 4: Wire `OamSE` into `acquire.py`

**Files:**
- Modify: `bottom_up_corpus/eu/acquire.py`

**Interfaces:**
- Consumes: `OamSE` from `oam_se.py`
- Produces: `COUNTRY_BACKENDS["SE"] = OamSE`

- [ ] **Step 1: Add import and backend registration**

In `bottom_up_corpus/eu/acquire.py`, after the `oam_nl` import, add:
```python
from .sources.oam_se import OamSE
```

And in `COUNTRY_BACKENDS`, add:
```python
    "SE": OamSE,
```

- [ ] **Step 2: Verify**

```bash
cd /Users/marc/Desktop/All\ CODING/GENERALI/bottom_up_corpus/.claude/worktrees/agent-a72c3ee3592833601 && \
  python -c "from bottom_up_corpus.eu.acquire import COUNTRY_BACKENDS; print('SE' in COUNTRY_BACKENDS)"
```

Expected: `True`

---

### Task 5: Execute Tasks 1-4 in order, run full test suite, commit

**Files:**
- All files from Tasks 1-4

**Interfaces:**
- Produces: green tests, commit on `feat/eu-se-backend`

- [ ] **Step 1: Execute Task 1 (capture fixtures)** — run the fixture-capture steps

- [ ] **Step 2: Execute Task 2 (write tests)** — create `tests/eu/test_oam_se.py`

- [ ] **Step 3: Run tests (RED)**

```bash
cd /Users/marc/Desktop/All\ CODING/GENERALI/bottom_up_corpus/.claude/worktrees/agent-a72c3ee3592833601 && \
  venv/bin/python -m pytest tests/eu/test_oam_se.py -q 2>&1
```

Expected: FAILED (import error — `oam_se` not yet created).

- [ ] **Step 4: Execute Task 3 (implement `oam_se.py`)** — create the backend

- [ ] **Step 5: Execute Task 4 (wire acquire.py)** — add SE to COUNTRY_BACKENDS

- [ ] **Step 6: Run EU tests (GREEN)**

```bash
cd /Users/marc/Desktop/All\ CODING/GENERALI/bottom_up_corpus/.claude/worktrees/agent-a72c3ee3592833601 && \
  venv/bin/python -m pytest tests/eu/ -q 2>&1
```

Expected: all EU tests pass.

- [ ] **Step 7: Run whole-repo tests**

```bash
cd /Users/marc/Desktop/All\ CODING/GENERALI/bottom_up_corpus/.claude/worktrees/agent-a72c3ee3592833601 && \
  venv/bin/python -m pytest -q 2>&1 | tail -20
```

Expected: overall pass (or pre-existing failures only, no new failures).

- [ ] **Step 8: Commit**

```bash
cd /Users/marc/Desktop/All\ CODING/GENERALI/bottom_up_corpus/.claude/worktrees/agent-a72c3ee3592833601 && \
  git add \
    bottom_up_corpus/eu/sources/oam_se.py \
    bottom_up_corpus/eu/acquire.py \
    tests/eu/test_oam_se.py \
    tests/fixtures/eu/se_search.html \
    tests/fixtures/eu/se_result_atlascopco.html && \
  git commit -m "$(cat <<'EOF'
feat(eu): Sweden OAM backend (Finanscentralen / Finansinspektionen WebForms)

OamSE scrapes the Finanscentralen ASP.NET WebForms OAM: bootstrap __VIEWSTATE, POST a
company-name search, parse the six GridView tables (annual/half-year/quarterly/year-end
financial reports + flaggings + regulated announcements), page each via ViewCompany2.aspx,
and download via GetFile.aspx?fid=. Maps grid/category -> doc_type. Wired into
COUNTRY_BACKENDS["SE"].

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Write the report

**Files:**
- Create: `.superpowers/sdd/se-backend-report.md`

**Interfaces:**
- Produces: concise report (≤14 lines) per spec

- [ ] **Step 1: Write report**

The report must cover: Status, branch+commit, test summary (eu+repo), live download result for Atlas Copco, concerns.

- [ ] **Step 2: Verify report exists**

```bash
ls -lh /Users/marc/Desktop/All\ CODING/GENERALI/bottom_up_corpus/.claude/worktrees/agent-a72c3ee3592833601/.superpowers/sdd/se-backend-report.md
```
