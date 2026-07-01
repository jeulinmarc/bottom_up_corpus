"""Tests for the DK register.

Task 1 — stdlib DK-GAAP FSA XBRL parser (``dk_fsa_xbrl.parse_fsa_facts``).
Task 2 — DK-GAAP concept pack + NO-FALSE-DATA gate (``concepts_dk.map_fsa_facts``).

Fixtures — all real, provenance-clean DK-GAAP class-B filings served verbatim by
Virk/ERST (``distribution.virk.dk`` -> ``regnskaber.virk.dk``):

  * ``dk_30830725_microB_2025`` — NIPE FINANS ApS, micro-B, FY ending 2025-09-30.
  * ``dk_30560000_2024``        — K.Kirch ApS, FY ending 2025-12-31.
  * ``dk_42566551_2025``        — Sneaks2Peak ApS, a FY2025 wind-down. Its
    ``fsa:Provisions`` (15,844) and ``fsa:Revenue`` (36,536) are tagged ONLY in
    the 2024 comparative; the 2025 current period settled them. This is the
    regression fixture for the period-mixing bug: the fixed parser must NOT leak
    those prior-only facts into the current-period view.
  * ``dk_42710644_provisions``  — Taxikørsel 011 ApS, class B, FY2025. Its
    CURRENT period carries ``fsa:Provisions`` (4,469) > 0 — the honest fixture
    for the provisions-in-liabilities branch (prior year was a different 24,475,
    so a period-mixed parse would break the reconciliation).
"""
from bottom_up_corpus.registers.dk_fsa_xbrl import parse_fsa_facts
from bottom_up_corpus.registers.concepts_dk import map_fsa_facts

MICROB = "tests/fixtures/dk/dk_30830725_microB_2025.xml"
STANDARD = "tests/fixtures/dk/dk_30560000_2024.xml"
WINDDOWN = "tests/fixtures/dk/dk_42566551_2025.xml"
PROVISIONS = "tests/fixtures/dk/dk_42710644_provisions.xml"


def _suppressed_keys(result):
    return {k for k, _ in result["suppressed"]}


def _reason(result, key):
    return dict(result["suppressed"]).get(key)


# ===========================================================================
# Task 1 — parser
# ===========================================================================

def test_parse_fsa_facts_microb_2025():
    """parse_fsa_facts on the micro-B 2025 fixture returns correct BS figures."""
    result = parse_fsa_facts(MICROB)

    assert result["currency"] == "DKK"
    assert result["period_end"] == "2025-09-30"

    facts = result["facts"]

    # Balance-sheet figures (instant c4=2025-09-30, not prior-year c5=2024-09-30)
    assert facts["Assets"] == 6744.0
    assert facts["Equity"] == -585256.0
    assert facts["LiabilitiesAndEquity"] == 6744.0
    assert facts["CashAndCashEquivalents"] == 1744.0


def test_parse_current_period_only_excludes_prior_only_facts():
    """REGRESSION (no period-mixing): the parser selects the CURRENT reporting
    period only and never leaks a prior-period-only fact.

    dk_42566551 is a real FY2025 wind-down. fsa:Provisions (15,844) and
    fsa:Revenue (36,536) are tagged ONLY in the 2024 comparative context; the
    2025 current period settled them. The fixed parser therefore OMITS both from
    the current-period facts (never defaulted, never leaked from the prior year),
    and the current row balances on its own.
    """
    result = parse_fsa_facts(WINDDOWN)

    assert result["currency"] == "DKK"
    assert result["period_end"] == "2025-12-31"      # current balance-sheet date

    facts = result["facts"]

    # The heart of the bug: a fact present ONLY in the prior-year context must be
    # ABSENT from the current-period view — not leaked, not defaulted.
    assert "Provisions" not in facts
    assert "Revenue" not in facts

    # Current-period (2025) figures — internally consistent for one period alone.
    assert facts["Assets"] == 77480.0
    assert facts["LiabilitiesAndEquity"] == 77480.0
    assert facts["Equity"] == 77480.0
    assert facts["Assets"] == facts["LiabilitiesAndEquity"]   # balances alone
    assert facts["CashAndCashEquivalents"] == 5462.0
    assert facts["ProfitLoss"] == -2981.0
    assert facts["GrossProfitLoss"] == -2981.0


