import pytest

from bottom_up_corpus.registers.concepts_uk import map_ch_facts
from bottom_up_corpus.registers.identity import resolve_register_specs


def flat(**kw):
    """Synthetic ``flatten_oim_json`` output: one point per concept at 2025-03-31."""
    return {
        name: [{"val": v, "end": "2025-03-31", "unit": "GBP", "tag": name, "label": name}]
        for name, v in kw.items()
    }


def test_full_clean_filer_all_emitted():   # 00510976
    m = map_ch_facts(flat(FixedAssets=3645291, CurrentAssets=2625095,
        NetCurrentAssetsLiabilities=2484269, TotalAssetsLessCurrentLiabilities=6129560,
        NetAssetsLiabilities=6053560, Equity=6053560, ProfitLoss=161709, CashBankOnHand=2095623))
    v = {k: m["values"][k]["value"] for k in m["values"]}
    assert not m["unbalanced"]
    assert v["equity"] == 6053560 and v["net_income"] == 161709
    assert v["liabilities_current"] == 140826 and v["long_term_debt"] == 76000
    assert v["assets"] == 6270386 and v["liabilities"] == 216826
    # balance identity holds
    assert abs((v["assets"] - v["liabilities"]) - v["equity"]) <= 2


def test_fixedassets_untagged_assets_via_talcl_not_understated():   # 01194034
    m = map_ch_facts(flat(CurrentAssets=1107327, NetCurrentAssetsLiabilities=565497,
        TotalAssetsLessCurrentLiabilities=8893190, NetAssetsLiabilities=7521425,
        Equity=7521425, CashBankOnHand=47547))
    v = {k: m["values"][k]["value"] for k in m["values"]}
    assert v["assets"] == 9435020           # NOT 1107327 (the trap)
    assert v["long_term_debt"] == 1371765 and v["liabilities_current"] == 541830


def test_pl_only_filer_suppresses_unconfirmable_balance():   # SC741022
    m = map_ch_facts(flat(TurnoverRevenue=30927, GrossProfitLoss=30796, OperatingProfitLoss=19924,
        ProfitLossOnOrdinaryActivitiesBeforeTax=19924, TaxTaxCreditOnProfitOrLossOnOrdinaryActivities=4030,
        ProfitLoss=15894, CurrentAssets=22922, NetAssetsLiabilities=18260, Equity=18260, CashBankOnHand=10759))
    v = {k: m["values"][k]["value"] for k in m["values"]}
    assert v["revenue"] == 30927 and v["net_income"] == 15894 and v["equity"] == 18260
    assert "assets" not in v and "liabilities" not in v   # TALCL/NCA absent -> suppressed, not faked


def test_micro_balance_sheet():   # frs105 02855129
    m = map_ch_facts(flat(FixedAssets=0, CurrentAssets=304205,
        NetCurrentAssetsLiabilities=24699, TotalAssetsLessCurrentLiabilities=24699, Equity=24699))
    v = {k: m["values"][k]["value"] for k in m["values"]}
    assert v["assets"] == 304205 and v["liabilities_current"] == 279506 and v["equity"] == 24699
    assert v.get("long_term_debt", 0) == 0


def test_negative_equity_distressed():   # 11515034
    # CurrentAssets absent -> liabilities_current not derivable. long_term_debt
    # (= TALCL - NetAssets = 13002) IS derivable, but emitting it alone would let
    # the engine compute total_debt = 13002, understating true total liabilities.
    # Per the completeness rule the whole derived balance block is suppressed;
    # only directly-tagged equity/net_assets and the P&L survive.
    m = map_ch_facts(flat(TurnoverRevenue=0, ProfitLoss=0, NetCurrentAssetsLiabilities=-10541,
        TotalAssetsLessCurrentLiabilities=-10541, NetAssetsLiabilities=-23543, Equity=-23543))
    v = {k: m["values"][k]["value"] for k in m["values"]}
    assert v["equity"] == -23543 and v["net_assets"] == -23543 and v["revenue"] == 0
    for key in ("long_term_debt", "liabilities", "liabilities_current",
                "short_term_debt", "assets"):
        assert key not in v
    suppressed_keys = {k for k, _ in m["suppressed"]}
    assert {"long_term_debt", "liabilities_current"} <= suppressed_keys
    assert not m["unbalanced"]


