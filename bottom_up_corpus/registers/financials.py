"""Register-financials producer — Brreg JSON accounts -> the curated schema, in a
separate data/financials_register/ output labelled by basis."""
from __future__ import annotations

import json
from datetime import date

from ..config import Config
from ..financials import PeriodSummary, rows_from_base
from ..storage import Storage, _atomic_write_text
from .concepts_no import map_brreg_entry
from .identity import resolve_register_specs
from .no_brreg import fetch_brreg_accounts


def _dedupe_latest(entries: list[dict]) -> list[dict]:
    """Collapse raw Brreg entries so each (regnskapsperiode.tilDato, regnskapstype)
    appears once, keeping the highest submission `id` — Brreg can return corrected /
    resubmitted accounts for the same period, which would otherwise double-count. When
    an `id` is missing on either side, the last-seen entry for that key wins. Operates
    on RAW entries (which still carry `id`), so `map_brreg_entry` stays unchanged."""
    best: dict[tuple, dict] = {}
    for e in entries:
        key = ((e.get("regnskapsperiode") or {}).get("tilDato"), e.get("regnskapstype"))
        cur = best.get(key)
        if cur is None:
            best[key] = e
            continue
        e_id, cur_id = e.get("id"), cur.get("id")
        if e_id is None or cur_id is None or e_id >= cur_id:
            best[key] = e
    return list(best.values())


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
        for entry in _dedupe_latest(fetch_brreg_accounts(r["orgnr"], fetcher=fetcher)):
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
        _atomic_write_text(cov, "\n".join(json.dumps(c, default=str) for c in coverage))
        out["coverage_path"] = str(cov)
    else:
        out["coverage_path"] = None
    return out
