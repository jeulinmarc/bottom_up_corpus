"""Command-line interface for bottom_up_corpus.

Mirrors cb_corpus CLI ergonomics: subcommands, a dry-run-by-default posture, and
explicit ``--download`` / ``--write`` flags for side effects. Phase 0 wires up
the read-only inspection commands; discovery/download land in later phases.
"""

from __future__ import annotations

import argparse

from . import __version__
from .config import Config
from .taxonomy import FormType, FULL_SCOPE, parse_scope


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
    print(f"raw_dir           : {cfg.raw_dir}")
    if cfg.contact in cfg.user_agent and "@" not in cfg.contact:
        print("WARNING: set a real contact via BOTTOM_UP_CORPUS_CONTACT before crawling.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bottom_up_corpus")
    p.add_argument("--version", action="version", version=f"bottom_up_corpus {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    lf = sub.add_parser("list-forms", help="show the filing taxonomy (families A-F)")
    lf.add_argument("--forms", default="all", help="code/family selector, e.g. A,B or A1,B1 (default: all)")
    lf.set_defaults(func=_cmd_list_forms)

    cf = sub.add_parser("config", help="print the effective runtime configuration")
    cf.set_defaults(func=_cmd_config)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)
