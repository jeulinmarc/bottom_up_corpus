"""Orchestrator for the European acquisition (Pillar A).

resolve universe -> dispatch each entity to its country OAM backend + the
filings.xbrl.org complement -> merge/dedupe -> download every file -> write entity
index, manifests, and the coverage report.
"""
from __future__ import annotations

import json

from ..config import Config
from .dispatcher import merge_documents
from .download import download_document
from .entities import Entity, resolve_entities
from .reconcile import reconcile
from .sources.filings_org import FilingsXbrlOrg
from .sources.oam_be import StoriBE
from .sources.oam_de import BundesanzeigerDE
from .sources.oam_dk import OamDK
from .sources.oam_es import CnmvES
from .sources.oam_fi import OamFI
from .sources.oam_fr import InfoFinanciereFR
from .sources.oam_gb import NsmGB
from .sources.oam_it import OneInfoIT
from .sources.oam_nl import AfmNL
from .sources.oam_no import NewsWebNO

# Increment A+B+C backends. Entities whose country has no backend resolve but discover
# 0 docs -> the coverage report flags them as "no-documents" (deliberate: never
# silently partial).
COUNTRY_BACKENDS = {
    "BE": StoriBE,
    "DE": BundesanzeigerDE,
    "DK": OamDK,
    "ES": CnmvES,
    "FI": OamFI,
    "FR": InfoFinanciereFR,
    "GB": NsmGB,
    "IT": OneInfoIT,
    "NL": AfmNL,
    "NO": NewsWebNO,
}


def acquire(specs, *, fetcher, config: Config, download: bool = True) -> dict:
    entities = resolve_entities(specs, fetcher=fetcher)
    _write_entity_index(entities, config)

    all_docs, errors = [], []
    for e in entities:
        if not e.lei:
            continue
        backends = []
        cls = COUNTRY_BACKENDS.get(e.country)
        if cls:
            backends.append(cls(fetcher=fetcher, config=config))
        backends.append(FilingsXbrlOrg(fetcher=fetcher, config=config))
        per_backend = []
        for b in backends:
            try:
                per_backend.append(b.discover(e))
            except Exception as exc:  # noqa: BLE001
                per_backend.append([])
                errors.append({"source": "acquire", "context": "discover",
                               "entity": e.lei, "error": str(exc)})
            errors.extend(getattr(b, "errors", []))
        all_docs.extend(merge_documents(per_backend))

    manifests = 0
    download_errors = 0
    if download:
        for d in all_docs:
            man = download_document(d, fetcher=fetcher, config=config)
            manifests += 1
            for f in man.get("files", []):
                if "error" in f:
                    download_errors += 1
                    errors.append({"source": "acquire", "context": "download",
                                   "doc_id": d.doc_id, "file": f.get("name"),
                                   "error": f["error"]})

    cov = reconcile(entities, all_docs)
    cov_path = config.data_dir / "reports" / "eu_coverage.jsonl"
    cov_path.parent.mkdir(parents=True, exist_ok=True)
    cov_path.write_text("\n".join(json.dumps(r, default=str) for r in cov))

    return {"entities": len(entities), "documents": len(all_docs),
            "manifests": manifests, "download_errors": download_errors,
            "coverage_path": str(cov_path), "errors": errors}


def _write_entity_index(entities: list[Entity], config: Config) -> None:
    path = config.data_dir / "universe" / "eu_entities.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps({
        "lei": e.lei, "name": e.name, "country": e.country, "isins": list(e.isins),
        "tickers": list(e.tickers), "resolution": e.resolution}) for e in entities))
