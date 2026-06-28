"""Tests for the Euronext backend (EuronextSource — cross-market notices feed).

Network-free: a stub Fetcher serves the captured notices fixture keyed by the
``<ISIN>-<MIC>`` path segment, exactly as the live per-issuer feed is keyed.
"""
from __future__ import annotations

from pathlib import Path

from bottom_up_corpus.eu.documents import DOC_TYPES
from bottom_up_corpus.eu.entities import Entity
from bottom_up_corpus.eu.sources.oam_euronext import (
    EURONEXT_MICS,
    EuronextSource,
    _doc_type,
    _published_ts,
)

FIX = Path(__file__).parent.parent / "fixtures" / "eu"
_NOTICES = (FIX / "euronext_notices_edp.html").read_text()

_EDP_ISIN = "PTEDP0AM0009"
EDP = Entity(lei="LEDP", name="EDP", country="PT", isins=(_EDP_ISIN,))

_EMPTY = '<div class="card"><h3>NOTICES</h3></div>'


class _StubFetcher:
    """Serves the notices fixture for the EDP/XLIS path, empty otherwise."""

    def __init__(self, *, notices=None):
        self._notices = notices if notices is not None else _NOTICES
        self.urls: list[str] = []

    def get_text(self, url: str, **_):
        self.urls.append(url)
        if f"{_EDP_ISIN}-XLIS" in url:
            return self._notices
        return _EMPTY


# ---------------------------------------------------------------------------
# Pure-logic tests
# ---------------------------------------------------------------------------


def test_doc_type_mapping():
    assert _doc_type("CE - Shares - Dividend - Announcement") == "other"
    assert _doc_type("Notice of General Meeting") == "governance"
    assert _doc_type("CE - Change of issuer/product name") == "governance"
    assert _doc_type("Annual Financial Report 2025") == "annual_report"
    assert _doc_type("Prospectus approved") == "prospectus"


def test_published_ts():
    assert _published_ts("23 Apr 2026") == "2026-04-23T00:00:00+00:00"
    assert _published_ts("") is None
    assert _published_ts("not a date") is None


def test_country_to_mic_map_covers_six_markets():
    assert set(EURONEXT_MICS) == {"NL", "BE", "FR", "PT", "NO", "IE"}
    assert EURONEXT_MICS["PT"] == "XLIS" and EURONEXT_MICS["IE"] == "XMSM"


# ---------------------------------------------------------------------------
# discover()
# ---------------------------------------------------------------------------


def test_discover_parses_notices():
    src = EuronextSource(fetcher=_StubFetcher())
    docs = src.discover(EDP)
    assert len(docs) == 3
    assert all(d.country == "PT" and d.source == "euronext" for d in docs)
    assert all(d.doc_type in DOC_TYPES for d in docs)
    assert {d.doc_type for d in docs} == {"other", "governance"}
    assert not src.errors


def test_attachment_becomes_a_file_else_index_only():
    docs = EuronextSource(fetcher=_StubFetcher()).discover(EDP)
    by_num = {d.native_meta["notice_number"]: d for d in docs}
    # Dividend notice carries a PDF download.
    div = by_num["LIS_20260423_00070_EUR"]
    # The title is clean (the collapse button's "Toggle Visibility" label stripped).
    assert div.native_meta["title"] == "CE - Shares - Dividend - Announcement"
    assert div.files and div.files[0]["kind"] == "document"
    assert div.files[0]["url"].endswith("id=1544726&type=PDF&attachmentId=426888")
    assert div.published_ts == "2026-04-23T00:00:00+00:00"
    # Name-change notice has no attachment -> index-only (recorded, no file).
    name = by_num["LIS_20250312_00041_EUR"]
    assert name.files == [] and name.native_meta["has_attachment"] is False


def test_mic_resolved_from_country_and_query_path():
    f = _StubFetcher()
    EuronextSource(fetcher=f).discover(EDP)
    assert f.urls == [f"https://live.euronext.com/en/ajax/getNoticePublicData/{_EDP_ISIN}-XLIS"]


def test_non_euronext_country_yields_nothing():
    src = EuronextSource(fetcher=_StubFetcher())
    docs = src.discover(Entity(lei="L", name="X", country="DE", isins=("DE000A1EWWW0",)))
    assert docs == [] and not src.errors  # backend simply does not apply


def test_no_isin_records_error():
    src = EuronextSource(fetcher=_StubFetcher())
    docs = src.discover(Entity(lei="L", name="No ISIN", country="PT", isins=()))
    assert docs == []
    assert any(e["context"] == "no-isin" for e in src.errors)


def test_dedup_by_notice_id_across_isins():
    """Two ISINs returning overlapping notices collapse by notice id."""
    class _TwoIsinStub(_StubFetcher):
        def get_text(self, url, **_):
            self.urls.append(url)
            # Both ISINs map to the same notices page.
            return self._notices

    e = Entity(lei="L", name="EDP", country="PT", isins=(_EDP_ISIN, "PTEDP0AM0011"))
    src = EuronextSource(fetcher=_TwoIsinStub())
    docs = src.discover(e)
    assert len(docs) == 3  # not 6 — deduped by notice id


def test_truncation_recorded_at_cap():
    """An ISIN returning the 50-notice cap records a truncation error."""
    rows = "".join(
        f'<tr class="row_{i} "><td class="noticenumber">N{i}</td>'
        f'<td class="noticedate">01 Jan 2026</td>'
        f'<td class="noticename">CE - X</td></tr>'
        for i in range(50)
    )
    big = f'<table><tbody>{rows}</tbody></table>'
    src = EuronextSource(fetcher=_StubFetcher(notices=big))
    docs = src.discover(EDP)
    assert len(docs) == 50
    assert any(e["context"] == "truncated" for e in src.errors)


def test_euronext_wired_as_complement_after_national():
    """acquire appends Euronext for its markets, after the national backend."""
    import inspect
    import bottom_up_corpus.eu.acquire as acq
    src = inspect.getsource(acq.acquire)
    assert "EURONEXT_MICS" in src and "EuronextSource" in src
