import pytest


def test_oim_from_ch_html_parses_micro():
    pytest.importorskip("arelle")
    from bottom_up_corpus.registers.ch_ixbrl import oim_from_ch_html
    oim = oim_from_ch_html("tests/fixtures/uk/frs105_micro_02855129.html")
    facts = oim["facts"]
    assert len(facts) > 20
    concepts = {fv["dimensions"]["concept"].split(":")[-1] for fv in facts.values()}
    assert {"CurrentAssets", "Equity"} <= concepts
