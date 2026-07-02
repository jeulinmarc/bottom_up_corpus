# Task 2 Report — SK concept pack + NO-FALSE-DATA gate

**Status:** COMPLETE — TDD (11 T2 tests RED → GREEN); full suite green.

**Module:** `bottom_up_corpus/registers/concepts_sk.py` —
`map_sk_vykaz(vykaz, sablona)` calls T1's `parse_vykaz` internally, dispatches by
`idSablony` (699→`SK_POD_MAP`, 687→`SK_MUJ_MAP`, else→no-financials), returns
`{period_end, basis:"company", currency:"EUR", values, suppressed, unbalanced}`.

## Governing principle reproduced (to the cent)

**POD balance gate uses the accruals term (sk_36319007, 699):**
- assets 1 051 307 · equity 262 763 · liabilities 737 181 · accruals(r141) 51 363
- equity + liabilities alone = 999 944 (does NOT reach assets)
- equity + liabilities + accruals = 1 051 307 == assets → `unbalanced=False`.
  The POD-only accruals term is load-bearing; MUJ omits it.

**revenue = operating_revenue_total, NOT net_turnover (POD):**
- revenue (r2, *Výnosy z hospodárskej činnosti spolu*) = 2 005 372
- net_turnover (r1, *Čistý obrat*) = 1 950 307 → NEVER mapped; recorded as an
  explicit `("net_turnover", …)` suppression.

**Borrowings-based leverage (present + nonzero bank-loan lines):**
- POD: long_term_debt (r121) 150 722 + short_term_debt (r139) 155 854 emitted;
  interest_expense (r49) 21 002 positive as-is (no abs).
- POD nodebt (sk_50296353): both bank-loan lines empty → debt block suppressed,
  equity 8 627 / assets 10 152 still emitted, gate holds (8 627 + 1 525 = 10 152).
- MUJ (sk_54953006, 687): long_term_debt (r37) 16 303 emitted; short_term_debt
  (r44) absent → suppressed; gate `assets 88 449 == equity 6 240 + liabilities
  82 209` (no accruals term).
- `leverage_basis="borrowings"` is stamped by the producer (`_emit_entity_rows`),
  NOT here.

**Column rule:** POD table 0 (4-col assets) → col index 2 (netto-current); every
other (table, cislo) → col 0. Helper `cell(cells, (ti, cislo), col)`.

## Gate / suppression

`tol = max(2, 0.005·|assets|)`. Synthetic assets ≠ equity+liab(+accruals) beyond
tol → `unbalanced=True`, empty values. `idSablony=695` (IFRS) and
`pristupnostDat="Neverejné"` → no-financials (empty values, `("__all__", reason)`,
`unbalanced=False`). period_end derived from `titulnaStrana.obdobieDo`
(`"2023-12"` → month-end `"2023-12-31"`); else None (producer supplies it).

**Interpretation:** `period_end` normalizes `YYYY-MM` → last-day-of-month
(statutory periods close at month-end — deterministic, not inferred data).
`net_turnover` is recorded in `suppressed` (not a curated key) to keep the
no-false-data decision auditable. Engine internals untouched.

**Full suite:** 900 passed, 2 skipped (pre-existing; NO/UK/BE/LU/FI/DK/EE intact).

**Report path:** `.superpowers/sdd/task-2-report.md`

---

## Review fixes — commit fec8023

**Status:** COMPLETE — all 3 review fixes applied and green; full suite 917 passed, 2 skipped.

**Fix 1 (IMPORTANT — NO FALSE DATA): template-match guard in `map_sk_vykaz`**
`sablona.get("id") != vykaz.get("idSablony")` → return no-financials immediately (before
`parse_vykaz` is called) with suppression `("__all__", "sablona/vykaz idSablony mismatch: ...")`.
Tested: `sk_54953006_MUJ.json` (idSablony=687) + `sk_sablona_699.json` (id=699) → `values=={}`,
`unbalanced=False`, `"mismatch"` in suppressed reason — NOT a set of emitted numbers.
`_synth_pod` / `_synth_meta` helpers updated to carry `"id": sablony` on the sablona so
existing synthetic tests still hit the correct (matching) path.

**Fix 2 (MINOR): `parse_vykaz` robust to malformed records**
`vykaz["obsah"]["titulnaStrana"]` → `.get()` chains: `obsah = vykaz.get("obsah") or {}`,
`ico = (obsah.get("titulnaStrana") or {}).get("ico")`, `tabulky = obsah.get("tabulky", [])`,
`id_sablony = vykaz.get("idSablony")`. Malformed vykaz (no titulnaStrana, 0 tables) returns
`cells=={}` without raising; downstream classifies as no-financials (not error).

**Fix 3 (MINOR): fixture fidelity — remove injected `pocetDatovychStlpcov` from vykaz fixtures**
Removed `pocetDatovychStlpcov` from every table in `obsah.tabulky[]` of all 3 vykaz fixtures
(`sk_36319007_POD.json`, `sk_50296353_POD_nodebt.json`, `sk_54953006_MUJ.json`). Sablona
fixtures untouched (authoritative ncols source). Cent-accurate values (assets 1 051 307,
equity 262 763, MUJ assets 88 449 etc.) confirmed unchanged — parser reads ncols from sablona.

**Full suite:** 917 passed, 2 skipped (NO/UK/BE/LU/FI/DK/EE + all prior SK tests intact).
