# Task 5 — FI PRH Producer + CLI: Implementation Report

**Date:** 2026-07-01  
**Branch:** `feat/register-financials-fi`  
**Worktree:** `/Users/marc/Desktop/All CODING/GENERALI/bu-fi`

## What was built

### `registers/financials.py` additions
- `_YTUNNUS_RE` — regex `(\d{7}-\d)` to extract Y-tunnus from filename stems.
- `_fi_entity_id(path_obj)` — returns Y-tunnus from filename (e.g. `fi_2919415-2_full_2024.xml` → `"2919415-2"`); falls back to full stem.
- `_fi_pipeline(xbrl_source, entity_id, lei, name, *, storage, out, coverage, write)` — shared parse→map→emit tail for both local and API paths; mirrors `_be_pipeline`; handles `unbalanced` / empty-values / ok branches; calls `_emit_entity_rows` for the shared storage/coverage write.
- `build_fi_financials_from_files(paths, *, config, write=True)` — keyless local path: per-entity `try/except` isolation, coverage statuses (ok/unbalanced/no-financials/error), suppressed reasons forwarded.
- `build_fi_financials(specs, *, fetcher, config, write=True)` — API path: `resolve_register_specs` → `list_fi_dates` (latest date via `max()`) → `fetch_fi_financial` → same pipeline.

### `cli.py` additions
- Imported `build_fi_financials`, `build_fi_financials_from_files`.
- `--fi-file PATH …` (mutually exclusive group, FI keyless local) — dry-run default.
- `--fi-businessid Y_TUNNUS …` (mutually exclusive group, FI PRH open API, keyless) — dry-run default.
- Handlers inserted in `_cmd_register_financials` before the BE branches (no namespace collision with NO/UK/BE flags).

## Test outcomes

### Task 5 tests (7 new, all GREEN)
| Test | Assertion |
|------|-----------|
| `test_build_fi_financials_from_files_writes_jsonl` | `2919415-2.jsonl` written; equity=185,650.88; revenue=481,773.33; derived `roa`/`operating_margin`/`interest_coverage` present |
| `test_build_fi_financials_from_files_dry_run` | no file written; paths=[] |
| `test_build_fi_financials_from_files_error_isolation` | bad path → errors=1, good path → with_financials=1 |
| `test_build_fi_financials_api_stub` | stubbed fetcher → same rows; source="prh"; country="FI" |
| `test_cli_fi_file_dry_run` | rc=0; no file written |
| `test_cli_fi_file_write` | rc=0; file exists |
| `test_cli_fi_businessid_dry_run` | monkeypatched at `bottom_up_corpus.cli` level; calls=["2919415-2"]; no file |

### Full suite: **747 passed, 2 skipped** (baseline was 740 + 2 skipped)
- `test_register_fi.py`: 41/41 passed (34 pre-existing Tasks 1–4 + 7 new Task 5)
- `test_register_be.py`: 38/38 passed
- `test_register_no.py`: 22/22 passed
- `test_register_uk.py`: 21/21 passed (2 skipped — Arelle-dependent, unchanged)

## Key design decisions
- `_fi_entity_id` uses `_YTUNNUS_RE = re.compile(r"(\d{7}-\d)")` on the filename stem — authoritative, no XML re-parse.
- `build_fi_financials` uses `max(dates)` to pick the latest available date (ISO strings compare lexicographically).
- `monkeypatch.setattr(_cli, "build_fi_financials", ...)` patches the CLI's own imported reference (not the module where it's defined), matching how the CLI imports names.
