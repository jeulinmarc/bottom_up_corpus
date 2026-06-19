from __future__ import annotations

from bottom_up_corpus.universe import Issuer, Universe, resolve_tickers


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
