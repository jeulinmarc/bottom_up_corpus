"""Command-line interface for bottom_up_corpus.

Mirrors cb_corpus CLI ergonomics: subcommands, a dry-run-by-default posture, and
an explicit ``--write`` flag for side effects. Phase 0 added inspection
commands; Phase 1 adds the issuer universe and EDGAR discovery.
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import date

from . import __version__
from .completeness import build_matrix, summarize
from .config import Config, normalize_cik
from .entity import EntityRegistry
from .http import Fetcher
from .pipeline import (
    discover_universe,
    download_universe,
    fetch_financials,
    process_ownership,
    render_universe,
)
from .rag import iter_items
from .sources.edgar_index import EdgarFullIndex
from .storage import Storage
from .taxonomy import FULL_SCOPE, FormType, parse_scope
from .universe import Universe, resolve_ciks, resolve_tickers


def _parse_years(spec: str | None) -> list[int]:
    """Expand ``"2006-2025"`` / ``"2024"`` / ``"2006-2025,2026"`` to a year list."""
    if not spec:
        this_year = date.today().year
        return list(range(this_year - 19, this_year + 1))  # default: last 20 years
    years: set[int] = set()
    for token in spec.split(","):
        token = token.strip()
        if not token:
            continue
        if "-" in token:
            lo, hi = token.split("-", 1)
            years.update(range(int(lo), int(hi) + 1))
        else:
            years.add(int(token))
    return sorted(years)


def _ciks_for(args: argparse.Namespace, config: Config) -> list[str]:
    """Resolve the CIK set from --ciks or --universe."""
    if getattr(args, "ciks", None):
        return [normalize_cik(c) for c in args.ciks.split(",") if c.strip()]
    if getattr(args, "universe", None):
        return list(Universe(config).iter_ciks(args.universe))
    raise SystemExit("error: provide --ciks or --universe")


# ---- commands ----
def _cmd_list_forms(args: argparse.Namespace) -> int:
    scope = parse_scope(args.forms)
    default = set(FULL_SCOPE)
    print(f"{'code':<5} {'family':<7} {'in_default':<11} edgar_forms / label")
    print("-" * 72)
    for ft in scope:
        forms = ", ".join(ft.edgar_forms) if ft.edgar_forms else "(structured/XBRL)"
        flag = "yes" if ft in default else "opt-in"
        print(f"{ft.code:<5} {ft.family:<7} {flag:<11} {ft.label}  [{forms}]")
    return 0


def _cmd_config(args: argparse.Namespace) -> int:
    cfg = Config()
    print(f"version           : {__version__}")
    print(f"data_dir          : {cfg.data_dir}")
    print(f"user_agent        : {cfg.user_agent}")
    print(f"requests_per_sec  : {cfg.requests_per_second} (SEC max 10)")
    print(f"min_delay_seconds : {cfg.min_delay_seconds:.4f}")
    print(f"manifest_dir      : {cfg.manifest_dir}")
    print(f"universe_dir      : {cfg.universe_dir}")
    print(f"raw_dir           : {cfg.raw_dir}")
    return 0


def _cmd_build_universe(args: argparse.Namespace) -> int:
    cfg = Config()
    fetcher = Fetcher(cfg)
    issuers: list = []
    if args.tickers:
        tickers = [t for t in args.tickers.split(",") if t.strip()]
        resolved, unresolved = resolve_tickers(tickers, fetcher)
        issuers.extend(resolved)
        if unresolved:
            print(f"WARNING: unresolved tickers (delisted/renamed?): {', '.join(unresolved)}",
                  file=sys.stderr)
    if args.ciks:
        # CIK-anchored: works for delisted/merged issuers the ticker map omits.
        issuers.extend(resolve_ciks([c for c in args.ciks.split(",") if c.strip()], fetcher))
    if not issuers:
        raise SystemExit("error: provide --tickers and/or --ciks")
    if args.write:
        path = Universe(cfg).save(args.name, issuers)
        print(f"wrote {len(issuers)} issuers -> {path}")
    else:
        print(f"[dry-run] resolved {len(issuers)} issuers for universe '{args.name}':")
        for it in issuers:
            print(f"  {it.cik}  {it.ticker:<8} {it.company}")
        print("re-run with --write to persist")
    return 0


def _cmd_list_universe(args: argparse.Namespace) -> int:
    uni = Universe(Config())
    if not args.name:
        names = uni.names()
        print("universes:", ", ".join(names) if names else "(none)")
        return 0
    issuers = uni.load(args.name)
    print(f"universe '{args.name}': {len(issuers)} issuers")
    for it in issuers:
        print(f"  {it.cik}  {it.ticker:<8} {it.company}")
    return 0


def _cmd_discover(args: argparse.Namespace) -> int:
    cfg = Config()
    ciks = _ciks_for(args, cfg)
    scope = parse_scope(args.forms)
    years = _parse_years(args.years)
    since = date(min(years), 1, 1) if years else None
    dry_run = not args.write

    # --download implies persisting the manifest (records must exist on disk).
    if args.download:
        dry_run = False

    report = discover_universe(
        ciks,
        scope=scope,
        since=since,
        dry_run=dry_run,
        max_rounds=args.rounds,
        config=cfg,
    )
    mode = "DRY-RUN (nothing written)" if dry_run else "WROTE manifests"
    s = report.stats
    print(f"discover [{mode}] — {report.issuers} issuers, {report.rounds} round(s)")
    print(f"  seen={s.seen} added={s.added} updated={s.updated} unchanged={s.unchanged}")
    if report.errors:
        print(f"  discovery errors: {len(report.errors)} (see discovery_errors.jsonl)")

    if args.download:
        dl = download_universe(
            ciks, scope=scope, dry_run=False, overwrite=args.overwrite,
            limit=args.limit, config=cfg,
        )
        print(f"download — got={dl.downloaded} skipped={dl.skipped} errors={dl.errors} "
              f"bytes={dl.bytes:,}")
    return 0


def _cmd_download(args: argparse.Namespace) -> int:
    cfg = Config()
    ciks = _ciks_for(args, cfg)
    scope = parse_scope(args.forms)
    dry_run = not args.write
    dl = download_universe(
        ciks, scope=scope, dry_run=dry_run, overwrite=args.overwrite,
        limit=args.limit, config=cfg,
    )
    mode = "DRY-RUN (nothing written)" if dry_run else "WROTE"
    print(f"download [{mode}] — got={dl.downloaded} skipped={dl.skipped} "
          f"empty={dl.empty} errors={dl.errors} bytes={dl.bytes:,}")
    if dl.error_items:
        print(f"  errors logged: {len(dl.error_items)} (see discovery_errors.jsonl)")
    return 0


def _cmd_render_pdf(args: argparse.Namespace) -> int:
    cfg = Config()
    ciks = _ciks_for(args, cfg)
    scope = parse_scope(args.forms)
    dry_run = not args.write
    try:
        rep = render_universe(
            ciks, scope=scope, dry_run=dry_run, overwrite=args.overwrite,
            limit=args.limit, config=cfg,
        )
    except RuntimeError as exc:  # e.g. Chrome not installed
        print(f"render-pdf: {exc}", file=sys.stderr)
        return 1
    mode = "DRY-RUN (nothing written)" if dry_run else "WROTE"
    print(f"render-pdf [{mode}] — rendered={rep.rendered} would-render={rep.would_render} "
          f"skipped={rep.skipped} no-primary={rep.no_primary} errors={rep.errors}")
    if rep.error_items:
        print(f"  errors logged: {len(rep.error_items)} (see discovery_errors.jsonl)")
    return 0


def _cmd_xbrl(args: argparse.Namespace) -> int:
    cfg = Config()
    ciks = _ciks_for(args, cfg)
    years = _parse_years(args.years) if args.years else None
    since_year = min(years) if years else None
    dry_run = not args.write
    rep = fetch_financials(ciks, since_year=since_year, dry_run=dry_run, config=cfg)
    mode = "DRY-RUN (nothing written)" if dry_run else "WROTE"
    s = rep.stats
    print(f"xbrl [{mode}] — {rep.issuers} issuers, {rep.periods} period summaries (F1)")
    print(f"  seen={s.seen} added={s.added} updated={s.updated} unchanged={s.unchanged}")
    if rep.errors:
        print(f"  errors: {len(rep.errors)} (see discovery_errors.jsonl)")
    return 0


def _cmd_ownership(args: argparse.Namespace) -> int:
    cfg = Config()
    ciks = _ciks_for(args, cfg)
    scope = parse_scope(args.forms) if args.forms else None
    dry_run = not args.write
    rep = process_ownership(ciks, scope=scope, dry_run=dry_run, overwrite=args.overwrite,
                            limit=args.limit, config=cfg)
    mode = "DRY-RUN (nothing written)" if dry_run else "WROTE"
    print(f"ownership [{mode}] — {rep.issuers} issuers, downloaded={rep.downloaded}")
    print(f"  insider(E1)={rep.parsed_insider} 13F(E2)={rep.parsed_13f} "
          f"narrative(E3)={rep.passthrough} errors={rep.errors}")
    if rep.error_items:
        print(f"  errors logged: {len(rep.error_items)} (see discovery_errors.jsonl)")
    return 0


def _cmd_rag_items(args: argparse.Namespace) -> int:
    cfg = Config()
    ciks = None
    if args.universe:
        ciks = list(Universe(cfg).iter_ciks(args.universe))
    elif args.ciks:
        ciks = [c for c in args.ciks.split(",") if c.strip()]
    items = iter_items(
        ciks=ciks, doctypes=args.forms, year_min=args.year_min,
        year_max=args.year_max, prefer=args.prefer, config=cfg,
    )
    n = 0
    for it in items:
        n += 1
        if n <= args.show:
            p = it.payload
            print(f"{it.doc_id}  {p['doc_type']:<3} {p.get('year')}  {p['company']}  -> {it.path}")
    print(f"rag-items: {n} ingestible item(s) [prefer={args.prefer}]")
    return 0


def _cmd_report(args: argparse.Namespace) -> int:
    cfg = Config()
    ciks = _ciks_for(args, cfg)
    scope = parse_scope(args.forms)
    years = _parse_years(args.years)
    rows = build_matrix(ciks, years, scope, storage=Storage(cfg), config=cfg)

    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else [])
            writer.writeheader()
            writer.writerows(rows)
        print(f"wrote {len(rows)} matrix rows -> {args.csv}")
    tally = summarize(rows)
    print("completeness:", ", ".join(f"{k}={v}" for k, v in sorted(tally.items())) or "(empty)")
    return 0


def _cmd_entities(args: argparse.Namespace) -> int:
    reg = EntityRegistry(Config()).load()
    if args.cik:
        ent = reg.resolve(args.cik)
        if ent:
            print(f"{normalize_cik(args.cik)} -> entity '{ent.entity_id}' ({ent.name})")
            print(f"  CIKs: {', '.join(ent.ciks)}")
            if ent.note:
                print(f"  note: {ent.note}")
        else:
            print(f"{normalize_cik(args.cik)} -> standalone (no alias entry)")
        return 0
    ents = list(reg.entities())
    print(f"entities: {len(ents)}")
    for e in ents:
        print(f"  {e.entity_id:<16} {e.name}  [{', '.join(e.ciks)}]")
    return 0


def _cmd_discover_index(args: argparse.Namespace) -> int:
    cfg = Config()
    scope = parse_scope(args.forms)
    years = _parse_years(args.years)
    reg = EntityRegistry(cfg).load()

    cik_filter: set[str] | None = None
    if args.universe:
        cik_filter = set(reg.expand_all(Universe(cfg).iter_ciks(args.universe)))
    elif args.ciks:
        cik_filter = set(reg.expand_all(c for c in args.ciks.split(",") if c.strip()))
    elif not args.all:
        raise SystemExit("error: provide --universe, --ciks, or --all (full EDGAR)")

    dry_run = not args.write
    storage = Storage(cfg)
    src = EdgarFullIndex(fetcher=Fetcher(cfg), config=cfg)
    from .storage import SaveStats

    stats = SaveStats()
    for year in years:
        for quarter in (1, 2, 3, 4):
            recs = list(src.discover(year, quarter, scope=scope, ciks=cik_filter))
            for rec in recs:
                eid = reg.entity_id_for(rec.cik)
                if eid:
                    rec.entity_id = eid
            stats += storage.save_records(recs, dry_run=dry_run)
    if src.errors and not dry_run:
        storage.record_errors(src.errors)

    mode = "DRY-RUN (nothing written)" if dry_run else "WROTE manifests"
    span = f"{years[0]}-{years[-1]}" if years else "(none)"
    scope_note = "ALL filers" if cik_filter is None else f"{len(cik_filter)} CIKs"
    print(f"discover-index [{mode}] — years={span}, {scope_note}")
    print(f"  seen={stats.seen} added={stats.added} updated={stats.updated} unchanged={stats.unchanged}")
    if src.errors:
        print(f"  index errors: {len(src.errors)} (see discovery_errors.jsonl)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bottom_up_corpus")
    p.add_argument("--version", action="version", version=f"bottom_up_corpus {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    lf = sub.add_parser("list-forms", help="show the filing taxonomy (families A-F)")
    lf.add_argument("--forms", default="all", help="code/family selector, e.g. A,B or A1,B1")
    lf.set_defaults(func=_cmd_list_forms)

    cf = sub.add_parser("config", help="print the effective runtime configuration")
    cf.set_defaults(func=_cmd_config)

    bu = sub.add_parser("build-universe", help="resolve tickers/CIKs -> a curated list")
    bu.add_argument("--tickers", default="", help="comma-separated tickers, e.g. AAPL,MSFT")
    bu.add_argument("--ciks", default="", help="comma-separated CIKs (for delisted/historical issuers)")
    bu.add_argument("--name", default="curated", help="universe name (file stem)")
    bu.add_argument("--write", action="store_true", help="persist to data/universe/<name>.jsonl")
    bu.set_defaults(func=_cmd_build_universe)

    en = sub.add_parser("entities", help="list cross-CIK entities or resolve a CIK's entity")
    en.add_argument("--cik", default="", help="resolve a single CIK to its entity")
    en.set_defaults(func=_cmd_entities)

    dx = sub.add_parser("discover-index", help="exhaustive discovery via quarterly full-index (incl. delisted)")
    dxsrc = dx.add_mutually_exclusive_group(required=False)
    dxsrc.add_argument("--universe", help="restrict to a universe's CIKs (alias-expanded)")
    dxsrc.add_argument("--ciks", help="restrict to comma-separated CIKs (alias-expanded)")
    dx.add_argument("--all", action="store_true", help="index ALL filers (no CIK filter; very large)")
    dx.add_argument("--forms", default=None, help="scope selector (default: narrative A-D)")
    dx.add_argument("--years", default=None, help="year range, e.g. 2006-2025 (default: last 20)")
    dx.add_argument("--write", action="store_true", help="persist manifests (else dry-run)")
    dx.set_defaults(func=_cmd_discover_index)

    lu = sub.add_parser("list-universe", help="list curated universes or one universe's issuers")
    lu.add_argument("--name", default="", help="universe name; omit to list all")
    lu.set_defaults(func=_cmd_list_universe)

    di = sub.add_parser("discover", help="discover filings into manifests (dry-run by default)")
    src = di.add_mutually_exclusive_group(required=True)
    src.add_argument("--universe", help="universe name to crawl")
    src.add_argument("--ciks", help="comma-separated CIKs to crawl")
    di.add_argument("--forms", default=None, help="scope selector (default: narrative A-D)")
    di.add_argument("--years", default=None, help="year filter, e.g. 2006-2025 (default: last 20)")
    di.add_argument("--rounds", type=int, default=1, help="max convergence rounds")
    di.add_argument("--write", action="store_true", help="persist manifests (else dry-run)")
    di.add_argument("--download", action="store_true", help="also download+decompose (implies --write)")
    di.add_argument("--overwrite", action="store_true", help="re-download already-stored filings")
    di.add_argument("--limit", type=int, default=None, help="cap number of new downloads")
    di.set_defaults(func=_cmd_discover)

    dl = sub.add_parser("download", help="download+decompose filings from existing manifests")
    dlsrc = dl.add_mutually_exclusive_group(required=True)
    dlsrc.add_argument("--universe", help="universe name")
    dlsrc.add_argument("--ciks", help="comma-separated CIKs")
    dl.add_argument("--forms", default=None, help="scope selector (default: narrative A-D)")
    dl.add_argument("--write", action="store_true", help="persist files+manifest (else dry-run)")
    dl.add_argument("--overwrite", action="store_true", help="re-download already-stored filings")
    dl.add_argument("--limit", type=int, default=None, help="cap number of new downloads")
    dl.set_defaults(func=_cmd_download)

    rp = sub.add_parser("report", help="completeness matrix (issuer x form x year)")
    rsrc = rp.add_mutually_exclusive_group(required=True)
    rsrc.add_argument("--universe", help="universe name")
    rsrc.add_argument("--ciks", help="comma-separated CIKs")
    rp.add_argument("--forms", default=None, help="scope selector (default: narrative A-D)")
    rp.add_argument("--years", default=None, help="year range, e.g. 2006-2025 (default: last 20)")
    rp.add_argument("--csv", default="", help="optional CSV output path")
    rp.set_defaults(func=_cmd_report)

    rd = sub.add_parser("render-pdf", help="render downloaded primary docs to PDF (separate batch)")
    rdsrc = rd.add_mutually_exclusive_group(required=True)
    rdsrc.add_argument("--universe", help="universe name")
    rdsrc.add_argument("--ciks", help="comma-separated CIKs")
    rd.add_argument("--forms", default=None, help="scope selector (default: narrative A-D)")
    rd.add_argument("--write", action="store_true", help="render+persist (else dry-run)")
    rd.add_argument("--overwrite", action="store_true", help="re-render already-rendered filings")
    rd.add_argument("--limit", type=int, default=None, help="cap number of new renders")
    rd.set_defaults(func=_cmd_render_pdf)

    xb = sub.add_parser("xbrl", help="build per-period XBRL financial summaries (family F1)")
    xbsrc = xb.add_mutually_exclusive_group(required=True)
    xbsrc.add_argument("--universe", help="universe name")
    xbsrc.add_argument("--ciks", help="comma-separated CIKs")
    xb.add_argument("--years", default=None, help="keep periods with fiscal year >= min(years)")
    xb.add_argument("--write", action="store_true", help="persist summaries+facts (else dry-run)")
    xb.set_defaults(func=_cmd_xbrl)

    ow = sub.add_parser("ownership", help="download+structure ownership filings (family E)")
    owsrc = ow.add_mutually_exclusive_group(required=True)
    owsrc.add_argument("--universe", help="universe name (curated tier recommended)")
    owsrc.add_argument("--ciks", help="comma-separated CIKs")
    ow.add_argument("--forms", default="E", help="scope selector (default: E = E1,E2,E3)")
    ow.add_argument("--write", action="store_true", help="download+persist summaries (else dry-run)")
    ow.add_argument("--overwrite", action="store_true", help="re-download already-stored filings")
    ow.add_argument("--limit", type=int, default=None, help="cap number of new downloads")
    ow.set_defaults(func=_cmd_ownership)

    ri = sub.add_parser("rag-items", help="preview SourceItems the RAG would ingest")
    risrc = ri.add_mutually_exclusive_group(required=False)
    risrc.add_argument("--universe", help="universe name")
    risrc.add_argument("--ciks", help="comma-separated CIKs (default: all manifests)")
    ri.add_argument("--forms", default=None, help="scope selector (default: all)")
    ri.add_argument("--year-min", type=int, default=None, dest="year_min")
    ri.add_argument("--year-max", type=int, default=None, dest="year_max")
    ri.add_argument("--prefer", choices=["pdf", "text", "primary"], default="pdf",
                    help="which stored artifact to feed (default: pdf)")
    ri.add_argument("--show", type=int, default=10, help="how many items to print")
    ri.set_defaults(func=_cmd_rag_items)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)
