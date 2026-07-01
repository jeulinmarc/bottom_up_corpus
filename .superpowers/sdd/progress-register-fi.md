# 🇫🇮 FI PRH register — Subagent-Driven Progress Ledger (WORKTREE)

- **Worktree:** `/Users/marc/Desktop/All CODING/GENERALI/bu-fi` · **Branch:** `feat/register-financials-fi` · **BASE:** `58bf533`
- **Plan/Spec:** `docs/superpowers/{plans,specs}/2026-07-01-fi-prh-register*.md` (git-excluded)
- **GOVERNING:** NO FALSE DATA. **KEYLESS** (avoindata.prh.fi) → scale-validate autonomously. PARALLEL with LU (main copy).
- **PACK VALIDATED (5 real entities):** dimensional XBRL (fi_met:mi53 BS / md103 P&L, fi_dim:MCY member fi_MC:xNNN; current=no REF). Pack: revenue=x673 op_profit=x689 net_income=**x740(not x738!)** interest=abs(x4046) assets=x360 equity=x435 liabilities=x513 personnel=x1869 noncurrent=x376(can be neg) current=x424. SUPPRESS: income_tax(x448≠income tax), cash(x438 ambig), provisions, financial_debt(no borrowings split→liabilities-based). Gate: x360==x435+x513 primary; x376+x424==x360; x689+x12==x738 & x738+x541==x740. x583/x816=liab split (long/short unconfirmed → T2 resolves or suppresses). Fixtures: tests/fixtures/fi/{2919415-2_full, 0100379-9_abbrev, 0100843-4_housing}.xml.

## Tasks
- [ ] T1: stdlib dimensional parser (`fi_prh_xbrl.py`)
- [ ] T2: concept pack + gate (`concepts_fi.py`) — CRITICAL
- [ ] T3: FI identity (`identity.py` — Y-tunnus)
- [ ] T4: keyless PRH acquisition (`prh_api.py`)
- [ ] T5: producer + CLI
- [ ] T6: docs

## Completed
(none yet)

## Controller follow-ups
- Live SCALE validation (autonomous — keyless API): fetch N real cos, confirm x360==x435+x513, eyeball.
- whole-branch requesting-code-review (opus) → PR into main (Marc merges).