def test_parse_provisions_selects_current_period_value():
    """The provisions fixture tags fsa:Provisions in BOTH years (current 4,469,
    prior 24,475). The fixed parser returns the CURRENT 4,469 — never the prior
    24,475 — and the current balance sheet reconciles."""
    facts = parse_fsa_facts(PROVISIONS)["facts"]
    assert facts["Provisions"] == 4469.0
    assert facts["Assets"] == 279025.0
    assert facts["LiabilitiesAndEquity"] == 279025.0
    assert facts["Assets"] == facts["LiabilitiesAndEquity"]


# ===========================================================================
# Task 2 — concept pack + NO-FALSE-DATA gate
# ===========================================================================

def test_map_microb_shape_and_currency():
    """The micro-B mapping returns the sibling shape: company basis, DKK, DKK units."""
    r = map_fsa_facts(parse_fsa_facts(MICROB))
    assert r["basis"] == "company"
    assert r["currency"] == "DKK"
    assert r["period_end"] == "2025-09-30"
    assert r["unbalanced"] is False
    # Every emitted value carries unit DKK, a label, and an fsa: tag.
    for key, v in r["values"].items():
        assert v["unit"] == "DKK"
        assert v["label"]
        assert v["tag"].startswith("fsa:") or "derived" in v["tag"]


def test_map_microb_core_values():
    """dk_30830725: assets 6,744 · equity -585,256 · liabilities 592,000 (derived)
    · net_income = fsa:ProfitLoss (-300) · gate holds."""
    r = map_fsa_facts(parse_fsa_facts(MICROB))
    v = r["values"]
    assert v["assets"]["value"] == 6744.0
    assert v["assets"]["tag"] == "fsa:Assets"
    assert v["equity"]["value"] == -585256.0
    assert v["liabilities"]["value"] == 592000.0        # LiabilitiesAndEquity - Equity
    assert "derived" in v["liabilities"]["tag"].lower()
    assert v["net_income"]["value"] == -300.0           # real fsa:ProfitLoss
    assert v["net_income"]["tag"] == "fsa:ProfitLoss"
    assert v["cash"]["value"] == 1744.0


def test_map_microb_revenue_suppressed_gross_profit_is_not_revenue():
    """§32: fsa:Revenue is absent on the micro-B filing -> revenue SUPPRESSED,
    and GrossProfitLoss (-300) is mapped to gross_profit, never to revenue."""
    r = map_fsa_facts(parse_fsa_facts(MICROB))
    assert "revenue" not in r["values"]
    assert "revenue" in _suppressed_keys(r)
    assert r["values"]["gross_profit"]["value"] == -300.0
    assert r["values"]["gross_profit"]["tag"] == "fsa:GrossProfitLoss"
    # The GrossProfitLoss value must not have leaked into revenue.
    assert "revenue" not in r["values"]


def test_map_microb_short_term_debt_reconciles_alone():
    """Micro-B carries only ShorttermLiabilitiesOtherThanProvisions (592,000),
    which alone reconciles to derived liabilities (no provisions) -> emit
    short_term_debt; long_term_debt stays absent (never synthesised)."""
    r = map_fsa_facts(parse_fsa_facts(MICROB))
    assert r["values"]["short_term_debt"]["value"] == 592000.0
    assert r["values"]["short_term_debt"]["tag"] == \
        "fsa:ShorttermLiabilitiesOtherThanProvisions"
    assert "long_term_debt" not in r["values"]
    assert "long_term_debt" in _suppressed_keys(r)
    # Borrowings-by-instrument is never fabricated for class-B filers.
    assert "financial_debt" in _suppressed_keys(r)


