"""Register-financials producer — Brreg JSON accounts + CH iXBRL -> the curated schema.

Two producers share a common tail:
- ``build_register_financials``: Norwegian Brreg register (multi-period per entity).
- ``build_ch_financials``: UK Companies House Accounts Bulk Data zip (one period per entity).

Both use the shared ``_emit_entity_rows`` helper for writing the financials table and
updating coverage/counter state, so the storage + coverage logic is only written once.
"""
from __future__ import annotations

import json
import tempfile
from datetime import date
from pathlib import Path

from ..config import Config
from ..eu.oim import flatten_oim_json
from ..financials import PeriodSummary, rows_from_base
from ..storage import Storage, _atomic_write_text
from .ch_bulk import iter_ch_bulk
from .ch_ixbrl import oim_from_ch_html
from .concepts_no import map_brreg_entry
from .concepts_uk import map_ch_facts
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


def _summary(
    mapped: dict, name: str,
    *, sec_form: str = "brreg", accession: str | None = None,
) -> PeriodSummary:
    """Build a PeriodSummary from a ``mapped`` dict (NO or UK)."""
    pe = date.fromisoformat(mapped["period_end"])
    acc = accession if accession is not None else f"{sec_form}-{pe.isoformat()}"
    return PeriodSummary(
        period_end=pe, frequency="annual", publication_date=None, sec_form=sec_form,
        accession=acc, company=name, company_current=name,
        values=mapped["values"], currency=mapped["currency"], sic=None)


def _base(
    entity_id: str, lei, mapped: dict, summary: PeriodSummary,
    *, country: str, source: str,
) -> dict:
    """Build the common row base dict (identity + period columns)."""
    return {"entity_id": entity_id, "lei": lei, "country": country, "source": source,
            "basis": mapped["basis"], "fy": summary.fy, "frequency": "annual",
            "currency": mapped["currency"], "period_end": mapped["period_end"],
            "publication_date": None}


def _emit_entity_rows(
    entity_id: str, rows: list[dict], n_periods: int,
    cov_base: dict, storage: Storage, out: dict, coverage: list[dict],
    *, write: bool,
) -> None:
    """Shared tail: write the financials table, update counters, append coverage entry.

    Handles both the ``no-financials`` (empty rows) and ``ok`` paths. Error and
    ``unbalanced`` paths are handled by the individual producers before calling here.

    Parameters
    ----------
    entity_id:  Key for ``write_register_financials_table`` and ``paths``.
    rows:       Pre-built row list from ``rows_from_base``; may be empty.
    n_periods:  Number of source periods that contributed rows (for the coverage entry).
    cov_base:   Dict of coverage-identifying fields (e.g. ``{"orgnr": …}`` for NO,
                ``{"ch_number": …}`` for UK); ``status`` and ``periods`` are added here.
    storage:    Storage instance for ``write_register_financials_table``.
    out:        Mutable summary dict; ``no_financials`` / ``with_financials`` / ``periods``
                / ``paths`` are updated in-place.
    coverage:   Mutable list; one entry is appended.
    write:      When False, skip the disk write and ``paths`` update.
    """
    if not rows:
        coverage.append({**cov_base, "status": "no-financials"})
        out["no_financials"] += 1
        return
    out["periods"] += n_periods
    out["with_financials"] += 1
    if write:
        out["paths"].append(storage.write_register_financials_table(entity_id, rows))
    coverage.append({**cov_base, "status": "ok", "periods": n_periods})


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
                rows.extend(
                    row for row in rows_from_base(
                        _base(r["orgnr"], r.get("lei"), mapped, s,
                              country="NO", source="brreg"), s)
                    if row.get("concept") not in _SUPPRESSED_CONCEPTS
                )
                n += 1
            cov_base = {"orgnr": r["orgnr"], "lei": r.get("lei")}
            _emit_entity_rows(r["orgnr"], rows, n, cov_base, storage, out, coverage,
                              write=write)
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


