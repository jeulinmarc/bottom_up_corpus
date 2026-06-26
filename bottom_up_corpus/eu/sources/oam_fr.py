"""info-financiere.gouv.fr (AMF) backend — Opendatasoft Explore v2.1 records API.

Recon-confirmed: dataset `flux-amf-new-prod` (~531k records). The v2.1 `/records`
response is `{"total_count": N, "results": [ <flat field dict>, ... ]}` (results
items ARE the field dicts; no record/fields nesting). Confirmed field names below.
"""
from __future__ import annotations

from datetime import datetime, timezone

from ..documents import Document
from ..entities import Entity
from ..oam_base import IssuerRef, OamSource

BASE = "https://www.info-financiere.gouv.fr/api/explore/v2.1/catalog/datasets"
DATASET = "flux-amf-new-prod"

# Recon-confirmed real fields: the download URL is `url_de_recuperation` (an
# ftp.opendatasoft.com PDF, HTTP 200), NOT a constructed path; the document type is
# `subtype_of_information` (specific) falling back to `type_of_information`.
# Map the English subtype/type labels -> our DOC_TYPES; extend from the data.
_TYPE_MAP = {
    "annual financial report": "annual_report",
    "half-yearly financial report": "half_year_report",
    "interim management statement": "interim_statement",
    "quarterly financial information": "interim_statement",
    "inside information": "inside_information",
    "total number of voting rights and capital": "holding_notification",
    "prospectus": "prospectus",
}


def _doc_type(subtype: str | None, typ: str | None) -> str:
    return _TYPE_MAP.get((subtype or "").strip().lower(),
                         _TYPE_MAP.get((typ or "").strip().lower(), "other"))


class InfoFinanciereFR(OamSource):
    name = "oam-fr"
    country = "FR"

    def list_issuers(self) -> list[IssuerRef]:
        return []  # full enumeration via ODS facets is a scale-up concern; not needed for the bounded test

    def discover(self, entity: Entity) -> list[Document]:
        from urllib.parse import quote
        if not entity.lei and not entity.isins:
            return []
        # Build OR-clause over LEI and each ISIN so pre-LEI-era records aren't silently dropped.
        clauses = []
        if entity.lei:
            clauses.append(f'identificationsociete_iso_cd_lei="{entity.lei}"')
        for isin in entity.isins:
            clauses.append(f'identificationsociete_iso_cd_isi="{isin}"')
        where = quote(" OR ".join(f"({c})" if len(clauses) > 1 else c for c in clauses))
        q = f"{BASE}/{DATASET}/records?where={where}&limit=100"
        try:
            resp = self.fetcher.get_json(q)
            results = resp.get("results") or []
            total_count = resp.get("total_count", len(results))
        except Exception as exc:  # noqa: BLE001
            self._record_error("discover", q, exc)
            return []
        if total_count > len(results):
            self._record_error("truncated", q,
                               f"{len(results)}/{total_count} records")
        now = datetime.now(timezone.utc).isoformat()
        out: list[Document] = []
        for f in results:
            url = f.get("url_de_recuperation")
            if not url:
                continue
            out.append(Document(
                doc_id=f"fr-{f.get('uin_idt_uin')}", lei=entity.lei, country="FR",
                doc_type=_doc_type(f.get("subtype_of_information"),
                                   f.get("type_of_information")),
                period_end=None,  # FR records are publication-dated, not period-keyed
                published_ts=f.get("informationdeposee_inf_dat_emt") or f.get("uin_dat_amf"),
                discovered_ts=now, language="fr", source=self.name,
                files=[{"name": url.rsplit("/", 1)[-1], "url": url, "kind": "document"}],
                native_meta=f))
        return out
