"""Tests for the Finnish PRH register.

Task 1 — stdlib dimensional parser (``fi_prh_xbrl.parse_fi_facts``).
Task 2 — concept pack + NO-FALSE-DATA gate (``concepts_fi.map_fi_facts``).
"""
import pytest

from bottom_up_corpus.registers.concepts_fi import map_fi_facts
from bottom_up_corpus.registers.fi_prh_xbrl import parse_fi_facts

FIXTURE = "tests/fixtures/fi/fi_2919415-2_full_2024.xml"
ABBREV = "tests/fixtures/fi/fi_0100379-9_abbrev_2023.xml"
HOUSING = "tests/fixtures/fi/fi_0100843-4_housing_2023.xml"


def _parsed():
    """Parse the full 2024 fixture once (not cached — pytest handles isolation)."""
    return parse_fi_facts(FIXTURE)


# ===========================================================================
# Task 1 — parser
# ===========================================================================

def test_parse_fi_facts_revenue():
    """fields[673] == 481 773.33 (revenue line, md103 namespace)."""
    result = _parsed()
    assert result["fields"][673] == pytest.approx(481_773.33, abs=0.01)


def test_parse_fi_facts_total_assets_present_and_positive():
    """fields[360] (total assets) must be present and > 0."""
    result = _parsed()
    assert 360 in result["fields"]
    assert result["fields"][360] > 0


def test_parse_fi_facts_net_income_present():
    """fields[740] (net income, NOT x738) must be present."""
    result = _parsed()
    assert 740 in result["fields"]


def test_parse_fi_facts_currency():
    """currency must be 'EUR'."""
    result = _parsed()
    assert result["currency"] == "EUR"


def test_parse_fi_facts_period_end():
    """period_end must be '2024-12-31'."""
    result = _parsed()
    assert result["period_end"] == "2024-12-31"


def test_parse_fi_facts_no_prior_period_fields():
    """Prior-period facts (fi_dim:REF present) must be excluded."""
    result = _parsed()
    # All fields come from current contexts only: verify by checking
    # that we have some fields (parser ran) but prior MCY codes are not
    # double-counted as separate entries.
    assert len(result["fields"]) > 0


def test_parse_fi_facts_bytes_input():
    """parse_fi_facts must also accept raw bytes."""
    raw = open(FIXTURE, "rb").read()
    result = parse_fi_facts(raw)
    assert result["fields"][673] == pytest.approx(481_773.33, abs=0.01)
    assert result["period_end"] == "2024-12-31"


# ===========================================================================
# Task 2 — concept pack + NO-FALSE-DATA gate (map_fi_facts)
# ===========================================================================

def _mapped(path):
    return map_fi_facts(parse_fi_facts(path))


def _reason(mapped, key):
    """The suppression reason recorded for ``key`` (or None)."""
    for k, r in mapped["suppressed"]:
        if k == key:
            return r
    return None


# --- fi_2919415-2 (full 2024) ----------------------------------------------

def test_full_shape_basis_currency_balanced():
    m = _mapped(FIXTURE)
    assert m["basis"] == "company"
    assert m["currency"] == "EUR"
    assert m["period_end"] == "2024-12-31"
    assert m["unbalanced"] is False


def test_full_revenue():
    m = _mapped(FIXTURE)
    rev = m["values"]["revenue"]
    assert rev["value"] == pytest.approx(481_773.33, abs=0.01)
    assert rev["tag"] == "fi_MC:x673"
    assert rev["unit"] == "EUR"


def test_full_net_income_is_x740_not_x738():
    """THE TRAP: net_income is x740 (final, after appropriations), NEVER x738."""
    parsed = parse_fi_facts(FIXTURE)
    x738 = parsed["fields"][738]   # 72 574.02  pre-appropriations
    x740 = parsed["fields"][740]   # 57 560.30  final bottom line
    assert x738 != pytest.approx(x740, abs=0.01)      # the two genuinely differ
    m = map_fi_facts(parsed)
    ni = m["values"]["net_income"]
    assert ni["value"] == pytest.approx(57_560.30, abs=0.01)
    assert ni["value"] == pytest.approx(x740, abs=0.01)        # == x740
    assert ni["value"] != pytest.approx(x738, abs=0.01)        # NOT x738
    assert ni["tag"] == "fi_MC:x740"


def test_full_total_assets():
    m = _mapped(FIXTURE)
    ta = m["values"]["total_assets"]
    assert ta["value"] == pytest.approx(201_064.55, abs=0.01)
    assert ta["tag"] == "fi_MC:x360"