def test_map_winddown_current_period_has_no_provisions_or_revenue():
    """dk_42566551 (FY2025 wind-down): the RESTORED real filing's current period.
    Gate holds; derived liabilities are 0 (LiabilitiesAndEquity 77,480 − Equity
    77,480 — every liability was settled); provisions and revenue are ABSENT
    because they belong only to the settled prior year. Absent/zero provisions in
    the current period is the truth — assert it (never the leaked prior 15,844)."""
    r = map_fsa_facts(parse_fsa_facts(WINDDOWN))
    v = r["values"]
    assert r["unbalanced"] is False
    assert r["period_end"] == "2025-12-31"
    assert v["assets"]["value"] == 77480.0
    assert v["equity"]["value"] == 77480.0
    assert v["liabilities"]["value"] == 0.0             # all liabilities settled
    assert v["net_income"]["value"] == -2981.0
    assert v["gross_profit"]["value"] == -2981.0
    # Provisions is a prior-year-only fact -> never emitted for the current period.
    assert "provisions" not in v
    # Revenue tagged only in the prior year -> suppressed (§32 + prior-only).
    assert "revenue" not in v
    assert "revenue" in _suppressed_keys(r)


def test_map_provisions_derived_liabilities_capture_provisions():
    """dk_42710644 (Taxikørsel 011 ApS, FY2025): the CURRENT period carries
    fsa:Provisions (4,469). Derived liabilities (LiabilitiesAndEquity 279,025 −
    Equity 83,118 = 195,907) capture provisions automatically, and provisions is
    also surfaced as its own reported value; gate holds."""
    r = map_fsa_facts(parse_fsa_facts(PROVISIONS))
    v = r["values"]
    assert r["unbalanced"] is False
    assert r["period_end"] == "2025-12-31"
    assert v["assets"]["value"] == 279025.0
    assert v["equity"]["value"] == 83118.0
    assert v["liabilities"]["value"] == 195907.0         # captures provisions 4,469
    assert v["provisions"]["value"] == 4469.0
    assert v["provisions"]["tag"] == "fsa:Provisions"
    # Provisions are inside the derived total liabilities.
    assert v["liabilities"]["value"] >= v["provisions"]["value"]
    assert v["net_income"]["value"] == 63109.0


def test_map_provisions_split_reconciles_with_provisions():
    """The lone short bucket (191,438) + provisions (4,469) reconcile to the
    derived liabilities (195,907) -> emit short_term_debt; provisions stay a
    SEPARATE reported value, NOT counted as debt; no long bucket -> not emitted.

    This reconciliation holds ONLY because provisions is the CURRENT 4,469 (not
    the prior-year 24,475) — a period-mixed parse would break it (191,438 +
    24,475 = 215,913 != 195,907)."""
    r = map_fsa_facts(parse_fsa_facts(PROVISIONS))
    assert r["values"]["short_term_debt"]["value"] == 191438.0
    assert "long_term_debt" not in r["values"]
    # Provisions reported separately, not merged into the debt keys.
    assert r["values"]["provisions"]["value"] == 4469.0


def test_map_revenue_emitted_when_directly_tagged():
    """When fsa:Revenue IS tagged, revenue is emitted from it — the §32
    suppression fires only when fsa:Revenue is absent. Uses a synthetic parsed
    dict: every real DK class-B fixture here presents Bruttoresultat and omits
    fsa:Revenue in the current period, so this branch has no real fixture."""
    parsed = {"period_end": "2025-12-31", "currency": "DKK",
              "facts": {"Assets": 1000.0, "LiabilitiesAndEquity": 1000.0,
                        "Equity": 400.0, "Revenue": 5000.0,
                        "GrossProfitLoss": 250.0}}
    r = map_fsa_facts(parsed)
    assert r["values"]["revenue"]["value"] == 5000.0
    assert r["values"]["revenue"]["tag"] == "fsa:Revenue"
    # GrossProfitLoss keeps its own key and is never conflated with revenue.
    assert r["values"]["gross_profit"]["value"] == 250.0


