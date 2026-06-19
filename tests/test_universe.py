from __future__ import annotations

from bottom_up_corpus.universe import Issuer, Universe, resolve_ciks, resolve_tickers


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


def test_resolve_ciks_uses_submissions(apple_fetcher):
    # CIK-anchored: resolves via submissions API, and keeps a CIK even when its
    # metadata is unavailable (delisted/merged issuer not in the ticker map).
    issuers = resolve_ciks(["320193", "999999"], apple_fetcher)
    by_cik = {i.cik: i for i in issuers}
    assert by_cik["0000320193"].company == "Apple Inc."
    assert by_cik["0000320193"].ticker == "AAPL"
    assert "0000999999" in by_cik
    assert by_cik["0000999999"].company == ""  # no route -> empty, but CIK retained
