"""Tests for the DK register.

Task 1 — stdlib DK-GAAP FSA XBRL parser (``dk_fsa_xbrl.parse_fsa_facts``).
Task 2 — DK-GAAP concept pack + NO-FALSE-DATA gate (``concepts_dk.map_fsa_facts``).
"""
import pytest
from bottom_up_corpus.registers.dk_fsa_xbrl import parse_fsa_facts
from bottom_up_corpus.registers.concepts_dk import map_fsa_facts

MICROB = "tests/fixtures/dk/dk_30830725_microB_2025.xml"
PROVISIONS = "tests/fixtures/dk/dk_42566551_provisions_2024.xml"
STANDARD = "tests/fixtures/dk/dk_30560000_2024.xml"


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


def test_map_provisions_derived_liabilities_capture_provisions():
    """dk_42566551 (FY2024): assets 107,347 · equity 83,832 · liabilities 23,515
    (= 107,347 - 83,832, includes provisions 15,844) · gate exact."""
    r = map_fsa_facts(parse_fsa_facts(PROVISIONS))
    v = r["values"]
    assert r["unbalanced"] is False
    assert v["assets"]["value"] == 107347.0
    assert v["equity"]["value"] == 83832.0
    assert v["liabilities"]["value"] == 23515.0          # captures provisions 15,844
    assert v["provisions"]["value"] == 15844.0
    assert v["provisions"]["tag"] == "fsa:Provisions"
    assert v["net_income"]["value"] == -34060.0


def test_map_provisions_split_reconciles_with_provisions():
    """The lone short bucket (7,671) + provisions (15,844) reconcile to the
    derived liabilities (23,515) -> emit short_term_debt; provisions stay a
    separate reported value, NOT counted as debt; no long bucket -> not emitted."""
    r = map_fsa_facts(parse_fsa_facts(PROVISIONS))
    assert r["values"]["short_term_debt"]["value"] == 7671.0
    assert "long_term_debt" not in r["values"]
    # Provisions reported separately, not merged into the debt keys.
    assert r["values"]["provisions"]["value"] == 15844.0


def test_map_provisions_revenue_present_is_emitted():
    """When fsa:Revenue IS tagged (36,536 here) revenue is emitted from it —
    the suppression rule fires only when fsa:Revenue is absent."""
    r = map_fsa_facts(parse_fsa_facts(PROVISIONS))
    assert r["values"]["revenue"]["value"] == 36536.0
    assert r["values"]["revenue"]["tag"] == "fsa:Revenue"
    # This filing has no GrossProfitLoss line -> gross_profit suppressed.
    assert "gross_profit" not in r["values"]
    assert "gross_profit" in _suppressed_keys(r)


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
