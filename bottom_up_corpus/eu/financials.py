"""EU Pillar B producer — structured IFRS financials from ESEF (filings.xbrl.org).

filings.xbrl.org exposes each ESEF filing's facts as OIM xBRL-JSON (``json_url``):
the "European companyfacts". We union an issuer's filings into one ``flat`` dict and
run the shared engine with the IFRS concept pack, writing the SEC-unified schema.
"""
from __future__ import annotations

import json

from ..config import Config
from ..financials import attach_ttm_from_flat, rows_from_base, summaries_from_flat
from ..storage import Storage
from .entities import Entity, resolve_entities
from .ifrs_concepts import IFRS_CONCEPTS, IFRS_CONCEPTS_BY_KEY
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
        if not report:           # a None/empty body is skipped, never fatal
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


def _eu_base(lei: str, summary) -> dict:
    """The SEC-unified identity + period columns, EU-mapped (cik->lei, no sic, etc.)."""
    return {
        "lei": lei, "fy": summary.fy, "frequency": summary.frequency,
        "currency": summary.currency, "is_financial": None,
        "period_end": summary.period_end.isoformat() if summary.period_end else None,
        "publication_date": summary.publication_date.isoformat() if summary.publication_date else None,
        "doc_type": summary.sec_form, "source": summary.accession,
    }


def build_eu_financials(specs, *, fetcher, config: Config, write: bool = True) -> dict:
    """Resolve specs -> IFRS financials -> data/financials_eu/<LEI>.jsonl (SEC schema).

    Coverage (with/without financials) is written to reports/eu_financials_coverage.jsonl;
    an unresolved or unindexed issuer is recorded there, never silently dropped.
    """
    entities = resolve_entities(specs, fetcher=fetcher)
    storage = Storage(config)
    coverage: list[dict] = []
    out = {"entities": 0, "with_financials": 0, "no_financials": 0, "periods": 0, "paths": []}
    for ent in entities:
        out["entities"] += 1
        if not ent.lei:
            coverage.append({"lei": None, "name": ent.name, "resolution": ent.resolution,
                             "status": "unresolved"})
            out["no_financials"] += 1
            continue
        flat = facts_for_entity(ent, fetcher=fetcher)
        summaries = summaries_from_flat(flat, concepts=IFRS_CONCEPTS, company=ent.name,
                                        company_current=ent.name, sic=None)
        attach_ttm_from_flat(flat, summaries, concepts_by_key=IFRS_CONCEPTS_BY_KEY)
        if not summaries:
            coverage.append({"lei": ent.lei, "name": ent.name, "status": "no-financials"})
            out["no_financials"] += 1
            continue
        rows: list[dict] = []
        for s in summaries:
            rows.extend(rows_from_base(_eu_base(ent.lei, s), s))
        out["periods"] += len(summaries)
        out["with_financials"] += 1
        if write:
            out["paths"].append(storage.write_eu_financials_table(ent.lei, rows))
        coverage.append({"lei": ent.lei, "name": ent.name, "status": "ok",
                         "periods": len(summaries), "fy_range": [summaries[-1].fy, summaries[0].fy]})
    cov_path = config.data_dir / "reports" / "eu_financials_coverage.jsonl"
    cov_path.parent.mkdir(parents=True, exist_ok=True)
    cov_path.write_text("\n".join(json.dumps(r, default=str) for r in coverage))
    out["coverage_path"] = str(cov_path)
    return out
