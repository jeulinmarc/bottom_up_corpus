"""Tests for the Luxembourg LBR/STATEC eCDF register parser (stdlib path)."""
from pathlib import Path

import pytest

from bottom_up_corpus.registers.lu_ecdf import parse_lu_declarers

FIXTURES = Path("tests/fixtures/lu")
FERRERO = FIXTURES / "ferrero_b60814_full_2022.xml"
KROKUS = FIXTURES / "krokus_b138357_full_2012.xml"
SILVERSTEIN = FIXTURES / "silverstein_b154370_abr_2022.xml"


def _declarer(result, rcs):
    matches = [d for d in result if d["rcs"] == rcs]
    assert matches, f"No declarer with rcs={rcs!r}"
    return matches[0]


def _declaration(declarer, type_):
    matches = [d for d in declarer["declarations"] if d["type"] == type_]
    assert matches, f"No declaration type={type_!r} in {declarer['rcs']}"
    return matches[0]


class TestFerrero:
    def test_rcs_and_name(self):
        result = parse_lu_declarers(FERRERO)
        d = _declarer(result, "B60814")
        assert d["name"] == "FERRERO INTERNATIONAL S.A."

    def test_bilan_fields(self):
        result = parse_lu_declarers(FERRERO)
        d = _declarer(result, "B60814")
        dec = _declaration(d, "CA_BILAN")
        assert dec["currency"] == "EUR"
        assert dec["fields"][201] == 8721873385.0

    def test_compp_fields(self):
        result = parse_lu_declarers(FERRERO)
        d = _declarer(result, "B60814")
        dec = _declaration(d, "CA_COMPP")
        assert dec["fields"][701] == 237229504.0


class TestKrokus:
    def test_rcs_and_name(self):
        result = parse_lu_declarers(KROKUS)
        d = _declarer(result, "B138357")
        assert d["name"] == "KROKUS S.A."

    def test_bilan_fields(self):
        result = parse_lu_declarers(KROKUS)
        d = _declarer(result, "B138357")
        dec = _declaration(d, "CA_BILAN")
        assert dec["currency"] == "EUR"
        assert dec["fields"][201] == 2182897.55


class TestSilverstein:
    def test_parses_without_error(self):
        result = parse_lu_declarers(SILVERSTEIN)
        assert len(result) >= 1

    def test_rcs(self):
        result = parse_lu_declarers(SILVERSTEIN)
        d = _declarer(result, "B154370")
        assert "Silverstein" in d["name"]

    def test_abridged_bilan_total_assets(self):
        result = parse_lu_declarers(SILVERSTEIN)
        d = _declarer(result, "B154370")
        dec = _declaration(d, "CA_BILANABR")
        assert dec["fields"][201] == 1386627.13


class TestBytesInput:
    def test_bytes_path_equivalence(self):
        """parse_lu_declarers must accept raw bytes as well as a file path."""
        result_path = parse_lu_declarers(KROKUS)
        result_bytes = parse_lu_declarers(KROKUS.read_bytes())
        assert result_path == result_bytes