def test_unbalanced_filing_suppressed():   # crafted: NA != E beyond tol
    m = map_ch_facts(flat(NetAssetsLiabilities=1000, Equity=1200, CurrentAssets=5000,
        NetCurrentAssetsLiabilities=1200, TotalAssetsLessCurrentLiabilities=1000))
    assert m["unbalanced"] is True and m["values"] == {}


def test_reconciliation_mismatch_suppresses_all_derived_balance():   # crafted
    # Primary NA==E passes (1100==1100) but the Anchor reconciliation fails:
    # TALCL 1200 != FixedAssets 1000 + NetCurrentAssets 100. The inputs are
    # proven inconsistent, so EVERY derived balance item is suppressed — not just
    # assets/liabilities. Directly-tagged equity/net_assets still stand; this is a
    # partial suppression, not a whole-filing reject (unbalanced stays False).
    m = map_ch_facts(flat(FixedAssets=1000, CurrentAssets=500, NetCurrentAssetsLiabilities=100,
        TotalAssetsLessCurrentLiabilities=1200, NetAssetsLiabilities=1100, Equity=1100))
    v = {k: m["values"][k]["value"] for k in m["values"]}
    for key in ("assets", "liabilities", "liabilities_current",
                "short_term_debt", "long_term_debt"):
        assert key not in v
    assert v["equity"] == 1100 and v["net_assets"] == 1100
    suppressed_keys = {k for k, _ in m["suppressed"]}
    assert {"assets", "liabilities", "liabilities_current",
            "short_term_debt", "long_term_debt"} <= suppressed_keys
    assert m["unbalanced"] is False


def test_oim_from_ch_html_parses_micro():
    pytest.importorskip("arelle")
    from bottom_up_corpus.registers.ch_ixbrl import oim_from_ch_html
    oim = oim_from_ch_html("tests/fixtures/uk/frs105_micro_02855129.html")
    facts = oim["facts"]
    assert len(facts) > 20
    concepts = {fv["dimensions"]["concept"].split(":")[-1] for fv in facts.values()}
    assert {"CurrentAssets", "Equity"} <= concepts


# ---------------------------------------------------------------------------
# GB identity: _norm_ch_number + resolve_register_specs GB branch
# ---------------------------------------------------------------------------

def test_ch_number_preserved_and_padded():
    from bottom_up_corpus.registers.identity import _norm_ch_number
    assert _norm_ch_number("510976") == "00510976"       # pure digits -> zero-pad to 8
    assert _norm_ch_number("SC741022") == "SC741022"     # SC prefix -> verbatim
    assert _norm_ch_number(" oc372294 ") == "OC372294"   # strip + uppercase, OC prefix


class _GleifFetcherGB:
    """Minimal GLEIF stub returning one entity record."""
    def __init__(self, country, registered_as, name="ACME LTD"):
        self._c, self._r, self._n = country, registered_as, name

    def get_json(self, url, **kw):
        return {"data": {"attributes": {"entity": {
            "legalName": {"name": self._n},
            "legalAddress": {"country": self._c},
            "registeredAs": self._r,
        }}}}


def test_gb_lei_resolves_via_gleif():
    """LEI for a GB entity resolves via GLEIF entity.registeredAs -> ch_number."""
    r = resolve_register_specs(
        [{"lei": "L1GB"}],
        fetcher=_GleifFetcherGB("GB", "10399850"),
    )[0]
    assert r["ch_number"] == "10399850"
    assert r["country"] == "GB"
    assert r["status"] == "ok"
    assert r["lei"] == "L1GB"


def test_non_gb_lei_unresolved():
    """LEI for a non-GB entity (e.g. SE) stays unresolved; no ch_number returned."""
    r = resolve_register_specs(
        [{"lei": "L2SE"}],
        fetcher=_GleifFetcherGB("SE", "5560000000"),
    )[0]
    assert r["status"] == "unresolved"
    assert not r.get("ch_number")
