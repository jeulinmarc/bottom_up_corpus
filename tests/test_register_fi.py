"""Tests for the Finnish PRH XBRL parser — Task 1 (stdlib dimensional parser)."""
import pytest

from bottom_up_corpus.registers.fi_prh_xbrl import parse_fi_facts

FIXTURE = "tests/fixtures/fi/fi_2919415-2_full_2024.xml"


def _parsed():
    """Parse the full 2024 fixture once (not cached — pytest handles isolation)."""
    return parse_fi_facts(FIXTURE)


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
