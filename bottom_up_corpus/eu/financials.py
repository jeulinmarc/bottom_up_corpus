"""EU Pillar B producer — structured IFRS financials from ESEF (filings.xbrl.org).

filings.xbrl.org exposes each ESEF filing's facts as OIM xBRL-JSON (``json_url``):
the "European companyfacts". We union an issuer's filings into one ``flat`` dict and
run the shared engine with the IFRS concept pack, writing the SEC-unified schema.
"""
from __future__ import annotations

from .entities import Entity
from .oim import flatten_oim_json
from .sources.filings_org import FilingsXbrlOrg


def facts_for_entity(entity: Entity, *, fetcher) -> dict[str, list[dict]]:
    """Union the OIM-JSON facts across all of the entity's filings.xbrl.org filings.

    Each annual report carries the current + prior-year comparative; the union yields
    a multi-year series, and the engine's latest-filed rule resolves restatements.
    """
    flat: dict[str, list[dict]] = {}
    if not entity.lei:
        return flat
    src = FilingsXbrlOrg(fetcher=fetcher)
    for doc in src.discover(entity):
        meta = doc.native_meta or {}
        jf = next((f for f in doc.files if f.get("kind") == "json_url" and f.get("url")), None)
        if not jf:
            continue
        try:
            report = fetcher.get_json(jf["url"])
        except Exception:        # noqa: BLE001 — a bad/absent report is skipped, never fatal
            continue
        part = flatten_oim_json(
            report,
            filed=str(meta.get("date_added") or doc.published_ts or ""),
            form=doc.doc_type,
            accn=str(meta.get("fxo_id") or doc.doc_id),
        )
        for tag, pts in part.items():
            flat.setdefault(tag, []).extend(pts)
    return flat
