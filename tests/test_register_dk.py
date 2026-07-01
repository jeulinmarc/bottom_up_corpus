"""Tests for the DK-GAAP FSA XBRL parser (Task 1)."""
import pytest
from bottom_up_corpus.registers.dk_fsa_xbrl import parse_fsa_facts

FIXTURE = "tests/fixtures/dk/dk_30830725_microB_2025.xml"


def test_parse_fsa_facts_microb_2025():
    """parse_fsa_facts on the micro-B 2025 fixture returns correct BS figures."""
    result = parse_fsa_facts(FIXTURE)

    assert result["currency"] == "DKK"
    assert result["period_end"] == "2025-09-30"

    facts = result["facts"]

    # Balance-sheet figures (instant c4=2025-09-30, not prior-year c5=2024-09-30)
    assert facts["Assets"] == 6744.0
    assert facts["Equity"] == -585256.0
    assert facts["LiabilitiesAndEquity"] == 6744.0
    assert facts["CashAndCashEquivalents"] == 1744.0
