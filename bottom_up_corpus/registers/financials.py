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

# Brreg's standard layout exposes assets only as the aggregate `sumAnleggsmidler` and
# never breaks out goodwill / intangibles, so the engine's tangible_book_value
# (= common equity − goodwill − intangibles, both defaulting to 0) collapses to `equity`
# and would silently OVERSTATE true TBV for any obligor carrying intangibles. We
# structurally cannot compute it from the register, so we suppress it (and its per-share
# form, already absent for want of a share count) rather than emit a misleading figure.
_SUPPRESSED_CONCEPTS = {"tangible_book_value", "tangible_book_value_per_share"}


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
        # Type-safe: a heterogeneous / non-int `id` would make `e_id >= cur_id` raise
        # TypeError and abort the batch; treat any non-int id as "keep last-seen".
        if not isinstance(e_id, int) or not isinstance(cur_id, int) or e_id >= cur_id:
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
    out = {"entities": 0, "with_financials": 0, "no_financials": 0, "periods": 0,
           "errors": 0, "paths": []}
    for r in resolved:
        out["entities"] += 1
        if not r.get("orgnr"):
            coverage.append({"orgnr": None, "lei": r.get("lei"), "status": "unresolved"})
            out["no_financials"] += 1
            continue
        try:  # one malformed record must not abort the whole batch (nor the coverage write)
            rows: list[dict] = []
            n = 0
            for entry in _dedupe_latest(fetch_brreg_accounts(r["orgnr"], fetcher=fetcher)):
                mapped = map_brreg_entry(entry)
                if not mapped:
                    continue
                s = _summary(mapped, r.get("name") or r["orgnr"])
                # I1: drop tangible_book_value (unprovable from the register) per-row.
                rows.extend(row for row in rows_from_base(_base(r["orgnr"], r.get("lei"), mapped, s), s)
                            if row.get("concept") not in _SUPPRESSED_CONCEPTS)
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
        except Exception as exc:  # noqa: BLE001 — record + skip, keep the batch going
            coverage.append({"orgnr": r["orgnr"], "lei": r.get("lei"),
                             "status": "error", "error": str(exc)})
            out["errors"] += 1
            continue
    if write:
        cov = config.data_dir / "reports" / "register_coverage.jsonl"
        _atomic_write_text(cov, "\n".join(json.dumps(c, default=str) for c in coverage))
        out["coverage_path"] = str(cov)
    else:
        out["coverage_path"] = None
    return out