def test_map_standard_gate_and_net_income():
    """dk_30560000 (FY2025): gate holds; net_income present; real equity/assets."""
    r = map_fsa_facts(parse_fsa_facts(STANDARD))
    v = r["values"]
    assert r["unbalanced"] is False
    assert v["assets"]["value"] == 100000.0
    assert v["equity"]["value"] == -46307.0
    assert v["liabilities"]["value"] == 146307.0         # 100000 - (-46307)
    assert v["net_income"]["value"] == 7994.0
    assert v["short_term_debt"]["value"] == 146307.0     # reconciles alone


def test_map_unbalanced_emits_no_values():
    """Assets != LiabilitiesAndEquity beyond tol -> unbalanced, empty values."""
    parsed = {"period_end": "2024-12-31", "currency": "DKK",
              "facts": {"Assets": 1000.0, "LiabilitiesAndEquity": 1200.0,
                        "Equity": 300.0, "ProfitLoss": 50.0}}
    r = map_fsa_facts(parsed)
    assert r["unbalanced"] is True
    assert r["values"] == {}
    assert "__all__" in _suppressed_keys(r)


def test_map_no_maturity_split_suppresses_debt():
    """A balanced filing with no maturity buckets -> short/long debt suppressed
    (never map a lone total as long_term_debt)."""
    parsed = {"period_end": "2024-12-31", "currency": "DKK",
              "facts": {"Assets": 1000.0, "LiabilitiesAndEquity": 1000.0,
                        "Equity": 400.0}}
    r = map_fsa_facts(parsed)
    assert r["unbalanced"] is False
    assert r["values"]["liabilities"]["value"] == 600.0
    assert "short_term_debt" not in r["values"]
    assert "long_term_debt" not in r["values"]
    assert "short_term_debt" in _suppressed_keys(r)


def test_map_two_bucket_split_reconciles_emits_both():
    """When both correctly-labelled maturity buckets are present and reconcile
    to derived liabilities, emit BOTH short_term_debt and long_term_debt."""
    parsed = {"period_end": "2024-12-31", "currency": "DKK",
              "facts": {"Assets": 1000.0, "LiabilitiesAndEquity": 1000.0,
                        "Equity": 400.0,
                        "ShorttermLiabilitiesOtherThanProvisions": 250.0,
                        "LongtermLiabilitiesOtherThanProvisions": 350.0}}
    r = map_fsa_facts(parsed)
    assert r["values"]["short_term_debt"]["value"] == 250.0
    assert r["values"]["long_term_debt"]["value"] == 350.0
    assert r["values"]["long_term_debt"]["tag"] == \
        "fsa:LongtermLiabilitiesOtherThanProvisions"


def test_map_split_not_reconciling_suppresses_debt():
    """A partial maturity bucket that does NOT reconcile to derived liabilities
    -> suppress the split (no false maturity claim)."""
    parsed = {"period_end": "2024-12-31", "currency": "DKK",
              "facts": {"Assets": 1000.0, "LiabilitiesAndEquity": 1000.0,
                        "Equity": 400.0,
                        "ShorttermLiabilitiesOtherThanProvisions": 100.0}}
    r = map_fsa_facts(parsed)
    assert "short_term_debt" not in r["values"]
    assert "long_term_debt" not in r["values"]


# ===========================================================================
# Task 3 — Virk ESEF bare-XBRL parser (reuses IFRS engine, borrowings leverage)
# ===========================================================================
ESEF_SLICE = "tests/fixtures/dk/dk_esef_slice.xml"


