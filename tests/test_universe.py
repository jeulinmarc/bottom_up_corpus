from __future__ import annotations

import pytest

from bottom_up_corpus.universe import (
    Issuer,
    Universe,
    load_company_tickers,
    load_cusip_crosswalk,
    read_identifier_csv,
    reconcile_identifiers,
    resolve_ciks,
    resolve_cusips,
    resolve_tickers,
)


def test_resolve_tickers(apple_fetcher):
    issuers, unresolved = resolve_tickers(["aapl", "MSFT", "NOPE"], apple_fetcher)
    assert unresolved == ["NOPE"]
    by_ticker = {i.ticker: i for i in issuers}
    assert by_ticker["AAPL"].cik == "0000320193"
    assert by_ticker["MSFT"].cik == "0000789019"
    assert by_ticker["AAPL"].company == "Apple Inc."


def test_issuer_normalizes_cik():
    assert Issuer(cik=320193, ticker="AAPL").cik == "0000320193"


def test_universe_save_load_roundtrip(config):
    uni = Universe(config)
    issuers = [Issuer(cik="320193", ticker="AAPL", company="Apple Inc."),
               Issuer(cik="789019", ticker="MSFT", company="MICROSOFT CORP")]
    path = uni.save("curated", issuers)
    assert path.exists()
    loaded = uni.load("curated")
    assert [i.cik for i in loaded] == ["0000320193", "0000789019"]
    assert uni.names() == ["curated"]
    assert list(uni.iter_ciks("curated")) == ["0000320193", "0000789019"]


def test_universe_dedup_on_save(config):
    uni = Universe(config)
    uni.save("dup", [Issuer(cik="320193", ticker="AAPL"), Issuer(cik="320193", ticker="AAPL")])
    assert len(uni.load("dup")) == 1


def test_load_company_tickers_collision_prefers_lowest_cik(make_fetcher):
    # A ticker mapped to two different CIKs must resolve deterministically (lowest
    # CIK), independent of feed order, and surface a warning -- not last-write-wins.
    routes = {"company_tickers.json": {
        "0": {"cik_str": 789019, "ticker": "DUP", "title": "Higher CIK first"},
        "1": {"cik_str": 320193, "ticker": "DUP", "title": "Lower CIK second"},
        "2": {"cik_str": 111, "ticker": "AAPL", "title": "Apple"},
    }}
    fetcher = make_fetcher(routes)
    with pytest.warns(UserWarning, match="multiple"):
        out = load_company_tickers(fetcher)
    assert out["DUP"].cik == "0000320193"  # lowest CIK wins regardless of order
    assert out["AAPL"].cik == "0000000111"


def test_load_company_tickers_no_warning_when_clean(make_fetcher):
    routes = {"company_tickers.json": {
        "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
        "1": {"cik_str": 789019, "ticker": "MSFT", "title": "MICROSOFT CORP"},
    }}
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # any warning would fail the test
        out = load_company_tickers(make_fetcher(routes))
    assert out["AAPL"].cik == "0000320193"


def test_resolve_ciks_uses_submissions(apple_fetcher):
    # CIK-anchored: resolves via submissions API, and keeps a CIK even when its
    # metadata is unavailable (delisted/merged issuer not in the ticker map).
    issuers = resolve_ciks(["320193", "999999"], apple_fetcher)
    by_cik = {i.cik: i for i in issuers}
    assert by_cik["0000320193"].company == "Apple Inc."
    assert by_cik["0000320193"].ticker == "AAPL"
    assert "0000999999" in by_cik
    assert by_cik["0000999999"].company == ""  # no route -> empty, but CIK retained


def test_issuer_defaults_cusip_fields_empty():
    issuer = Issuer(cik="320193", ticker="AAPL")
    assert issuer.cusip6 == ""
    assert issuer.resolution == ""


def test_universe_roundtrips_cusip6_and_resolution(config):
    uni = Universe(config)
    uni.save("withcusip", [Issuer(cik="320193", ticker="AAPL", company="Apple Inc.",
                                  cusip6="037833", resolution="both")])
    loaded = uni.load("withcusip")
    assert loaded[0].cusip6 == "037833"
    assert loaded[0].resolution == "both"


SAMPLE_CROSSWALK = """cik,cusip6,cusip8
320193.0,037833,03783310
789019.0,594918,59491810
12345.0,DUPDUP,DUPDUP10
67890.0,DUPDUP,DUPDUP20
"""


def _write_crosswalk(tmp_path):
    p = tmp_path / "xw.csv"
    p.write_text(SAMPLE_CROSSWALK, encoding="utf-8")
    return p


def test_load_cusip_crosswalk_normalizes_cik_despite_float_artifact(tmp_path):
    xw = load_cusip_crosswalk(_write_crosswalk(tmp_path))
    assert xw["037833"] == {"0000320193"}
    assert xw["594918"] == {"0000789019"}


def test_load_cusip_crosswalk_collects_multiple_ciks_per_cusip6(tmp_path):
    xw = load_cusip_crosswalk(_write_crosswalk(tmp_path))
    assert xw["DUPDUP"] == {"0000012345", "0000067890"}


def test_resolve_cusips_single_match(tmp_path):
    xw = load_cusip_crosswalk(_write_crosswalk(tmp_path))
    resolved, unresolved = resolve_cusips(["037833", "594918"], xw)
    assert resolved == {"037833": "0000320193", "594918": "0000789019"}
    assert unresolved == []