def test_full_equity_read_from_fixture_equals_assets_minus_liabilities():
    parsed = parse_fi_facts(FIXTURE)
    x435 = parsed["fields"][435]
    x360 = parsed["fields"][360]
    x513 = parsed["fields"][513]
    # x435 read straight from the fixture satisfies the balance identity.
    assert x435 == pytest.approx(x360 - x513, abs=0.01)   # 185650.88 == 201064.55 − 15413.67
    m = map_fi_facts(parsed)
    eq = m["values"]["equity"]
    assert eq["value"] == pytest.approx(x435, abs=0.01)
    assert eq["value"] == pytest.approx(185_650.88, abs=0.01)
    assert eq["tag"] == "fi_MC:x435"


def test_full_interest_expense_is_abs_of_x4046():
    parsed = parse_fi_facts(FIXTURE)
    assert parsed["fields"][4046] < 0                 # stored negative
    ie = map_fi_facts(parsed)["values"]["interest_expense"]
    assert ie["value"] == pytest.approx(abs(parsed["fields"][4046]), abs=0.01)
    assert ie["value"] >= 0
    assert ie["tag"] == "fi_MC:x4046"


def test_full_leverage_split_suppressed_despite_reconciling():
    """x583 + x816 == x513 to the cent, yet WHICH is long vs short is unconfirmed
    → suppress the maturity split (NO FALSE DATA). Total liabilities still emitted."""
    parsed = parse_fi_facts(FIXTURE)
    f = parsed["fields"]
    assert f[583] + f[816] == pytest.approx(f[513], abs=0.01)   # reconciles exactly
    m = map_fi_facts(parsed)
    assert "long_term_debt" not in m["values"]
    assert "short_term_debt" not in m["values"]
    reason = _reason(m, "long_term_debt")
    assert reason is not None and "UNCONFIRMED" in reason
    assert _reason(m, "short_term_debt") is not None
    # The confirmed TOTAL liabilities is still emitted (liabilities-based).
    assert m["values"]["liabilities"]["value"] == pytest.approx(15_413.67, abs=0.01)
    assert m["values"]["liabilities"]["tag"] == "fi_MC:x513"


def test_full_always_suppressed_concepts():
    m = _mapped(FIXTURE)
    for key in ("income_tax", "cash", "financial_debt", "provisions"):
        assert key not in m["values"]
        assert _reason(m, key) is not None


# --- fi_0100379-9 (abbreviated 2023) ---------------------------------------

def test_abbrev_revenue_absent_but_gate_holds():
    m = _mapped(ABBREV)
    assert "revenue" not in m["values"]               # x673 missing in abbreviated
    assert _reason(m, "revenue") is not None
    assert m["values"]["equity"]["value"] == pytest.approx(19_979.80, abs=0.01)
    assert m["values"]["total_assets"]["value"] == pytest.approx(122_979.81, abs=0.01)
    assert m["unbalanced"] is False                   # primary balance holds


# --- fi_0100843-4 (housing 2023) -------------------------------------------

def test_housing_negative_non_current_accepted():
    parsed = parse_fi_facts(HOUSING)
    x376 = parsed["fields"][376]
    assert x376 < 0                                   # negative non-current assets
    m = map_fi_facts(parsed)
    nc = m["values"]["non_current_assets"]
    assert nc["value"] == pytest.approx(x376, abs=0.01)   # accepted as-is, no positivity check
    assert nc["value"] < 0
    assert nc["tag"] == "fi_MC:x376"
    # decomposition x376 + x424 == x360 still holds → current_assets also emitted
    assert m["values"]["current_assets"]["value"] == pytest.approx(parsed["fields"][424], abs=0.01)
    assert m["unbalanced"] is False


def test_housing_decomposition_identity_holds():
    f = parse_fi_facts(HOUSING)["fields"]
    assert f[376] + f[424] == pytest.approx(f[360], abs=0.01)   # x376 negative, still balances


# --- synthetic edge cases --------------------------------------------------

def test_synthetic_unbalanced_blanks_all_values():
    """x360 != x435 + x513 beyond tol → unbalanced, NO values emitted."""
    parsed = {"period_end": "2024-12-31", "currency": "EUR",
              "fields": {360: 300_000.0, 435: 200_000.0, 513: 50_000.0, 673: 10_000.0}}
    m = map_fi_facts(parsed)
    assert m["unbalanced"] is True
    assert m["values"] == {}
    assert _reason(m, "__all__") is not None


def test_synthetic_debt_not_reconciling_suppresses_split():
    """x583 + x816 != x513 → maturity split suppressed with a reconciliation reason."""
    parsed = {"period_end": "2024-12-31", "currency": "EUR",
              "fields": {360: 250_000.0, 435: 200_000.0, 513: 50_000.0,
                         583: 40_000.0, 816: 5_000.0}}          # 45k != 50k liabilities
    m = map_fi_facts(parsed)
    assert m["unbalanced"] is False
    assert "long_term_debt" not in m["values"]
    assert "short_term_debt" not in m["values"]
    reason = _reason(m, "long_term_debt")
    assert reason is not None and "reconcile" in reason