def test_parse_virk_esef_xml_returns_flat_shape():
    """parse_virk_esef_xml returns {local_name: [datapoint]} matching oim.flatten_oim_json shape.

    Instant facts (balance-sheet): val, end, unit, tag, label, filed, form, accn — no 'start'.
    Duration facts (P&L): same keys plus 'start'.
    """
    from bottom_up_corpus.registers.concepts_dk import parse_virk_esef_xml

    flat = parse_virk_esef_xml(open(ESEF_SLICE, "rb").read())

    # ifrs-full local names present
    assert "Assets" in flat
    assert "Equity" in flat
    assert "Revenue" in flat
    assert "ProfitLoss" in flat
    assert "NoncurrentBorrowings" in flat
    assert "CurrentBorrowings" in flat
    assert "CashAndCashEquivalents" in flat

    # Instant fact: no 'start'; unit = DKK; correct value
    assets_pts = flat["Assets"]
    assert len(assets_pts) == 1          # only no-dim context (dim context excluded)
    pt = assets_pts[0]
    assert pt["val"] == 1_000_000.0
    assert pt["end"] == "2025-12-31"
    assert "start" not in pt
    assert pt["unit"] == "DKK"
    assert pt["tag"] == "Assets"
    assert pt["label"] == "Assets"
    assert "filed" in pt
    assert "form" in pt
    assert "accn" in pt

    # Duration fact: has 'start'
    rev_pts = flat["Revenue"]
    assert len(rev_pts) == 1
    rp = rev_pts[0]
    assert rp["val"] == 2_000_000.0
    assert rp["start"] == "2025-01-01"
    assert rp["end"] == "2025-12-31"
    assert rp["unit"] == "DKK"


def test_parse_virk_esef_xml_skips_dimension_contexts():
    """Contexts with xbrli:scenario children (dimensioned) are excluded — no double-counting."""
    from bottom_up_corpus.registers.concepts_dk import parse_virk_esef_xml

    flat = parse_virk_esef_xml(open(ESEF_SLICE, "rb").read())
    # The slice fixture has a dimensioned context with Assets=999999 that must be ignored.
    assert len(flat.get("Assets", [])) == 1


def test_summaries_from_flat_yields_borrowings_debt_to_equity():
    """summaries_from_flat + IFRS_CONCEPTS yields assets/equity/revenue + borrowings-based
    debt_to_equity: (NoncurrentBorrowings + CurrentBorrowings) / Equity."""
    from bottom_up_corpus.registers.concepts_dk import parse_virk_esef_xml
    from bottom_up_corpus.eu.ifrs_concepts import IFRS_CONCEPTS
    from bottom_up_corpus.financials import summaries_from_flat

    flat = parse_virk_esef_xml(open(ESEF_SLICE, "rb").read())
    summaries = summaries_from_flat(
        flat, concepts=IFRS_CONCEPTS,
        company="Test DK ESEF Co", company_current="Test DK ESEF Co",
        sic=None,
    )
    assert len(summaries) >= 1
    s = summaries[0]
    assert s.currency == "DKK"

    v = s.values
    assert v["assets"]["value"] == 1_000_000.0
    assert v["equity"]["value"] == 600_000.0
    assert v["revenue"]["value"] == 2_000_000.0
    assert v["net_income"]["value"] == 200_000.0

    # Borrowings-based debt_to_equity via s.derived:
    # total_debt = long_term_debt + short_term_debt = 300,000 + 100,000 = 400,000
    # debt_to_equity = 400,000 / 600,000 ≈ 0.6667
    d = s.derived
    assert "debt_to_equity" in d, f"derived keys: {list(d)}"
    assert abs(d["debt_to_equity"]["value"] - (400_000 / 600_000)) < 0.001


def test_map_dk_esef_balance_gate_holds():
    """map_dk_esef verifies Assets == Equity + Liabilities and returns PeriodSummary list."""
    from bottom_up_corpus.registers.concepts_dk import map_dk_esef

    summaries = map_dk_esef(open(ESEF_SLICE, "rb").read())
    assert len(summaries) >= 1
    s = summaries[0]
    v = s.values

    # Balance gate: Assets = 1,000,000 = Equity 600,000 + Liabilities 400,000
    assets = v["assets"]["value"]
    equity = v["equity"]["value"]
    liabilities = v["liabilities"]["value"]
    assert abs(assets - (equity + liabilities)) < max(2.0, 0.005 * abs(assets))

    # Borrowings-based leverage is present in derived
    assert "debt_to_equity" in s.derived
    assert s.currency == "DKK"
