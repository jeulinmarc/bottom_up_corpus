# Task 7 Report — DK Virk register user doc

**Status:** DONE

**Commit:** `8f45293` — `docs(registers): document the DK Virk register`

**Full-suite count:** 839 passed, 2 skipped (docs-only change, no test impact)

**What was done:**
- Bumped intro register count from five to six; added DK to the covered-registers list.
- Added full DK section to `docs/REGISTER_FINANCIALS.md` (343 lines net) covering:
  source (Virk Regnskaber, keyless POST, gzip magic caveat, ~4M filings),
  two-path table (Path A ESEF/IFRS stdlib reusing IFRS_CONCEPTS → borrowings-based;
  Path B DK-GAAP FSA stdlib → liabilities-based), schema table, FSA concept pack table,
  confidence gate (§32 / GrossProfitLoss≠revenue, primary balance gate, derived
  liabilities, maturity-split atomic gate), basis field, CVR+LEI identity, CLI examples,
  and honest caveats (DKK currency, class-B liabilities-based, class-C/D fast-follow,
  revenue absent for §32 filers, GAAP separation).
- Removed Denmark from the "Out of scope" list; updated covered-registers summary sentence.
- Cross-links to NO/UK/BE/LU/FI + FINANCIALS.md + EU_FINANCIALS.md added at section end.

**Concerns:** None. All 839 tests pass.

**Report path:** `.superpowers/sdd/task-7-report.md`

---

# Task 7 Addendum — False-data audit fixes (segment dimension + EUR unit)

**Status:** DONE

**Commit:** `54201ad` -- `fix(registers): DK parsers exclude segment dimensions + value unit follows currency (false-data audit)`

**Fix 1 (segment dimension):** `dk_fsa_xbrl._classify_context` and `concepts_dk.parse_virk_esef_xml` now check `xbrli:segment` (inside `xbrli:entity`) in addition to `xbrli:scenario`; a context is dimensioned when either container has element children. Segment-dimensioned fact Assets=999999 excluded in both parsers; solo/consolidated detection also covers segment.

**Fix 2 (EUR unit):** `map_fsa_facts` `emit()` sets `unit=currency` (detected ISO-4217 code) instead of hardcoded `"DKK"`. ARL s.16 EUR filings now carry `unit="EUR"` on each value.

**Segment exclusion confirmed:** synthetic ESEF XML (segment-dim Assets=999999 first in document order + no-dim Assets=1000000) -> only 1000000 kept; FSA analog same (segment fact listed first to avoid first-wins loophole). EUR->unit=EUR confirmed; DKK non-regression passes.

**5 real fixtures unaffected:** all real DK filings use `xbrli:scenario`; segment check is additive.

**Full-suite count:** 844 passed, 2 skipped (5 new DK tests added).

**Report path:** `.superpowers/sdd/task-7-report.md`