def build_ch_financials(
    zip_path: str,
    *,
    config: Config,
    write: bool = True,
    limit: int | None = None,
    cntlr=None,
) -> dict:
    """Parse a Companies House Accounts Bulk Data zip and emit the curated schema.

    Iterates ``iter_ch_bulk(zip_path, limit=limit)``, writes each HTML to a temp
    file, parses it with Arelle via ``oim_from_ch_html``, flattens + maps through
    ``map_ch_facts``, and emits rows via the shared tail.

    A single Arelle ``Cntlr`` is shared for the whole batch (first file ~14 s
    taxonomy download, subsequent files ~0.7 s each).  Pass a pre-built ``cntlr``
    to skip the Arelle dependency check (useful for unit tests).

    Parameters
    ----------
    zip_path:  Path to a CH Accounts Bulk Data .zip file.
    config:    Config instance (data_dir for output).
    write:     Persist rows + coverage (default True); False for dry-run.
    limit:     Cap on entities processed.
    cntlr:     Optional shared Arelle Cntlr; created internally if None.
    """
    own_cntlr = cntlr is None
    if own_cntlr:
        try:
            from arelle import Cntlr as _ArelleCntlr
        except ImportError as exc:
            raise ImportError(
                "UK Companies House iXBRL parsing needs Arelle — install the optional "
                "extra: pip install '.[eu-financials]'"
            ) from exc
        cntlr = _ArelleCntlr.Cntlr(logFileName="logToBuffer")

    storage = Storage(config)
    coverage: list[dict] = []
    out: dict = {"entities": 0, "with_financials": 0, "no_financials": 0,
                 "unbalanced": 0, "errors": 0, "periods": 0, "paths": []}

    try:
        for ch_number, html_bytes in iter_ch_bulk(zip_path, limit=limit):
            out["entities"] += 1
            cov_base: dict = {"ch_number": ch_number, "lei": None}

            try:
                # Arelle needs a real file path (not bytes); write to a temp file and
                # always clean up regardless of parse success/failure.
                with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as tmp:
                    tmp_name = tmp.name  # M1: assign before write so finally unlink is safe
                    tmp.write(html_bytes)
                try:
                    oim = oim_from_ch_html(tmp_name, cntlr=cntlr)
                finally:
                    Path(tmp_name).unlink(missing_ok=True)

                flat = flatten_oim_json(
                    oim, filed="", form="accounts", accn=f"ch-{ch_number}")
                mapped = map_ch_facts(flat)

                # C1: None check first, then unbalanced, then empty values — so that
                # the unbalanced branch is reached before the no-values branch (an
                # unbalanced filing returns values={}, which would otherwise fall
                # through to the no-financials path first).
                if mapped is None:
                    coverage.append({**cov_base, "status": "no-financials"})
                    out["no_financials"] += 1
                    continue

                # NetAssets != Equity -> whole filing rejected
                if mapped["unbalanced"]:
                    cov = {**cov_base, "status": "unbalanced"}
                    if mapped.get("suppressed"):
                        cov["suppressed"] = mapped["suppressed"]
                    coverage.append(cov)
                    out["unbalanced"] += 1
                    continue

                # No emittable values (but not an outright unbalanced rejection)
                if not mapped.get("values"):
                    cov = {**cov_base, "status": "no-financials"}
                    if mapped.get("suppressed"):
                        cov["suppressed"] = mapped["suppressed"]
                    coverage.append(cov)
                    out["no_financials"] += 1
                    continue

                s = _summary(
                    mapped, ch_number,
                    sec_form="companies_house",
                    accession=f"ch-{ch_number}-{mapped['period_end']}",
                )
                base = _base(ch_number, None, mapped, s, country="GB",
                             source="companies_house")
                rows = list(rows_from_base(base, s))

                cov = dict(cov_base)
                if mapped.get("suppressed"):
                    cov["suppressed"] = mapped["suppressed"]
                _emit_entity_rows(ch_number, rows, 1, cov, storage, out, coverage,
                                  write=write)

            except Exception as exc:  # noqa: BLE001 — record + skip, keep the batch
                coverage.append({**cov_base, "status": "error", "error": str(exc)})
                out["errors"] += 1
                continue

    finally:
        if own_cntlr:
            cntlr.close()

    if write:
        cov_path = config.data_dir / "reports" / "register_coverage.jsonl"
        _atomic_write_text(cov_path,
                           "\n".join(json.dumps(c, default=str) for c in coverage))
        out["coverage_path"] = str(cov_path)
    else:
        out["coverage_path"] = None
    return out
