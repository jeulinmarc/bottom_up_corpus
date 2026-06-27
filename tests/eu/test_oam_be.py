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
        # Peek requests (pageSize=1) return the fixture's first item so the
        # companyNumber is readable; they don't count as "the real first page".
        if body.get("pageSize") == 1:
            item = self._result["storiResultItems"][0]
            return {"resultCount": self._result["resultCount"], "storiResultItems": [item]}
        # Full pages: first real page returns items; subsequent pages drain.
        full_pages = [p for p in self.posts if p["url"] == url and p["body"].get("pageSize") != 1]
        if len(full_pages) == 1:
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


# ---------------------------------------------------------------------------
# peek-companyNumber optimization
# ---------------------------------------------------------------------------

def _make_item(topic_id: str, company_number: str, file_id: str) -> dict:
    return {
        "requiredReportingTopicId": topic_id,
        "companyName": "TEST CO",
        "companyNumber": company_number,
        "nationality": "BE",
        "reportingTopicName": "Rapport financier annuel",
        "datePublication": "2025-01-01T00:00:00",
        "dateReceived": "2025-01-01T00:00:00",
        "lei": "TESTLEI",
        "mainDocuments": [{
            "fileDataId": file_id,
            "language": "fr",
            "originalFileName": f"{file_id}.pdf",
            "fileType": "pdf",
        }],
        "attachments": [],
        "isinCodes": [],
        "documentTitle": "Annual Report",
    }


class _PeekAwareStub:
    """Stub that tracks peek calls (pageSize=1) vs full-page calls separately.

    Each ISIN is configured with a company_number and a list of full-page items.
    The peek for any ISIN returns one item with the configured company_number.
    Full pages: first page returns the configured items, subsequent pages return empty.
    """

    def __init__(self, isin_config: dict[str, tuple[str, list[dict]]]):
        # isin_config: {isin: (company_number, [items])}
        self._config = isin_config
        self.peek_calls: list[dict] = []   # pageSize==1 bodies
        self.full_calls: list[dict] = []   # pageSize>1 bodies

    def post_json(self, url, body, **_):
        isin = body.get("isinCode")
        page_size = body.get("pageSize", 50)
        start = body.get("startRowIndex", 0)

        if page_size == 1:
            self.peek_calls.append(body)
            if isin not in self._config:
                return {"resultCount": 0, "storiResultItems": []}
            company_number, items = self._config[isin]
            if not items:
                return {"resultCount": 0, "storiResultItems": []}
            peek_item = dict(items[0])
            peek_item["companyNumber"] = company_number
            return {"resultCount": len(items), "storiResultItems": [peek_item]}
        else:
            self.full_calls.append(body)
            if isin not in self._config or start > 0:
                return {"resultCount": 0, "storiResultItems": []}
            company_number, items = self._config[isin]
            tagged = [dict(i) | {"companyNumber": company_number} for i in items]
            return {"resultCount": len(tagged), "storiResultItems": tagged}

    def get_json(self, url, **_):
        return {}


def test_peek_skips_redundant_company():
    """Two ISINs mapping to the same companyNumber: second ISIN is peeked but not
    fully paginated — only one full pagination is issued for that company."""
    company = "0417497106"
    item_a = _make_item("topic-A", company, "file-A")
    item_b = _make_item("topic-B", company, "file-B")  # same company, different topic

    isin1, isin2 = "BE0000000001", "BE0000000002"
    http = _PeekAwareStub({
        isin1: (company, [item_a]),
        isin2: (company, [item_b]),
    })
    src = StoriBE(http=http)
    docs = src.discover(Entity(lei=None, name="TEST CO", country="BE",
                                isins=(isin1, isin2)))

    # Both ISINs should have been peeked.
    peek_isins = {b["isinCode"] for b in http.peek_calls}
    assert peek_isins == {isin1, isin2}, "both ISINs must be peeked"

    # Only isin1 should have triggered a full pagination (isin2 shares the company).
    full_isins = {b["isinCode"] for b in http.full_calls}
    assert full_isins == {isin1}, "isin2 must be skipped (same companyNumber)"

    # Documents: only from isin1's full fetch; topic-B was never fetched (correct —
    # items for the same STORI company are already captured from isin1's full run).
    assert len(docs) == 1
    assert docs[0].doc_id == f"be-{item_a['requiredReportingTopicId']}"


