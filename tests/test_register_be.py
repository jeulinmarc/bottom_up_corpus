"""Tests for the Belgium BNB CBSO XBRL parser (stdlib, no Arelle)."""
import pytest

from bottom_up_corpus.registers.bnb_xbrl import parse_bnb_data_xbrl, open_bnb_deposit

FIXTURE = "tests/fixtures/be/m02_full_0648822310.xbrl"


def test_parse_bnb_xbrl_count():
    facts = parse_bnb_data_xbrl(FIXTURE)
    assert len(facts) > 1000


def test_parse_bnb_xbrl_dims_keys():
    facts = parse_bnb_data_xbrl(FIXTURE)
    # At least one fact must have both "bas" and "part" dimension keys
    assert any("bas" in f["dims"] and "part" in f["dims"] for f in facts)


def test_parse_bnb_xbrl_total_assets_anchor():
    facts = parse_bnb_data_xbrl(FIXTURE)
    # Context c97: bas=m25, part=m1, prd=m1 → total assets 14 340 301 238.13 €
    target_dims = {"bas": "m25", "part": "m1", "prd": "m1"}
    matches = [f for f in facts if f["dims"] == target_dims]
    assert matches, f"No fact with dims == {target_dims!r}"
    values = [f["value"] for f in matches]
    assert any(abs(v - 14_340_301_238.13) < 1 for v in values), (
        f"Total-assets anchor not found; values seen: {values}"
    )


def test_open_bnb_deposit(tmp_path):
    import zipfile

    data_bytes = b"<xbrl>data</xbrl>"
    contact_bytes = b"<xbrl>contact</xbrl>"
    vendor_bytes = b"<xbrl>vendor</xbrl>"

    zip_path = tmp_path / "test_deposit.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("filing-contact.xbrl", contact_bytes)
        zf.writestr("filing-data.xbrl", data_bytes)
        zf.writestr("filing-vendor.xbrl", vendor_bytes)

    result = open_bnb_deposit(zip_path.read_bytes())
    assert result == data_bytes
