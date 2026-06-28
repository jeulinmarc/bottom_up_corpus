"""Euronext backend — one site, many markets (NL/BE/FR/PT/NO/IE).

``live.euronext.com`` is a single platform fronting every Euronext market, so a
single backend covers them all: the per-issuer **notices** feed is keyed by
``(ISIN, MIC)`` and the MIC follows from the entity's country.

* **Feed:** ``GET /en/ajax/getNoticePublicData/<ISIN>-<MIC>`` → an HTML table of
  the issuer's exchange notices (corporate events: dividends, admissions, name
  changes, general-meeting notices…), each with a date, a type and — when an
  attachment exists — a ``notice-download`` PDF link.
* **Identity:** ISIN-keyed (no-guess). The entity's GLEIF ISINs drive the query;
  notices are de-duplicated by their Euronext notice id across ISINs.

This is a **complement** to the national OAMs (AFM, FSMA, AMF, Oslo Børs…), which
are more complete for financial reports / ad-hoc: Euronext adds the exchange's
corporate-event notices.  It is the primary EU source only where no national
backend exists (Portugal; Ireland once its route is wired).  The acquisition
dispatcher lists it *after* the national backend, so any genuine overlap keeps
the national document (first-wins dedup in ``merge_documents``).
"""
from __future__ import annotations

import html as _html
import re
from datetime import datetime, timezone

from ..documents import Document
from ..entities import Entity
from ..oam_base import IssuerRef, OamSource

# ---------------------------------------------------------------------------
# Country -> Euronext market MIC
# ---------------------------------------------------------------------------

# Ireland (XMSM) currently returns an empty notices feed — Euronext Dublin's
# regulated information is published through a separate route. It is kept here so
# the backend is wired for it; it simply yields no documents until that route is
# found (surfaced as no-documents, never a silent gap).
EURONEXT_MICS: dict[str, str] = {
    "NL": "XAMS",
    "BE": "XBRU",
    "FR": "XPAR",
    "PT": "XLIS",
    "NO": "XOSL",
    "IE": "XMSM",
}

_FEED_URL = "https://live.euronext.com/en/ajax/getNoticePublicData/"
_DOWNLOAD_BASE = "https://live.euronext.com"
# The public GET feed returns at most the 50 most-recent notices and ignores
# page parameters (deeper history needs the Views-AJAX POST). An ISIN at the cap
# is recorded as truncated so the limit is never silent.
_PAGE_CAP = 50

# ---------------------------------------------------------------------------
# Row parsing
# ---------------------------------------------------------------------------

_ROW_RE = re.compile(r'<tr\b[^>]*\bclass="row_(\d+)\b[\s\S]*?</tr>', re.I)
_NOTICENUMBER_RE = re.compile(r'<td[^>]*\bclass="noticenumber"[^>]*>\s*([^<]+?)\s*</td>', re.I)
_NOTICEDATE_RE = re.compile(r'<td[^>]*\bclass="noticedate[^"]*"[^>]*>\s*([^<]+?)\s*</td>', re.I)
_NOTICENAME_TD_RE = re.compile(r'<td[^>]*\bclass="noticename[^"]*"[\s\S]*?</td>', re.I)
_DOWNLOAD_RE = re.compile(
    r'notice-download\?id=(\d+)[^"\']*?type=PDF[^"\']*?attachmentId=(\d+)', re.I
)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
# The notice-name / instruments cells embed a collapse button whose screen-reader
# label ("Toggle Visibility") would otherwise pollute the extracted text.
_SR_ONLY_RE = re.compile(r'<span\b[^>]*\bclass="sr-only"[^>]*>.*?</span>', re.I | re.S)

# ---------------------------------------------------------------------------
# doc_type mapping (Euronext notices are mostly corporate events)
# ---------------------------------------------------------------------------

_TYPE_RULES: list[tuple[str, str]] = [
    ("annual financial report", "annual_report"),
    ("annual report", "annual_report"),
    ("half-year", "half_year_report"),
    ("half year", "half_year_report"),
    ("interim", "interim_statement"),
    ("prospectus", "prospectus"),
    ("general meeting", "governance"),
    ("meeting of", "governance"),
    ("change of issuer", "governance"),
    ("change of product name", "governance"),
    ("voting rights", "holding_notification"),
]


def _doc_type(notice_name: str) -> str:
    """Map a Euronext notice type to a ``DOC_TYPES`` member (default ``other``)."""
    t = (notice_name or "").lower()
    for keyword, mapped in _TYPE_RULES:
        if keyword in t:
            return mapped
    return "other"