def test_peek_distinct_company_is_fully_fetched():
    """Two ISINs mapping to DIFFERENT companyNumbers → both are fully paginated."""
    company_x, company_y = "0417497106", "0436180892"
    item_x = _make_item("topic-X", company_x, "file-X")
    item_y = _make_item("topic-Y", company_y, "file-Y")

    isin1, isin2 = "BE0000000001", "BE0000000002"
    http = _PeekAwareStub({
        isin1: (company_x, [item_x]),
        isin2: (company_y, [item_y]),
    })
    src = StoriBE(http=http)
    docs = src.discover(Entity(lei=None, name="TEST CO", country="BE",
                                isins=(isin1, isin2)))

    # Both ISINs peeked.
    assert {b["isinCode"] for b in http.peek_calls} == {isin1, isin2}
    # Both ISINs fully paginated (different companies).
    assert {b["isinCode"] for b in http.full_calls} == {isin1, isin2}
    # Documents from both companies.
    doc_ids = {d.doc_id for d in docs}
    assert f"be-{item_x['requiredReportingTopicId']}" in doc_ids
    assert f"be-{item_y['requiredReportingTopicId']}" in doc_ids


def test_peek_empty_isin_skipped():
    """An ISIN whose peek returns 0 items produces no docs and no error."""
    company = "0417497106"
    item_a = _make_item("topic-A", company, "file-A")

    isin1, isin2 = "BE0000000001", "BE9999999999"  # isin2 not in config → 0 items
    http = _PeekAwareStub({
        isin1: (company, [item_a]),
        # isin2 absent → peek returns resultCount=0
    })
    src = StoriBE(http=http)
    docs = src.discover(Entity(lei=None, name="TEST CO", country="BE",
                                isins=(isin1, isin2)))

    # isin2 peeked (resultCount=0) — must not produce a full pagination.
    assert any(b["isinCode"] == isin2 for b in http.peek_calls), "isin2 must be peeked"
    assert not any(b["isinCode"] == isin2 for b in http.full_calls), \
        "isin2 must not trigger full pagination"

    # No errors recorded for the empty ISIN.
    assert not src.errors, f"no errors expected, got: {src.errors}"

    # Docs from isin1 only.
    assert len(docs) == 1
    assert docs[0].doc_id == f"be-{item_a['requiredReportingTopicId']}"


def test_peek_error_falls_through_to_full_search():
    """A transient peek failure must not drop the ISIN — falls through to full search."""
    company = "0417497106"
    item_a = _make_item("topic-A", company, "file-A")

    class _PeekFailStub:
        def __init__(self):
            self.calls = 0

        def post_json(self, url, body, **_):
            self.calls += 1
            if body.get("pageSize") == 1:
                raise RuntimeError("transient WAF error")
            # Full page
            if body.get("startRowIndex", 0) == 0:
                return {"resultCount": 1, "storiResultItems": [
                    dict(item_a) | {"companyNumber": company}
                ]}
            return {"resultCount": 1, "storiResultItems": []}

        def get_json(self, url, **_):
            return {}

    src = StoriBE(http=_PeekFailStub())
    docs = src.discover(Entity(lei=None, name="TEST CO", country="BE",
                                isins=("BE0000000001",)))

    # Peek error is recorded.
    assert any(e["context"] == "peek" for e in src.errors)
    # Full search still runs and returns the doc.
    assert len(docs) == 1
    assert docs[0].doc_id == f"be-{item_a['requiredReportingTopicId']}"
