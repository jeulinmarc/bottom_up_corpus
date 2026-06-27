"""Tests for the Finland OAM backend (OamFI — oam.fi Nasdaq Helsinki scrape).

All network-free: a stub Fetcher routes get_text (oam.fi/ → bootstrap fixture;
/view/ → view fixture) and post_text (/ → search fixture first page, then a
no-rows page so pagination stops).

RED → GREEN discipline: tests were written before implementation was correct.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from bottom_up_corpus.eu.documents import DOC_TYPES
from bottom_up_corpus.eu.entities import Entity
from bottom_up_corpus.eu.sources.oam_fi import (
    OamFI,
    _doc_type,
    _normalise_name,
    _parse_published_ts,
)

FIX = Path(__file__).parent.parent / "fixtures" / "eu"
BOOTSTRAP_HTML = (FIX / "fi_bootstrap.html").read_text()
SEARCH_HTML = (FIX / "fi_search_nokia.html").read_text()
VIEW_HTML = (FIX / "fi_view.html").read_text()

# A minimal no-rows page that stops pagination
_EMPTY_PAGE = '<html><body><nef-pagination totalDataLength="6"></nef-pagination></body></html>'


# ---------------------------------------------------------------------------
# Stub Fetcher
# ---------------------------------------------------------------------------

class _StubFetcher:
    """Routes calls by URL substring.

    * get_text on 'oam.fi/' or ending '/' → bootstrap fixture
    * get_text on '/view/' → view fixture
    * post_text first call → search fixture; subsequent → empty page (stops pagination)
    """

    def __init__(
        self,
        *,
        bootstrap_html: str = BOOTSTRAP_HTML,
        search_html: str = SEARCH_HTML,
        view_html: str = VIEW_HTML,
        empty_page: str = _EMPTY_PAGE,
    ):
        self._bootstrap = bootstrap_html
        self._search = search_html
        self._view = view_html
        self._empty = empty_page
        self._post_call_count = 0
        self.get_calls: list[str] = []
        self.post_calls: list[tuple[str, dict]] = []

    def get_text(self, url: str, **_) -> str:
        self.get_calls.append(url)
        if "/view/" in url:
            return self._view
        if url.endswith("/") or "oam.fi/" in url:
            return self._bootstrap
        raise RuntimeError(f"Unexpected get_text url: {url!r}")

    def post_text(self, url: str, data, **_) -> str:
        self.post_calls.append((url, dict(data)))
        self._post_call_count += 1
        # First POST returns real results; subsequent return empty so pagination stops
        if self._post_call_count == 1:
            return self._search
        return self._empty


# ---------------------------------------------------------------------------
# Pure helper tests
# ---------------------------------------------------------------------------

def test_parse_published_ts_eest():
    assert _parse_published_ts("2026-06-09 18:30:00 EEST") == "2026-06-09"


def test_parse_published_ts_utc():
    assert _parse_published_ts("2025-03-15 10:00:00 EET") == "2025-03-15"


def test_parse_published_ts_none_on_garbage():
    assert _parse_published_ts("not a date") is None
    assert _parse_published_ts("") is None
    assert _parse_published_ts(None) is None


def test_normalise_name_strips_oyj():
    assert _normalise_name("Nokia Oyj") == "nokia"
    assert _normalise_name("KONE OYJ") == "kone"


def test_normalise_name_strips_oy():
    assert _normalise_name("Example Oy") == "example"


def test_normalise_name_strips_abp():
    assert _normalise_name("Sampo ABP") == "sampo"


def test_normalise_name_strips_plc():
    assert _normalise_name("Example PLC") == "example"


def test_normalise_name_strips_diacritics():
    # Finnish company names occasionally have ä/ö
    assert _normalise_name("Elisa Oyj") == "elisa"


def test_normalise_name_collapses_whitespace():
    assert _normalise_name("  Nokia   Oyj  ") == "nokia"


# ---------------------------------------------------------------------------
# doc_type mapping tests
# ---------------------------------------------------------------------------

def test_doc_type_annual():
    assert _doc_type("Annual Financial Report (ESEF)") == "annual_report"
    assert _doc_type("annual financial report") == "annual_report"


def test_doc_type_half_year():
    assert _doc_type("Half Year Financial Report") == "half_year_report"
    assert _doc_type("half year") == "half_year_report"


def test_doc_type_interim():
    assert _doc_type("Interim report") == "interim_statement"
    assert _doc_type("Interim report (Q1 and Q3)") == "interim_statement"
    assert _doc_type("Financial Statement Release") == "interim_statement"


def test_doc_type_inside_information():
    assert _doc_type("Inside information") == "inside_information"


def test_doc_type_holding_notification():
    assert _doc_type("Major shareholder notification") == "holding_notification"
    assert _doc_type("Managers' transactions") == "holding_notification"
    assert _doc_type("Total voting rights") == "holding_notification"
    assert _doc_type("Total number of voting rights and capital") == "holding_notification"


def test_doc_type_other():
    assert _doc_type("Unknown category XYZ") == "other"
    assert _doc_type("") == "other"


def test_doc_type_always_in_doc_types():
    for cat in [
        "Annual Financial Report (ESEF)", "Half Year Financial Report",
        "Interim report", "Financial Statement Release", "Inside information",
        "Major shareholder notification", "Managers' transactions",
        "Total voting rights", "Total number of voting rights and capital",
        "Something completely unknown",
    ]:
        assert _doc_type(cat) in DOC_TYPES, f"_doc_type({cat!r}) not in DOC_TYPES"


# ---------------------------------------------------------------------------
# Bootstrap tests
# ---------------------------------------------------------------------------

def test_bootstrap_parses_csrf():
    src = OamFI(fetcher=_StubFetcher())
    result = src._bootstrap()
    assert result is not None
    csrf, _, _ = result
    assert csrf and len(csrf) > 5, f"CSRF token too short or empty: {csrf!r}"


def test_bootstrap_parses_company_map():
    src = OamFI(fetcher=_StubFetcher())
    result = src._bootstrap()
    assert result is not None
    _, company_map, _ = result
    assert isinstance(company_map, dict)
    assert len(company_map) > 0, "company_map must not be empty"
    # Nokia must be present with id=690
    nokia_id = next(
        (cid for name, cid in company_map.items() if "nokia" in name.lower()),
        None
    )
    assert nokia_id == 690, f"Nokia id expected 690, got {nokia_id}"


def test_bootstrap_parses_categories():
    src = OamFI(fetcher=_StubFetcher())
    result = src._bootstrap()
    assert result is not None
    _, _, category_map = result
    assert isinstance(category_map, dict)


def test_bootstrap_is_cached():
    """A second call to _bootstrap must not hit the network again."""
    fetcher = _StubFetcher()
    src = OamFI(fetcher=fetcher)
    src._bootstrap()
    calls_after_first = len(fetcher.get_calls)
    src._bootstrap()
    assert len(fetcher.get_calls) == calls_after_first, "bootstrap cache not working"


def test_bootstrap_failure_returns_none():
    class _FailFetcher:
        def get_text(self, url, **_):
            raise ConnectionError("network down")
        def post_text(self, url, data, **_):
            raise ConnectionError("network down")

    src = OamFI(fetcher=_FailFetcher())
    assert src._bootstrap() is None
    assert any(e["context"] == "bootstrap" for e in src.errors)


# ---------------------------------------------------------------------------
# Name resolution tests
# ---------------------------------------------------------------------------

def test_resolve_nokia_by_name():
    """Nokia Oyj → company id 690."""
    src = OamFI(fetcher=_StubFetcher())
    result = src._bootstrap()
    assert result is not None
    _, company_map, _ = result
    cid = src._resolve_company_id(Entity(lei="L1", name="Nokia Oyj", country="FI"), company_map)
    assert cid == 690


def test_resolve_case_insensitive():
    src = OamFI(fetcher=_StubFetcher())
    result = src._bootstrap()
    assert result is not None
    _, company_map, _ = result
    cid = src._resolve_company_id(Entity(lei="L1", name="nokia oyj", country="FI"), company_map)
    assert cid == 690


def test_resolve_no_match_records_error():
    src = OamFI(fetcher=_StubFetcher())
    result = src._bootstrap()
    assert result is not None
    _, company_map, _ = result
    cid = src._resolve_company_id(
        Entity(lei="L1", name="NONEXISTENT CORP XYZ", country="FI"), company_map
    )
    assert cid is None
    assert any(e["context"] == "resolve-no-match" for e in src.errors)


def test_discover_no_match_returns_empty():
    """discover() for an unresolvable name must return [] and record an error."""
    src = OamFI(fetcher=_StubFetcher())
    docs = src.discover(Entity(lei="L1", name="NONEXISTENT CORP XYZ", country="FI"))
    assert docs == []
    assert any(e["context"] == "resolve-no-match" for e in src.errors)


def test_discover_empty_name_returns_empty():
    fetcher = _StubFetcher()
    src = OamFI(fetcher=fetcher)
    assert src.discover(Entity(lei="L1", name="", country="FI")) == []
    assert fetcher.post_calls == [], "no POST for empty name"


# ---------------------------------------------------------------------------
# discover() integration tests
# ---------------------------------------------------------------------------

def test_discover_nokia_yields_documents():
    src = OamFI(fetcher=_StubFetcher())
    docs = src.discover(Entity(lei="FI-LEI-001", name="Nokia Oyj", country="FI"))
    assert docs, "expected at least one Document for Nokia"


def test_discover_documents_have_correct_source_and_country():
    src = OamFI(fetcher=_StubFetcher())
    docs = src.discover(Entity(lei="FI-LEI-001", name="Nokia Oyj", country="FI"))
    assert all(d.source == "oam-fi" for d in docs)
    assert all(d.country == "FI" for d in docs)


def test_discover_documents_have_doc_type_in_doc_types():
    src = OamFI(fetcher=_StubFetcher())
    docs = src.discover(Entity(lei="FI-LEI-001", name="Nokia Oyj", country="FI"))
    assert all(d.doc_type in DOC_TYPES for d in docs)


def test_discover_documents_carry_lei():
    src = OamFI(fetcher=_StubFetcher())
    docs = src.discover(Entity(lei="FI-LEI-001", name="Nokia Oyj", country="FI"))
    assert all(d.lei == "FI-LEI-001" for d in docs)


def test_discover_doc_ids_prefixed_fi():
    src = OamFI(fetcher=_StubFetcher())
    docs = src.discover(Entity(lei="FI-LEI-001", name="Nokia Oyj", country="FI"))
    assert all(d.doc_id.startswith("fi-") for d in docs)


def test_discover_file_urls_contain_viewattachment():
    src = OamFI(fetcher=_StubFetcher())
    docs = src.discover(Entity(lei="FI-LEI-001", name="Nokia Oyj", country="FI"))
    for doc in docs:
        for f in doc.files:
            assert "viewAttachment.action?messageAttachmentId=" in f["url"], (
                f"file URL should contain viewAttachment.action: {f['url']!r}"
            )


def test_discover_file_kind_esef_for_zip():
    """Files whose names end .zip must have kind='esef'."""
    src = OamFI(fetcher=_StubFetcher())
    docs = src.discover(Entity(lei="FI-LEI-001", name="Nokia Oyj", country="FI"))
    for doc in docs:
        for f in doc.files:
            name = f.get("name", "")
            if name.lower().endswith(".zip") or name.lower().endswith(".xhtml"):
                assert f["kind"] == "esef", f"zip/xhtml file should be kind=esef: {f}"


# ---------------------------------------------------------------------------
# Pagination tests
# ---------------------------------------------------------------------------

def test_pagination_terminates_on_empty_page():
    """Pagination must stop when 0 rows returned or total consumed."""
    fetcher = _StubFetcher()
    src = OamFI(fetcher=fetcher)
    src.discover(Entity(lei="FI-LEI-001", name="Nokia Oyj", country="FI"))
    # After first POST (real results) + second POST (empty page), should stop
    assert fetcher._post_call_count <= 2, (
        f"Expected ≤2 POST calls, got {fetcher._post_call_count}"
    )


def test_pagination_cap_records_truncation():
    """When _MAX_PAGES is hit, a truncation error must be recorded."""
    from bottom_up_corpus.eu.sources.oam_fi import _MAX_PAGES

    call_count = 0

    class _InfiniteFetcher:
        def get_text(self, url, **_):
            if "/view/" in url:
                return VIEW_HTML
            return BOOTSTRAP_HTML

        def post_text(self, url, data, **_):
            nonlocal call_count
            call_count += 1
            # Always return a page with rows and large totalDataLength
            # so pagination never stops naturally
            if 'totalDataLength' in SEARCH_HTML:
                return SEARCH_HTML.replace(
                    'totalDataLength="2589"', 'totalDataLength="99999"'
                )
            return (
                f'<nef-pagination totalDataLength="99999"></nef-pagination>'
                + SEARCH_HTML
            )

    src = OamFI(fetcher=_InfiniteFetcher())
    # Patch _MAX_PAGES to 2 for speed (avoid 60 network calls in the test)
    import bottom_up_corpus.eu.sources.oam_fi as _mod
    original = _mod._MAX_PAGES
    _mod._MAX_PAGES = 2
    try:
        src.discover(Entity(lei="FI-LEI-001", name="Nokia Oyj", country="FI"))
    finally:
        _mod._MAX_PAGES = original

    assert any(e["context"] == "truncated" for e in src.errors), (
        f"expected truncated error; errors={src.errors}"
    )


# ---------------------------------------------------------------------------
# acquire.py wiring
# ---------------------------------------------------------------------------

def test_country_backends_includes_fi():
    from bottom_up_corpus.eu.acquire import COUNTRY_BACKENDS
    from bottom_up_corpus.eu.sources.oam_fi import OamFI as _OamFI
    assert "FI" in COUNTRY_BACKENDS
    assert COUNTRY_BACKENDS["FI"] is _OamFI
