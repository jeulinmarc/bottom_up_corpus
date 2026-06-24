from __future__ import annotations

import pytest

from bottom_up_corpus.universe import (
    Issuer,
    Universe,
    load_company_tickers,
    resolve_ciks,
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
