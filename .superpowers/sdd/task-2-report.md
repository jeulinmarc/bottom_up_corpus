# Task 2 Report — FI PRH concept pack + NO-FALSE-DATA gate

**Status:** DONE — all tests green (RED→GREEN, TDD).

**Files:** `bottom_up_corpus/registers/concepts_fi.py` (new, `map_fi_facts`),
`tests/test_register_fi.py` (+13 Task-2 tests). `financials.py` untouched.

## Key values reproduced (fi_2919415-2_full_2024, to the cent)
- **net_income = x740 (57 560.30), NOT x738 (72 574.02)** — the appropriations
  trap. Waterfall verified `x738 + x541 (−15 013.72) = x740` before emitting;
  net_income is never x738.
- revenue x673 = 481 773.33 · total_assets x360 = 201 064.55 ·
  equity x435 = 185 650.88 (== x360 − x513 = 201 064.55 − 15 413.67) ·
  interest_expense = abs(x4046) = 0.10.
- **Primary balance** `x360 == x435 + (x513 or 0)` holds → `unbalanced=False`.
- abbrev (0100379-9): revenue absent (suppressed), equity/assets present, gate holds.
- housing (0100843-4): non_current x376 = **−59 015.40** accepted (no positivity
  check); `x376 + x424 == x360` holds.

## x583/x816 leverage decision — SUPPRESSED (never guessed)
Recon proved the PRH instances carry **no label linkbase / roleRef** and point to
an **external** taxonomy (`oytp_gaap_ind.xsd`, not shipped) — so which of x583/x816
is long- vs short-term is **unconfirmable from the data** (values don't
disambiguate either). Per NO-FALSE-DATA I **suppress the maturity split and
suppress engine leverage** (`long_term_debt`/`short_term_debt`) rather than emit a
guessed split, and rather than dump all of x513 into one bucket (an equally-false
"all one maturity" claim). The confirmed **total** `liabilities` (x513) is still
emitted → leverage is liabilities-based via the total. The reconciliation gate
(`|x583+x816 − x513| ≤ tol`) is implemented and yields the specific suppression
reason; on the full fixture it reconciles yet is still suppressed for the
label-unconfirmed reason. Always-suppressed: income_tax, cash, financial_debt,
provisions.

**Full-suite count:** 726 passed, 2 skipped (pre-existing optional-dep skips) —
0 failed/errors.

**Concern:** FI emits no `long_term_debt` → the engine computes no `total_debt`
and hence no `debt_to_equity`/`debt_to_assets` for FI. This is the deliberate,
honest cost of not guessing maturities; a downstream user can still derive
liabilities/equity from the emitted `liabilities` + `equity`. If an authoritative
PRH codelist later confirms the x583/x816 assignment, the split can be enabled in
`concepts_fi.py` under the already-present reconciliation gate.

**Report path:** `.superpowers/sdd/task-2-report.md`

---

## Review fixes — commit 6553eaa

**Status:** DONE — 728 passed, 2 skipped, 0 failed.

**Commit:** `6553eaa` — `fix(registers): FI canonical concept keys (restore roa/op_margin/interest_coverage) + P&L waterfall leg 1 + honest docstring`

### I1 (canonical concept keys)
Renamed in `FI_PACK`, `_GATED`, the asset-components loop, and all test
assertions: `total_assets→assets`, `operating_profit→operating_income`,
`current_assets→assets_current`. `compute_derived` now receives the engine's
canonical keys and produces `roa`, `operating_margin`, `interest_coverage` on the
full_2024 fixture (previously silently skipped). New test
`test_compute_derived_produces_roa_operating_margin_interest_coverage` proves all
three are present. `personnel_costs` kept (non-engine extra row, harmless).

### I2 (P&L waterfall leg 1)
Added leg 1: when x12 (net financial items) and x738 are both present, require
`|x689 + x12 − x738| ≤ tol`; failure suppresses `net_income` with a "leg-1 fails"
reason. All three real fixtures reconcile exactly (full 0.00, abbrev 0.00, housing
0.00 delta). New test `test_synthetic_pnl_leg1_failure_suppresses_net_income`
proves a synthetic mismatch (x689=100k + x12=50k ≠ x738=200k) suppresses
`net_income` even when leg 2 would pass alone.

### M1 (docstring honesty)
Removed "leverage stays liabilities-based via x513" from the module docstring and
the inline `lev_reason` string. Replacement text: FI emits `liabilities` as a raw
reported value but produces **no** `total_debt`/`debt_to_equity` (the engine's
leverage ratios require the suppressed long_term_debt/short_term_debt split); a
codelist confirming the assignment would re-enable it.

**Concern (unchanged):** FI still emits no `long_term_debt` → no engine leverage
ratios for FI filings. Honest and deliberate; a future PRH codelist can re-enable
under the existing reconciliation gate.