def _published_ts(text: str) -> str | None:
    """Convert a Euronext notice date (``23 Apr 2026``) to an ISO-8601 string."""
    try:
        d = datetime.strptime(text.strip(), "%d %b %Y").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None
    return d.isoformat()


def _cell_text(html_fragment: str) -> str:
    """Strip tags (and the collapse button's sr-only label) and collapse whitespace."""
    cleaned = _SR_ONLY_RE.sub(" ", html_fragment)
    return _WS_RE.sub(" ", _TAG_RE.sub(" ", cleaned)).strip()


class EuronextSource(OamSource):
    """Euronext exchange-notices backend, shared across all Euronext markets.

    The market MIC is derived from the entity's country via :data:`EURONEXT_MICS`;
    an entity in a non-Euronext country yields nothing.
    """

    name = "euronext"
    country = "EU"  # multi-market; each Document carries the entity's own country.

    def list_issuers(self) -> list[IssuerRef]:
        """Return empty — full enumeration is a scale-up concern."""
        return []

    def discover(self, entity: Entity) -> list[Document]:
        """Return the issuer's Euronext notices for its market.

        Resolves the market MIC from the entity's country, queries the notices
        feed for each of the entity's ISINs and de-duplicates by notice id. An
        entity outside the Euronext markets returns ``[]`` (no error: the backend
        simply does not apply there).
        """
        mic = EURONEXT_MICS.get(entity.country)
        if not mic:
            return []
        isins = [i for i in (entity.isins or ()) if i]
        if not isins:
            self._record_error(
                "no-isin",
                _FEED_URL,
                RuntimeError(
                    f"entity {entity.name!r} has no ISIN; the Euronext notices feed "
                    "is ISIN-keyed and cannot be queried without one"
                ),
            )
            return []

        now = datetime.now(timezone.utc).isoformat()
        seen: set[str] = set()
        docs: list[Document] = []
        for isin in isins:
            url = f"{_FEED_URL}{isin}-{mic}"
            try:
                html = self.fetcher.get_text(url)
            except Exception as exc:  # noqa: BLE001
                self._record_error("notices", url, exc)
                continue
            rows = list(self._rows(html))
            if len(rows) >= _PAGE_CAP:
                self._record_error(
                    "truncated",
                    url,
                    RuntimeError(
                        f"{isin}-{mic}: returned the {_PAGE_CAP}-notice cap; "
                        "older notices not fetched"
                    ),
                )
            for notice_id, row in rows:
                if notice_id in seen:
                    continue
                seen.add(notice_id)
                docs.append(self._to_document(notice_id, row, entity, mic, now))
        return docs

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _rows(html: str):
        """Yield ``(notice_id, row_html)`` for each notice row in the feed."""
        for m in _ROW_RE.finditer(html):
            yield m.group(1), m.group(0)

    def _to_document(
        self, notice_id: str, row: str, entity: Entity, mic: str, now: str
    ) -> Document:
        """Build a :class:`Document` from one notice row.

        A row with a PDF attachment gets a downloadable file; one without is kept
        as an index-only record (its metadata, no file) — never silently dropped.
        """
        row_u = _html.unescape(row)
        num_m = _NOTICENUMBER_RE.search(row)
        date_m = _NOTICEDATE_RE.search(row)
        name_td = _NOTICENAME_TD_RE.search(row)
        notice_name = _cell_text(name_td.group(0)) if name_td else ""
        notice_number = num_m.group(1).strip() if num_m else notice_id

        files: list[dict] = []
        dl = _DOWNLOAD_RE.search(row_u)
        if dl:
            did, aid = dl.groups()
            files.append({
                "name": f"euronext-{notice_number}.pdf",
                "kind": "document",
                "url": f"{_DOWNLOAD_BASE}/en/listview/notice-download"
                       f"?id={did}&type=PDF&attachmentId={aid}",
            })

        return Document(
            doc_id=f"euronext-{notice_id}",
            lei=entity.lei,
            country=entity.country,
            doc_type=_doc_type(notice_name),
            period_end=None,
            published_ts=_published_ts(date_m.group(1)) if date_m else None,
            discovered_ts=now,
            language="en",
            source=self.name,
            files=files,
            native_meta={
                "title": notice_name,
                "notice_number": notice_number,
                "mic": mic,
                "has_attachment": bool(files),
            },
        )
