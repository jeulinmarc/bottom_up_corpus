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
from .http import Fetcher
from .pipeline import discover_universe
from .storage import Storage
from .taxonomy import FULL_SCOPE, FormType, parse_scope
from .universe import Universe, resolve_tickers


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
    tickers = [t for t in args.tickers.split(",") if t.strip()]
    issuers, unresolved = resolve_tickers(tickers, Fetcher(cfg))
    if unresolved:
        print(f"WARNING: unresolved tickers: {', '.join(unresolved)}", file=sys.stderr)
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


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bottom_up_corpus")
    p.add_argument("--version", action="version", version=f"bottom_up_corpus {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    lf = sub.add_parser("list-forms", help="show the filing taxonomy (families A-F)")
    lf.add_argument("--forms", default="all", help="code/family selector, e.g. A,B or A1,B1")
    lf.set_defaults(func=_cmd_list_forms)

    cf = sub.add_parser("config", help="print the effective runtime configuration")
    cf.set_defaults(func=_cmd_config)

    bu = sub.add_parser("build-universe", help="resolve tickers -> CIKs and save a curated list")
    bu.add_argument("--tickers", required=True, help="comma-separated tickers, e.g. AAPL,MSFT")
    bu.add_argument("--name", default="curated", help="universe name (file stem)")
    bu.add_argument("--write", action="store_true", help="persist to data/universe/<name>.jsonl")
    bu.set_defaults(func=_cmd_build_universe)

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
    di.set_defaults(func=_cmd_discover)

    rp = sub.add_parser("report", help="completeness matrix (issuer x form x year)")
    rsrc = rp.add_mutually_exclusive_group(required=True)
    rsrc.add_argument("--universe", help="universe name")
    rsrc.add_argument("--ciks", help="comma-separated CIKs")
    rp.add_argument("--forms", default=None, help="scope selector (default: narrative A-D)")
    rp.add_argument("--years", default=None, help="year range, e.g. 2006-2025 (default: last 20)")
    rp.add_argument("--csv", default="", help="optional CSV output path")
    rp.set_defaults(func=_cmd_report)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)
