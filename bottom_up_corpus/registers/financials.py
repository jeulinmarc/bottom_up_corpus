"""Register-financials producer — Brreg JSON accounts -> the curated schema, in a
separate data/financials_register/ output labelled by basis."""
from __future__ import annotations

import json
from datetime import date

from ..config import Config
from ..financials import PeriodSummary, rows_from_base
from ..storage import Storage
from .concepts_no import map_brreg_entry
from .identity import resolve_register_specs
from .no_brreg import fetch_brreg_accounts


def _summary(mapped: dict, name: str) -> PeriodSummary:
    pe = date.fromisoformat(mapped["period_end"])
    return PeriodSummary(
        period_end=pe, frequency="annual", publication_date=None, sec_form="brreg",
        accession=f"brreg-{pe.isoformat()}", company=name, company_current=name,
        values=mapped["values"], currency=mapped["currency"], sic=None)


def _base(orgnr: str, lei, mapped: dict, summary: PeriodSummary) -> dict:
    return {"entity_id": orgnr, "lei": lei, "country": "NO", "source": "brreg",
            "basis": mapped["basis"], "fy": summary.fy, "frequency": "annual",
            "currency": mapped["currency"], "period_end": mapped["period_end"],
            "publication_date": None}


def build_register_financials(specs, *, fetcher, config: Config, write: bool = True) -> dict:
    resolved = resolve_register_specs(specs, fetcher=fetcher)
    storage = Storage(config)
    coverage: list[dict] = []
    out = {"entities": 0, "with_financials": 0, "no_financials": 0, "periods": 0, "paths": []}
    for r in resolved:
        out["entities"] += 1
        if not r.get("orgnr"):
            coverage.append({"orgnr": None, "lei": r.get("lei"), "status": "unresolved"})
            out["no_financials"] += 1
            continue
        rows: list[dict] = []
        n = 0
        for entry in fetch_brreg_accounts(r["orgnr"], fetcher=fetcher):
            mapped = map_brreg_entry(entry)
            if not mapped:
                continue
            s = _summary(mapped, r.get("name") or r["orgnr"])
            rows.extend(rows_from_base(_base(r["orgnr"], r.get("lei"), mapped, s), s))
            n += 1
        if not rows:
            coverage.append({"orgnr": r["orgnr"], "lei": r.get("lei"), "status": "no-financials"})
            out["no_financials"] += 1
            continue
        out["periods"] += n
        out["with_financials"] += 1
        if write:
            out["paths"].append(storage.write_register_financials_table(r["orgnr"], rows))
        coverage.append({"orgnr": r["orgnr"], "lei": r.get("lei"), "status": "ok", "periods": n})
    if write:
        cov = config.data_dir / "reports" / "register_coverage.jsonl"
        cov.parent.mkdir(parents=True, exist_ok=True)
        cov.write_text("\n".join(json.dumps(c, default=str) for c in coverage))
        out["coverage_path"] = str(cov)
    else:
        out["coverage_path"] = None
    return out
