"""Shared register-producer primitives (DRY core for :mod:`.financials`).

The ~13 register producers in :mod:`bottom_up_corpus.registers.financials` share
an identical output contract — the same ``out`` summary dict, the same per-source
coverage-write tail, and (for the single-period producers) the same
``gate → classify → emit`` body.  Those primitives live here so the producers stay
thin and no new producer can accidentally diverge from the NO-FALSE-DATA gate
ordering.
"""
from __future__ import annotations

import json

from ..config import Config
from ..storage import _atomic_write_text


def _make_out() -> dict:
    """Fresh, zeroed producer summary dict (identical across every register).

    ``unbalanced`` is always present (some producers historically omitted it; it
    should always have been there — a balance-gate rejection is a distinct outcome
    from ``no_financials``).
    """
    return {
        "entities": 0, "with_financials": 0, "no_financials": 0,
        "unbalanced": 0, "errors": 0, "periods": 0, "paths": [],
    }


def _finalise_coverage(
    out: dict, coverage: list[dict], config: Config, source: str, *, write: bool,
) -> dict:
    """Write the per-source coverage file and stamp ``coverage_path`` onto ``out``.

    The coverage file is ``register_coverage_<source>.jsonl`` under
    ``reports/`` — one JSON object per line.  When ``write`` is False (dry-run)
    nothing is written and ``coverage_path`` is ``None``.  Returns ``out`` so
    producers can ``return _finalise_coverage(...)``.

    ``source`` is the coverage-file suffix (e.g. ``"brreg"``, ``"erst"``), which
    is not always the same as the row ``source`` tag — DK writes a single
    ``register_coverage_erst.jsonl`` for both ``erst-fsa`` and ``erst-ifrs`` rows.
    """
    if write:
        cov_path = config.data_dir / "reports" / f"register_coverage_{source}.jsonl"
        _atomic_write_text(
            cov_path, "\n".join(json.dumps(c, default=str) for c in coverage))
        out["coverage_path"] = str(cov_path)
    else:
        out["coverage_path"] = None
    return out
