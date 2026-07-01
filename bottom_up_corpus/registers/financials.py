"""Register-financials producer — Brreg JSON accounts + CH iXBRL -> the curated schema.

Two producers share a common tail:
- ``build_register_financials``: Norwegian Brreg register (multi-period per entity).
- ``build_ch_financials``: UK Companies House Accounts Bulk Data zip (one period per entity).

Both use the shared ``_emit_entity_rows`` helper for writing the financials table and
updating coverage/counter state, so the storage + coverage logic is only written once.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import date
from pathlib import Path

from ..config import Config
from ..eu.oim import flatten_oim_json
from ..financials import PeriodSummary, rows_from_base
from ..storage import Storage, _atomic_write_text
from .bnb_cbso import fetch_bnb_deposit as _fetch_bnb_deposit
from .bnb_xbrl import open_bnb_deposit, parse_bnb_document
from .ch_bulk import iter_ch_bulk
from .ch_ixbrl import oim_from_ch_html
from .concepts_be import map_bnb_facts
from .concepts_ee import map_ee_report as _map_ee_report
from .concepts_fi import map_fi_facts
from .concepts_lu import map_lu_entity
from .concepts_no import map_brreg_entry
from .concepts_uk import map_ch_facts
from .ee_csv import download_ee_bulk as _download_ee_bulk, iter_ee_reports as _iter_ee_reports
from .fi_prh_xbrl import parse_fi_facts
from .identity import resolve_register_specs
from .lu_cdb import iter_lu_declarers
from .no_brreg import fetch_brreg_accounts
from .prh_api import fetch_fi_financial, list_fi_dates

# Y-tunnus pattern: NNNNNNN-N (7 digits, hyphen, 1 check digit)
_YTUNNUS_RE = re.compile(r"(\d{7}-\d)")

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
    # Single choke point: drop concepts that are structurally unprovable from any
    # register source.  Filtering here — rather than in each individual producer —
    # means no new producer can accidentally emit them.
    rows = [row for row in rows if row.get("concept") not in _SUPPRESSED_CONCEPTS]
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
                rows.extend(rows_from_base(
                    _base(r["orgnr"], r.get("lei"), mapped, s,
                          country="NO", source="brreg"), s))
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
                # Arelle needs a real file path (not bytes). Create a named temp
                # file, then write + parse inside a single try/finally so the file
                # is always removed — even if the write itself raises.
                tmp_fd, tmp_name = tempfile.mkstemp(suffix=".html")
                try:
                    os.close(tmp_fd)
                    Path(tmp_name).write_bytes(html_bytes)
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


# ---------------------------------------------------------------------------
# Belgium BNB CBSO register producer
# ---------------------------------------------------------------------------
# Note: consolidated model detection (m120) is out of scope; all entities are
# emitted with basis="company" (the statutory individual accounts model).

def _be_pipeline(
    xbrl_source,
    entity_id: str,
    lei,
    name: str,
    *,
    storage: Storage,
    out: dict,
    coverage: list[dict],
    write: bool,
) -> None:
    """Parse, map, and emit rows for one BNB XBRL source (shared by both BE paths).

    ``xbrl_source`` is either a :class:`pathlib.Path` (for the keyless path)
    or ``bytes`` (for the API path after deposit extraction).  Raises on any
    parse or mapping error — callers must wrap in ``try/except``.
    """
    # Single parse for both facts and period_end (M1: avoids double-parsing at batch scale).
    flat, pe = parse_bnb_document(xbrl_source)
    mapped = map_bnb_facts(flat, period_end=pe)

    cov_base: dict = {"be_number": entity_id, "lei": lei}

    if mapped["unbalanced"]:
        cov = {**cov_base, "status": "unbalanced"}
        if mapped.get("suppressed"):
            cov["suppressed"] = mapped["suppressed"]
        coverage.append(cov)
        out["unbalanced"] += 1
        return

    if not mapped.get("values"):
        cov = {**cov_base, "status": "no-financials"}
        if mapped.get("suppressed"):
            cov["suppressed"] = mapped["suppressed"]
        coverage.append(cov)
        out["no_financials"] += 1
        return

    s = _summary(
        mapped, name or entity_id,
        sec_form="bnb",
        accession=f"bnb-{entity_id}-{mapped['period_end']}",
    )
    base = _base(entity_id, lei, mapped, s, country="BE", source="bnb")
    rows = list(rows_from_base(base, s))

    cov = dict(cov_base)
    if mapped.get("suppressed"):
        cov["suppressed"] = mapped["suppressed"]
    _emit_entity_rows(entity_id, rows, 1, cov, storage, out, coverage, write=write)


def build_be_financials_from_files(
    paths,
    *,
    config: Config,
    write: bool = True,
) -> dict:
    """Parse a list of local BNB .xbrl or deposit .zip files -> the curated schema.

    Each path is either a bare ``-data.xbrl`` file or a BNB deposit ``.zip``
    (the three-file archive; the ``*-data.xbrl`` member is extracted via
    :func:`open_bnb_deposit`).

    The entity identifier (KBO) is derived from the filename: the last
    underscore-delimited token of the stem is used (e.g.
    ``m02_full_0648822310.xbrl`` → ``"0648822310"``).

    Parameters
    ----------
    paths:
        Iterable of file paths (str or Path) pointing to ``.xbrl`` or ``.zip``
        files.
    config:
        Config instance (``data_dir`` for output).
    write:
        Persist rows + coverage (default True); False for dry-run.
    """
    storage = Storage(config)
    coverage: list[dict] = []
    out: dict = {
        "entities": 0, "with_financials": 0, "no_financials": 0,
        "unbalanced": 0, "errors": 0, "periods": 0, "paths": [],
    }

    for path in paths:
        out["entities"] += 1
        path_obj = Path(str(path))
        # KBO from the last underscore-delimited stem token (or the whole stem).
        entity_id = path_obj.stem.rsplit("_", 1)[-1]
        cov_base: dict = {"be_number": entity_id, "lei": None}

        try:
            if path_obj.suffix.lower() == ".zip":
                xbrl_bytes = open_bnb_deposit(path_obj.read_bytes())
                xbrl_source = xbrl_bytes   # bytes
            else:
                xbrl_source = path_obj     # Path (parsed from disk)

            _be_pipeline(
                xbrl_source, entity_id, None, entity_id,
                storage=storage, out=out, coverage=coverage, write=write,
            )

        except Exception as exc:  # noqa: BLE001 — record + skip, keep the batch going
            coverage.append({**cov_base, "status": "error", "error": str(exc)})
            out["errors"] += 1
            continue

    if write:
        cov_path = config.data_dir / "reports" / "register_coverage.jsonl"
        _atomic_write_text(
            cov_path, "\n".join(json.dumps(c, default=str) for c in coverage))
        out["coverage_path"] = str(cov_path)
    else:
        out["coverage_path"] = None
    return out


def build_lu_financials_from_files(
    paths,
    *,
    config: Config,
    write: bool = True,
    rcs_filter=None,
) -> dict:
    """Parse a list of local LBR eCDF XML files and emit the curated schema.

    Each file is a STATEC/LBR eCDF bulk XML (as published on data.public.lu) or
    a single-entity XML; it may contain one or more ``<Declarer>`` elements.
    Each declarer is mapped via :func:`map_lu_entity` and emitted through the
    shared ``_emit_entity_rows`` tail.

    Parameters
    ----------
    paths:
        Iterable of file paths (str or Path) pointing to eCDF XML files.
    config:
        Config instance (``data_dir`` for output).
    write:
        Persist rows + coverage (default True); False for dry-run.
    rcs_filter:
        Optional collection of RCS strings (e.g. ``{"B60814"}``).  When
        provided, only declarers whose ``rcs`` is in the set are processed.
        Pass ``None`` (the default) to process every declarer.
    """
    storage = Storage(config)
    coverage: list[dict] = []
    out: dict = {
        "entities": 0, "with_financials": 0, "no_financials": 0,
        "unbalanced": 0, "errors": 0, "periods": 0, "paths": [],
    }

    for path in paths:
        path_obj = Path(str(path))
        # Materialise all declarers for this file inside a try so that a bad
        # path (missing file, malformed XML) is isolated from the rest of the batch.
        try:
            declarers = list(iter_lu_declarers(path_obj, rcs_filter=rcs_filter))
        except Exception as exc:  # noqa: BLE001 — path-level error, no entity_id
            coverage.append({"rcs": path_obj.name, "lei": None,
                             "status": "error", "error": str(exc)})
            out["errors"] += 1
            continue

        for declarer in declarers:
            out["entities"] += 1
            entity_id = declarer["rcs"]
            cov_base: dict = {"rcs": entity_id, "lei": None}

            try:
                mapped = map_lu_entity(declarer["declarations"])

                if mapped["unbalanced"]:
                    cov = {**cov_base, "status": "unbalanced"}
                    if mapped.get("suppressed"):
                        cov["suppressed"] = mapped["suppressed"]
                    coverage.append(cov)
                    out["unbalanced"] += 1
                    continue

                if not mapped.get("values"):
                    cov = {**cov_base, "status": "no-financials"}
                    if mapped.get("suppressed"):
                        cov["suppressed"] = mapped["suppressed"]
                    coverage.append(cov)
                    out["no_financials"] += 1
                    continue

                s = _summary(
                    mapped, declarer.get("name") or entity_id,
                    sec_form="lbr",
                    accession=f"lbr-{entity_id}-{mapped['period_end']}",
                )
                base = _base(entity_id, None, mapped, s, country="LU", source="lbr")
                rows = list(rows_from_base(base, s))

                cov = dict(cov_base)
                if mapped.get("suppressed"):
                    cov["suppressed"] = mapped["suppressed"]
                _emit_entity_rows(entity_id, rows, 1, cov, storage, out, coverage,
                                  write=write)

            except Exception as exc:  # noqa: BLE001 — record + skip, keep the batch going
                coverage.append({**cov_base, "status": "error", "error": str(exc)})
                out["errors"] += 1
                continue

    if write:
        cov_path = config.data_dir / "reports" / "register_coverage.jsonl"
        _atomic_write_text(
            cov_path, "\n".join(json.dumps(c, default=str) for c in coverage))
        out["coverage_path"] = str(cov_path)
    else:
        out["coverage_path"] = None
    return out


def build_be_financials(
    specs,
    *,
    fetcher,
    config: Config,
    key: str,
    write: bool = True,
) -> dict:
    """Fetch BNB deposit via the CBSO API and emit the curated schema.

    Resolves each spec to a KBO number via :func:`resolve_register_specs`, then
    calls :func:`fetch_bnb_deposit` to retrieve the latest deposit bytes.  The
    deposit may be a deposit ``.zip`` or a bare ``.xbrl`` depending on the model
    type; both are handled transparently.

    Live / scale validation — rate limits, pagination behaviour for entities with
    a large deposit history, key-quota behaviour — is a **maintainer step** and
    is intentionally out of scope for this function.  Unit-test with a stubbed
    fetcher returning fixture bytes.

    Parameters
    ----------
    specs:
        List of spec dicts accepted by :func:`resolve_register_specs` (e.g.
        ``[{"be_number": "0648822310"}]`` or ``[{"lei": "…"}]``).
    fetcher:
        A :class:`bottom_up_corpus.http.Fetcher` instance (or any object that
        exposes ``get_json`` and ``get``).
    config:
        Config instance (``data_dir`` for output).
    key:
        CBSO Authentic Data API subscription key.
    write:
        Persist rows + coverage (default True); False for dry-run.
    """
    resolved = resolve_register_specs(specs, fetcher=fetcher)
    storage = Storage(config)
    coverage: list[dict] = []
    out: dict = {
        "entities": 0, "with_financials": 0, "no_financials": 0,
        "unbalanced": 0, "errors": 0, "periods": 0, "paths": [],
    }

    for r in resolved:
        out["entities"] += 1
        be_number = r.get("be_number")
        cov_base: dict = {"be_number": be_number, "lei": r.get("lei")}

        if not be_number:
            coverage.append({**cov_base, "status": "unresolved"})
            out["no_financials"] += 1
            continue

        try:
            deposit_bytes = _fetch_bnb_deposit(be_number, fetcher=fetcher, key=key)
            if deposit_bytes is None:
                coverage.append({**cov_base, "status": "no-financials"})
                out["no_financials"] += 1
                continue

            # Detect deposit zip (PK magic) vs bare .xbrl bytes.
            xbrl_bytes = (
                open_bnb_deposit(deposit_bytes)
                if deposit_bytes[:4] == b"PK\x03\x04"
                else deposit_bytes
            )

            _be_pipeline(
                xbrl_bytes, be_number, r.get("lei"), r.get("name") or be_number,
                storage=storage, out=out, coverage=coverage, write=write,
            )

        except Exception as exc:  # noqa: BLE001 — record + skip, keep the batch going
            coverage.append({**cov_base, "status": "error", "error": str(exc)})
            out["errors"] += 1
            continue

    if write:
        cov_path = config.data_dir / "reports" / "register_coverage.jsonl"
        _atomic_write_text(
            cov_path, "\n".join(json.dumps(c, default=str) for c in coverage))
        out["coverage_path"] = str(cov_path)
    else:
        out["coverage_path"] = None
    return out


# ---------------------------------------------------------------------------
# Finland PRH open-data XBRL register producer
# ---------------------------------------------------------------------------
# Keyless open API — no subscription key required.  The XBRL instance document
# uses a dimensional model (fi_MC metric codes); ``parse_fi_facts`` (stdlib,
# no Arelle) + ``map_fi_facts`` (NO-FALSE-DATA gate) handle parsing/mapping.
# ``basis="company"`` (FAS individual statutory accounts). EUR only.
#
# net_income = x740 (FINAL, after appropriations), NEVER x738.
# Liabilities-based leverage: total liabilities (x513) emitted; the long/short
# maturity split (x583/x816) is suppressed because the label linkbase is not
# included in the instance — no-false-data prevents guessing which is LT vs ST.


def _fi_entity_id(path_obj: Path) -> str:
    """Extract Y-tunnus (NNNNNNN-N) from the filename stem.

    E.g. ``fi_2919415-2_full_2024.xml`` → ``"2919415-2"``.  Falls back to the
    full stem when no match is found.
    """
    m = _YTUNNUS_RE.search(path_obj.stem)
    return m.group(1) if m else path_obj.stem


def _fi_pipeline(
    xbrl_source,
    entity_id: str,
    lei,
    name: str,
    *,
    storage: Storage,
    out: dict,
    coverage: list[dict],
    write: bool,
) -> None:
    """Parse, map, and emit rows for one PRH XBRL source (shared by both FI paths).

    ``xbrl_source`` is a :class:`pathlib.Path` (keyless file path) or ``bytes``
    (API path).  Raises on any parse or mapping error — callers must wrap in
    ``try/except``.
    """
    parsed = parse_fi_facts(xbrl_source)
    mapped = map_fi_facts(parsed)

    cov_base: dict = {"business_id": entity_id, "lei": lei}

    if mapped["unbalanced"]:
        cov = {**cov_base, "status": "unbalanced"}
        if mapped.get("suppressed"):
            cov["suppressed"] = mapped["suppressed"]
        coverage.append(cov)
        out["unbalanced"] += 1
        return

    if not mapped.get("values"):
        cov = {**cov_base, "status": "no-financials"}
        if mapped.get("suppressed"):
            cov["suppressed"] = mapped["suppressed"]
        coverage.append(cov)
        out["no_financials"] += 1
        return

    s = _summary(
        mapped, name or entity_id,
        sec_form="prh",
        accession=f"prh-{entity_id}-{mapped['period_end']}",
    )
    base = _base(entity_id, lei, mapped, s, country="FI", source="prh")
    rows = list(rows_from_base(base, s))

    cov = dict(cov_base)
    if mapped.get("suppressed"):
        cov["suppressed"] = mapped["suppressed"]
    _emit_entity_rows(entity_id, rows, 1, cov, storage, out, coverage, write=write)


def build_fi_financials_from_files(
    paths,
    *,
    config: Config,
    write: bool = True,
) -> dict:
    """Parse a list of local PRH XBRL ``.xml`` files → the curated schema.

    The entity identifier (Y-tunnus) is extracted from the filename using the
    ``NNNNNNN-N`` pattern (e.g. ``fi_2919415-2_full_2024.xml`` → ``"2919415-2"``).

    Parameters
    ----------
    paths:
        Iterable of file paths (str or Path) pointing to PRH XBRL ``.xml`` files.
    config:
        Config instance (``data_dir`` for output).
    write:
        Persist rows + coverage (default True); False for dry-run.
    """
    storage = Storage(config)
    coverage: list[dict] = []
    out: dict = {
        "entities": 0, "with_financials": 0, "no_financials": 0,
        "unbalanced": 0, "errors": 0, "periods": 0, "paths": [],
    }

    for path in paths:
        out["entities"] += 1
        path_obj = Path(str(path))
        entity_id = _fi_entity_id(path_obj)
        cov_base: dict = {"business_id": entity_id, "lei": None}

        try:
            _fi_pipeline(
                path_obj, entity_id, None, entity_id,
                storage=storage, out=out, coverage=coverage, write=write,
            )
        except Exception as exc:  # noqa: BLE001 — record + skip, keep the batch going
            coverage.append({**cov_base, "status": "error", "error": str(exc)})
            out["errors"] += 1
            continue

    if write:
        cov_path = config.data_dir / "reports" / "register_coverage.jsonl"
        _atomic_write_text(
            cov_path, "\n".join(json.dumps(c, default=str) for c in coverage))
        out["coverage_path"] = str(cov_path)
    else:
        out["coverage_path"] = None
    return out


def build_fi_financials(
    specs,
    *,
    fetcher,
    config: Config,
    write: bool = True,
) -> dict:
    """Fetch PRH XBRL via the open API and emit the curated schema.

    Resolves each spec to a Y-tunnus via :func:`resolve_register_specs`, then
    calls :func:`list_fi_dates` to find the latest available date and
    :func:`fetch_fi_financial` to retrieve the XBRL bytes.

    Keyless — no API key required.  Live / scale validation — rate limits,
    pagination behaviour, filing completeness for a given date — is a
    **controller step** and is intentionally out of scope for this function.
    Unit-test with a stubbed fetcher returning fixture bytes.

    Parameters
    ----------
    specs:
        List of spec dicts accepted by :func:`resolve_register_specs` (e.g.
        ``[{"business_id": "2919415-2"}]`` or ``[{"lei": "…"}]``).
    fetcher:
        A :class:`bottom_up_corpus.http.Fetcher` instance (or any object that
        exposes ``get_json`` and ``get``).
    config:
        Config instance (``data_dir`` for output).
    write:
        Persist rows + coverage (default True); False for dry-run.
    """
    resolved = resolve_register_specs(specs, fetcher=fetcher)
    storage = Storage(config)
    coverage: list[dict] = []
    out: dict = {
        "entities": 0, "with_financials": 0, "no_financials": 0,
        "unbalanced": 0, "errors": 0, "periods": 0, "paths": [],
    }

    for r in resolved:
        out["entities"] += 1
        business_id = r.get("business_id")
        cov_base: dict = {"business_id": business_id, "lei": r.get("lei")}

        if not business_id:
            coverage.append({**cov_base, "status": "unresolved"})
            out["no_financials"] += 1
            continue

        try:
            dates = list_fi_dates(business_id, fetcher=fetcher)
            if not dates:
                coverage.append({**cov_base, "status": "no-financials"})
                out["no_financials"] += 1
                continue

            latest_date = max(dates)
            xbrl_bytes = fetch_fi_financial(business_id, latest_date, fetcher=fetcher)
            if xbrl_bytes is None:
                coverage.append({**cov_base, "status": "no-financials"})
                out["no_financials"] += 1
                continue

            _fi_pipeline(
                xbrl_bytes, business_id, r.get("lei"), r.get("name") or business_id,
                storage=storage, out=out, coverage=coverage, write=write,
            )

        except Exception as exc:  # noqa: BLE001 — record + skip, keep the batch going
            coverage.append({**cov_base, "status": "error", "error": str(exc)})
            out["errors"] += 1
            continue

    if write:
        cov_path = config.data_dir / "reports" / "register_coverage.jsonl"
        _atomic_write_text(
            cov_path, "\n".join(json.dumps(c, default=str) for c in coverage))
        out["coverage_path"] = str(cov_path)
    else:
        out["coverage_path"] = None
    return out


# ---------------------------------------------------------------------------
# Estonia Äriregister (RIK) bulk-CSV register producer
# ---------------------------------------------------------------------------
# Keyless open-data — no subscription key required.  stdlib csv + zipfile only.
# The bulk CSV join (_iter_ee_reports) + concept map (_map_ee_report) handle
# parsing/mapping.  basis="company" (RIK standalone statutory accounts). EUR.
#
# net_income = TotalAnnualPeriodProfitLoss (FINAL after tax) — NEVER TotalProfitLoss.
# Liabilities-based leverage: short_term_debt=CurrentLiabilities,
# long_term_debt=NonCurrentLiabilities.
# interest_expense / interest_coverage: ALWAYS suppressed (not in RIK bulk).


def build_ee_financials_from_files(
    elem_path,
    meta_path,
    *,
    config: Config,
    write: bool = True,
    limit: int | None = None,
) -> dict:
    """Iterate EE Äriregister bulk CSVs and emit the curated schema.

    Joins the elements and metadata CSVs via :func:`iter_ee_reports`, maps each
    report through :func:`map_ee_report`, and emits rows via the shared tail.

    Parameters
    ----------
    elem_path:
        File path (str / Path) or raw bytes of the elements CSV (or its .zip).
    meta_path:
        File path (str / Path) or raw bytes of the metadata CSV (or its .zip).
    config:
        Config instance (``data_dir`` for output).
    write:
        Persist rows + coverage (default True); False for dry-run.
    limit:
        Cap on the number of reports processed.  ``None`` = no cap.
    """
    storage = Storage(config)
    coverage: list[dict] = []
    out: dict = {
        "entities": 0, "with_financials": 0, "no_financials": 0,
        "unbalanced": 0, "errors": 0, "periods": 0, "paths": [],
    }

    for report in _iter_ee_reports(elem_path, meta_path):
        if limit is not None and out["entities"] >= limit:
            break
        out["entities"] += 1
        registrikood = report.get("registrikood")
        cov_base: dict = {"registrikood": registrikood, "lei": None}

        if not registrikood:
            coverage.append({**cov_base, "status": "no-financials"})
            out["no_financials"] += 1
            continue

        try:
            mapped = _map_ee_report(
                report["elements"], report["period_end"], registrikood
            )

            if mapped["unbalanced"]:
                cov = {**cov_base, "status": "unbalanced"}
                if mapped.get("suppressed"):
                    cov["suppressed"] = mapped["suppressed"]
                coverage.append(cov)
                out["unbalanced"] += 1
                continue

            if not mapped.get("values"):
                cov = {**cov_base, "status": "no-financials"}
                if mapped.get("suppressed"):
                    cov["suppressed"] = mapped["suppressed"]
                coverage.append(cov)
                out["no_financials"] += 1
                continue

            s = _summary(
                mapped, registrikood,
                sec_form="rik",
                accession=f"rik-{registrikood}-{mapped['period_end']}",
            )
            base = _base(registrikood, None, mapped, s, country="EE", source="rik")
            rows = list(rows_from_base(base, s))

            cov = dict(cov_base)
            if mapped.get("suppressed"):
                cov["suppressed"] = mapped["suppressed"]
            _emit_entity_rows(registrikood, rows, 1, cov, storage, out, coverage,
                              write=write)

        except Exception as exc:  # noqa: BLE001 — record + skip, keep the batch going
            coverage.append({**cov_base, "status": "error", "error": str(exc)})
            out["errors"] += 1
            continue

    if write:
        cov_path = config.data_dir / "reports" / "register_coverage.jsonl"
        _atomic_write_text(
            cov_path, "\n".join(json.dumps(c, default=str) for c in coverage))
        out["coverage_path"] = str(cov_path)
    else:
        out["coverage_path"] = None
    return out


def build_ee_financials(
    year: int,
    *,
    fetcher,
    config: Config,
    write: bool = True,
    limit: int | None = None,
    elem_url: str | None = None,
    meta_url: str | None = None,
) -> dict:
    """Download EE Äriregister bulk CSVs for *year* and emit the curated schema.

    Downloads the two bulk zips via :func:`download_ee_bulk` (keyless, CC-BY),
    then pipes the bytes through :func:`build_ee_financials_from_files`.

    Parameters
    ----------
    year:
        Fiscal year of interest — passed to ``download_ee_bulk`` for logging.
    fetcher:
        HTTP fetcher (exposes ``get(url) -> response`` with ``.content``).
    config:
        Config instance (``data_dir`` for output).
    write:
        Persist rows + coverage (default True); False for dry-run.
    limit:
        Cap on the number of reports processed.
    elem_url:
        Full URL for the elements zip (rotates with each RIK snapshot; obtain
        from avaandmed.ariregister.rik.ee/et/avaandmete-allalaadimine).
    meta_url:
        Full URL for the metadata zip.
    """
    elem_bytes, meta_bytes = _download_ee_bulk(
        year, fetcher=fetcher, elem_url=elem_url, meta_url=meta_url
    )
    return build_ee_financials_from_files(
        elem_bytes, meta_bytes, config=config, write=write, limit=limit
    )