def test_resolve_cusips_absent_goes_unresolved(tmp_path):
    xw = load_cusip_crosswalk(_write_crosswalk(tmp_path))
    resolved, unresolved = resolve_cusips(["999999"], xw)
    assert resolved == {} and unresolved == ["999999"]


def test_resolve_cusips_ambiguous_multi_cik_is_unresolved(tmp_path):
    xw = load_cusip_crosswalk(_write_crosswalk(tmp_path))
    with pytest.warns(UserWarning, match="multiple CIKs"):
        resolved, unresolved = resolve_cusips(["DUPDUP"], xw)
    assert resolved == {} and unresolved == ["DUPDUP"]


TICKER_TABLE = {
    "AAPL": Issuer(cik="320193", ticker="AAPL", company="Apple Inc."),
    "DT": Issuer(cik="1773383", ticker="DT", company="Dynatrace, Inc."),
    "KO": Issuer(cik="21344", ticker="KO", company="COCA COLA CO"),
}
CROSSWALK = {
    "037833": {"0000320193"},
    "25156P": {"0000999999"},
    "191216": {"0000888888"},
}


def test_reconcile_provided_cik_is_authoritative():
    rows = [{"cik": "1750", "ticker": "AAPL", "cusip6": "037833", "name": "AAR Corp"}]
    issuers, collisions, unresolved = reconcile_identifiers(rows, TICKER_TABLE, CROSSWALK)
    assert collisions == [] and unresolved == []
    assert issuers[0].cik == "0000001750"
    assert issuers[0].resolution == "cik"


def test_reconcile_both_sources_agree():
    rows = [{"cik": "", "ticker": "AAPL", "cusip6": "037833", "name": "Apple Inc."}]
    issuers, collisions, unresolved = reconcile_identifiers(rows, TICKER_TABLE, CROSSWALK)
    assert collisions == [] and unresolved == []
    assert issuers[0].cik == "0000320193" and issuers[0].resolution == "both"


def test_reconcile_flags_homonym_collision():
    rows = [{"cik": "", "ticker": "DT", "cusip6": "25156P", "name": "Deutsche Telekom Intl"}]
    issuers, collisions, unresolved = reconcile_identifiers(rows, TICKER_TABLE, CROSSWALK)
    assert issuers == []
    assert collisions[0]["ticker"] == "DT"
    assert collisions[0]["cik_ticker"] == "0001773383"
    assert collisions[0]["cik_cusip"] == "0000999999"
    assert collisions[0]["kind"] == "name_mismatch"
    assert collisions[0]["sec_ticker_name"] == "Dynatrace, Inc."


def test_reconcile_classifies_collision_name_match():
    rows = [{"cik": "", "ticker": "KO", "cusip6": "191216", "name": "The Coca-Cola Company"}]
    _, collisions, _ = reconcile_identifiers(rows, TICKER_TABLE, CROSSWALK)
    assert collisions[0]["kind"] == "name_match"


def test_reconcile_ticker_only_and_cusip_only_and_unresolved():
    rows = [
        {"cik": "", "ticker": "AAPL", "cusip6": "ZZZZZZ", "name": ""},
        {"cik": "", "ticker": "TKM", "cusip6": "25156P", "name": "DT Fin"},
        {"cik": "", "ticker": "NOPE", "cusip6": "ZZZZZZ", "name": "Mystery"},
    ]
    issuers, collisions, unresolved = reconcile_identifiers(rows, TICKER_TABLE, CROSSWALK)
    assert {i.resolution for i in issuers} == {"ticker", "cusip"}
    assert {i.cik for i in issuers} == {"0000320193", "0000999999"}
    assert unresolved == ["NOPE"]


def test_read_identifier_csv_autodetects_cik_ticker_cusip(tmp_path):
    csv_path = tmp_path / "u.csv"
    csv_path.write_text(
        "CIK,Ticker,CUSIP,Issuer\n"
        "0000320193,AAPL,037833AA0,Apple Inc\n"
        "0000320193,AAPL,037833BB1,Apple Inc\n"
        ",AFL,001055AY8,Aflac Inc\n",
        encoding="utf-8",
    )
    rows = read_identifier_csv(csv_path)
    by_ticker = {r["ticker"]: r for r in rows}
    assert by_ticker["AAPL"]["cik"] == "0000320193"
    assert by_ticker["AAPL"]["cusip6"] == "037833"
    assert by_ticker["AFL"]["cik"] == ""
    assert by_ticker["AFL"]["cusip6"] == "001055"


def test_read_identifier_csv_derives_cusip6_from_isin(tmp_path):
    csv_path = tmp_path / "u.csv"
    csv_path.write_text("Ticker,ISIN\nABBV,US00287YAD56\n", encoding="utf-8")
    rows = read_identifier_csv(csv_path)
    assert rows[0]["cusip6"] == "00287Y"


def test_read_identifier_csv_keeps_full_cusip(tmp_path):
    csv_path = tmp_path / "u.csv"
    csv_path.write_text(
        "Ticker,CUSIP\n"
        "AAPL,037833AA0\n"
        "AAPL,037833AA0\n"   # most common full CUSIP for AAPL
        "AAPL,037833BB1\n",
        encoding="utf-8",
    )
    rows = read_identifier_csv(csv_path)
    assert rows[0]["cusip"] == "037833AA0"
    assert rows[0]["cusip6"] == "037833"
