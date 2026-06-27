"""Tests for the Belgium OAM backend (StoriBE / FSMA STORI JSON API).

All network-free: an injected stub ``http`` client routes ``post_json`` from the
captured ``be_stori_result_abinbev.json`` fixture and ``get_json`` from the
companies / document-type fixtures. The real backend impersonates Chrome via
curl_cffi (F5 WAF), but that layer is bypassed entirely here.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from bottom_up_corpus.eu.documents import DOC_TYPES
from bottom_up_corpus.eu.entities import Entity
from bottom_up_corpus.eu.sources.oam_be import (
    StoriBE,
    _BASE,
    _MAX_RESULTS,
    _doc_type,
)

FIX = Path(__file__).parent.parent / "fixtures" / "eu"

ABINBEV_ISIN = "BE0974293251"


# ---------------------------------------------------------------------------
# Stub http client (post_json / get_json)
# ---------------------------------------------------------------------------

class _StubHttp:
    """First /result POST returns the fixture; later pages return empty so
    pagination terminates. get_json serves the companies / document-type fixtures."""

    def __init__(self):
        self._result = json.loads((FIX / "be_stori_result_abinbev.json").read_text())
        self._companies = json.loads((FIX / "be_stori_companies.json").read_text())
        self._doctypes = json.loads((FIX / "be_stori_document_types.json").read_text())
        self.posts: list[dict] = []
        self.gets: list[str] = []

    def post_json(self, url, body, **_):
        self.posts.append({"url": url, "body": body})
        # First page only carries items; subsequent pages drain so paging stops.
        if len([p for p in self.posts if p["url"] == url]) == 1:
            return self._result
        return {"resultCount": self._result["resultCount"], "storiResultItems": []}

    def get_json(self, url, **_):
        self.gets.append(url)
        if "companies" in url:
            return self._companies
        if "document-type" in url:
            return self._doctypes
        return {}


# ---------------------------------------------------------------------------
# discover by ISIN
# ---------------------------------------------------------------------------

def test_discover_by_isin_parses_items():
    http = _StubHttp()
    src = StoriBE(http=http)
    ent = Entity(lei=None, name="AB INBEV", country="BE", isins=(ABINBEV_ISIN,))

    docs = src.discover(ent)

    assert docs, "expected at least one Document from the fixture"
    assert all(d.doc_type in DOC_TYPES for d in docs)
    assert all(d.source == "oam-be" for d in docs)
    assert all(d.country == "BE" for d in docs)
    assert all(d.files for d in docs), "every Document must carry at least one file"
    dl = f"{_BASE}/download?fileDataId="
    assert all(f["url"].startswith(dl) for d in docs for f in d.files)
    # A multi-file item exposes every language file (the first fixture item has 3).
    assert any(len(d.files) >= 3 for d in docs), "multi-file item must expose all files"


def test_post_body_filters_by_isin():
    http = _StubHttp()
    src = StoriBE(http=http)
    ent = Entity(lei=None, name="AB INBEV", country="BE", isins=(ABINBEV_ISIN,))

    src.discover(ent)

    assert http.posts, "expected at least one POST"
    body = http.posts[0]["body"]
    assert body.get("isinCode") == ABINBEV_ISIN
    assert body.get("startRowIndex") == 0
    assert "pageSize" in body
    # A dropped/misspelled sort key would silently re-order (and could mis-page) the query.
    assert body.get("sortDirection") == "Descending"


def test_multiple_isins_dedup_by_topic_id():
    """Two ISINs that both return the same requiredReportingTopicId yield one Document."""
    result = json.loads((FIX / "be_stori_result_abinbev.json").read_text())

    class _PerIsinStub:
        """Returns the SAME fixture for the first page of EACH ISIN (so both ISINs
        surface the same items), empty thereafter — exercising cross-ISIN dedup."""
        def __init__(self):
            self.posts = []

        def post_json(self, url, body, **_):
            self.posts.append({"url": url, "body": body})
            return result if body.get("startRowIndex", 0) == 0 else {
                "resultCount": result["resultCount"], "storiResultItems": []}

        def get_json(self, url, **_):
            return {}

    http = _PerIsinStub()
    src = StoriBE(http=http)
    docs = src.discover(Entity(lei=None, name="AB INBEV", country="BE",
                               isins=(ABINBEV_ISIN, "BE0003793107")))
    ids = [d.doc_id for d in docs]
    assert ids, "expected documents"
    assert len(ids) == len(set(ids)), "documents must be deduped across ISINs by topic id"
    # Each ISIN's first page returned the same items; dedup collapses them to one set.
    assert len(docs) == len({i["requiredReportingTopicId"] for i in result["storiResultItems"]})
    assert {p["body"].get("isinCode") for p in http.posts} == {ABINBEV_ISIN, "BE0003793107"}


def test_ensure_session_without_curl_cffi_records_error(monkeypatch):
    """The lazy curl_cffi import failing must be recorded (not crash) and yield no session."""
    import builtins
    real_import = builtins.__import__

    def _no_curl(name, *a, **k):
        if name.startswith("curl_cffi"):
            raise ImportError("curl_cffi not installed")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _no_curl)
    src = StoriBE()  # no injected http → forces the live lazy path
    session = src._ensure_session()
    assert session is None
    assert any(e["context"] == "dependency" for e in src.errors)


# ---------------------------------------------------------------------------
# doc_type mapping
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("topic,expected", [
    ("Rapport financier annuel", "annual_report"),
    ("Rapport financier semestriel", "half_year_report"),
    ("Information trimestrielle", "interim_statement"),
    ("Rapport financier trimestriel", "interim_statement"),
    ("Déclaration intermédiaire", "interim_statement"),
    ("Information privilégiée", "inside_information"),
    ("Convocation assemblée générale", "governance"),
    ("Procès-verbal assemblée générale", "governance"),
    ("Communiqué de notification de transparence", "holding_notification"),
    ("Communiqué de changement du dénominateur ou des seuils statutaires",
     "holding_notification"),
    ("Prospectus de base", "prospectus"),
    ("Communiqué de rachat d'actions propres", "other"),
])
def test_doc_type_mapping(topic, expected):
    assert _doc_type(topic) == expected, f"_doc_type({topic!r}) expected {expected!r}"


def test_doc_type_accent_insensitive():
    # Accent-stripped + lowercased input must still map.
    assert _doc_type("information privilegiee") == "inside_information"
    assert _doc_type("") == "other"
    assert _doc_type(None) == "other"


def test_every_doc_type_in_doc_types():
    for topic in [
        "Rapport financier annuel", "Rapport financier semestriel",
        "Information trimestrielle", "Information privilégiée",
        "Convocation assemblée générale", "Communiqué de notification de transparence",
        "Prospectus", "anything else entirely",
    ]:
        assert _doc_type(topic) in DOC_TYPES


# ---------------------------------------------------------------------------
# no identity
# ---------------------------------------------------------------------------

def test_no_identity_returns_empty():
    http = _StubHttp()
    src = StoriBE(http=http)
    ent = Entity(lei=None, name="NO SUCH ISSUER XYZ", country="BE", isins=())

    docs = src.discover(ent)

    assert docs == []
    assert not http.posts, "no /result POST when there is no resolvable identity"


# ---------------------------------------------------------------------------
# name -> companyId fallback
# ---------------------------------------------------------------------------

def test_name_fallback_resolves_companyid():
    http = _StubHttp()
    src = StoriBE(http=http)
    ent = Entity(lei=None, name="AB INBEV", country="BE", isins=())

    docs = src.discover(ent)

    assert docs, "name fallback should resolve AB INBEV -> companyId and search"
    assert any("companies" in g for g in http.gets), "companies list must be consulted"
    body = http.posts[0]["body"]
    assert body.get("companyId") == "a8fc724c-0fb2-4d6c-90b3-085137807825"


# ---------------------------------------------------------------------------
# pagination + truncation
# ---------------------------------------------------------------------------

def test_pagination_and_truncation():

    class _BigStub:
        def __init__(self):
            self.calls = 0

        def post_json(self, url, body, **_):
            self.calls += 1
            if self.calls == 1:
                return {
                    "resultCount": _MAX_RESULTS + 1,
                    "storiResultItems": [{
                        "requiredReportingTopicId": f"topic-{i}",
                        "companyName": "BIG CO",
                        "reportingTopicName": "Information privilégiée",
                        "datePublication": "2025-01-01T00:00:00",
                        "lei": "X",
                        "mainDocuments": [{
                            "fileDataId": f"fid-{i}", "language": "fr",
                            "originalFileName": f"doc-{i}.pdf", "fileType": "pdf",
                        }],
                        "attachments": [],
                        "isinCodes": [],
                    } for i in range(50)],
                }
            return {"resultCount": _MAX_RESULTS + 1, "storiResultItems": []}

        def get_json(self, url, **_):
            return {}

    src = StoriBE(http=_BigStub())
    src.discover(Entity(lei=None, name="Big", country="BE", isins=("BE0000000001",)))

    assert any(e["context"] == "truncated" for e in src.errors)


# ---------------------------------------------------------------------------
# misc
# ---------------------------------------------------------------------------

def test_list_issuers_returns_empty():
    assert StoriBE(http=_StubHttp()).list_issuers() == []


def test_constructs_with_injected_http():
    # With an injected http stub the backend never touches curl_cffi / the network.
    # (The real lazy-import error path is covered by
    # test_ensure_session_without_curl_cffi_records_error.)
    src = StoriBE(http=_StubHttp())
    assert src.name == "oam-be"
    assert src.country == "BE"
