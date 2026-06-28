from __future__ import annotations
import re
from dataclasses import dataclass, field
from datetime import date

# native_meta keys that hold a genuine document title (not the issuer/file name).
_TITLE_KEYS = ("title", "headline", "documentTitle")
_WS_RE = re.compile(r"\s+")

DOC_TYPES = ("annual_report", "half_year_report", "interim_statement",
             "inside_information", "holding_notification", "prospectus",
             "governance", "other")

DOC_FAMILY = {"annual_report": "ESEF-AR", "half_year_report": "HY",
              "interim_statement": "IMS", "inside_information": "MAR",
              "holding_notification": "TVR", "prospectus": "PROSPECTUS",
              "governance": "GOV", "other": "OTHER"}


@dataclass
class Document:
    doc_id: str
    lei: str | None
    country: str
    doc_type: str
    period_end: date | None
    published_ts: str | None
    discovered_ts: str
    language: str | None
    source: str
    files: list[dict] = field(default_factory=list)
    native_meta: dict = field(default_factory=dict)

    def key(self) -> tuple:
        hashes = tuple(sorted(f.get("sha256") or f.get("name", "") for f in self.files))
        return (self.lei, self.doc_type, self.period_end, hashes)

    def content_key(self) -> tuple | None:
        """Cross-backend identity of one announcement: ``(lei, day, title)``.

        Collapses the same disclosure reported by two backends even when their
        file names differ (the file-name :meth:`key` cannot). Returns ``None``
        when there is no usable title or no publication day, so title-less
        documents are NEVER merged this way — they fall back to :meth:`key`,
        which is conservative (no silent loss of distinct documents).
        """
        title = ""
        for k in _TITLE_KEYS:
            v = self.native_meta.get(k)
            if v:
                title = str(v)
                break
        title = _WS_RE.sub(" ", title.strip().lower())
        if not title or not self.published_ts:
            return None
        return (self.lei, self.published_ts[:10], title)
