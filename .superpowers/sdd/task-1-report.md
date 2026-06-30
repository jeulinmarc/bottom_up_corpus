# Task 1 Report — Optional dep + Arelle bridge

## What was created

- `bottom_up_corpus/eu/arelle_esef.py` — new module with `oim_from_esef_zip(zip_path, *, cntlr=None) -> dict`
- `tests/test_eu_arelle.py` — import-guard test (monkeypatches `builtins.__import__` to simulate Arelle absence)
- `pyproject.toml` — added `eu-financials = ["arelle-release"]` under `[project.optional-dependencies]`

## pyproject.toml edit

Added alongside the existing `be = ["curl_cffi>=0.7"]` entry, matching its style:

```toml
eu-financials = ["arelle-release"]
```

## TDD evidence

| Phase | Command | Result |
|-------|---------|--------|
| RED   | `./venv/bin/python -m pytest tests/test_eu_arelle.py -v` | `ERROR — ImportError: cannot import name 'arelle_esef'` |
| GREEN | `./venv/bin/python -m pytest tests/test_eu_arelle.py -v` | `1 passed in 0.02s` |

## Full-suite count

`619 passed in 0.79s` — no regressions.

## Files changed

| File | Change |
|------|--------|
| `bottom_up_corpus/eu/arelle_esef.py` | Created (64 lines) |
| `tests/test_eu_arelle.py` | Created (18 lines) |
| `pyproject.toml` | Added `eu-financials` optional extra (+3 lines) |

## Self-review checklist

- [x] Arelle imported ONLY inside `oim_from_esef_zip` (`from arelle import Cntlr` inside the function)
- [x] Module top is stdlib-only (`from __future__ import annotations`, `import zipfile`)
- [x] `pyproject.toml` extra added cleanly alongside `be`, no other deps changed
- [x] Focused test GREEN (1/1)
- [x] Full suite GREEN (619/619)
- [x] Commit message matches brief exactly

## Concerns

None. The module is verbatim from the brief (validated live during recon per the brief's note). Bridge correctness on real data is deferred to Task 4.

## Commit

`82b043a feat(eu): Arelle bridge — local ESEF .zip -> OIM-JSON (optional dep)`

---
**Status:** DONE
**Date completed:** 2026-06-30
**Branch:** `feat/eu-financials-arelle`

---

## Bridge fixes — 2026-06-30

Four targeted fixes applied after final review:

| Fix | File | Change |
|-----|------|--------|
| Skip nil facts | `arelle_esef.py` | Added `getattr(f, "isNil", False)` guard in fact loop — nil facts no longer become `""` values (Tier-A parity) |
| Per-share unit denominator | `arelle_esef.py` | `ModelUnit.measures` is `(numerator_qnames, denominator_qnames)`; now renders `"iso4217:EUR/xbrli:shares"` instead of discarding the denominator |
| Honest + gated coverage flag | `financials.py` | Captured `arelle_flat` before the union loop; `"arelle"` key in the `"ok"` coverage record is now only emitted when `use_arelle=True`, and its value reflects actual contribution (`bool(arelle_flat)`). Default `use_arelle=False` output is byte-identical to Phase 1. |
| Import order | `financials.py` | Moved `from .arelle_esef import oim_from_esef_zip` to its alphabetical position in the relative-import group (before `from .ifrs_concepts import ...`) |

### Test results

| Suite | Command | Result |
|-------|---------|--------|
| Focused | `pytest tests/test_eu_arelle.py tests/test_cli_eu_financials.py -v` | **5 passed** |
| Full | `pytest` | **622 passed in 0.80s** |

No test assertions were adjusted — all existing tests already matched the new behavior.

### Commit

`fix(eu): per-share unit denominator, skip nil facts, honest+gated coverage flag`
