from __future__ import annotations

import json

import pytest

from bottom_up_corpus.config import Config


class FakeFetcher:
    """Stand-in for Fetcher that serves canned responses by URL substring.

    ``routes`` maps a substring of the requested URL to either a Python object
    (returned by ``get_json``) or a string (returned by ``get_text``). The first
    matching substring wins; an unmatched URL raises, exercising error paths.
    """

    def __init__(self, routes: dict[str, object], config: Config | None = None):
        self.routes = routes
        self.config = config or Config()
        self.calls: list[str] = []

    def _match(self, url: str):
        self.calls.append(url)
        for needle, value in self.routes.items():
            if needle in url:
                return value
        raise RuntimeError(f"no route for {url}")

    def get_json(self, url: str, **_):
        value = self._match(url)
        if isinstance(value, str):
            return json.loads(value)
        return value

    def get_text(self, url: str, **_):
        value = self._match(url)
        if isinstance(value, str):
            return value
        return json.dumps(value)


@pytest.fixture
def config(tmp_path) -> Config:
    return Config(data_dir=tmp_path / "data", contact="test@example.com")


@pytest.fixture
def make_fetcher(config):
    """Factory: build a FakeFetcher from a routes dict, sharing the test config."""

    def _make(routes: dict[str, object]) -> FakeFetcher:
        return FakeFetcher(routes, config=config)

    return _make


APPLE_SUBMISSIONS = {
    "name": "Apple Inc.",
    "tickers": ["AAPL"],
    "filings": {
        "recent": {
            "form": ["10-K", "10-Q", "8-K", "4", "NT 10-K"],
            "accessionNumber": [
                "0000320193-24-000123",
                "0000320193-24-000081",
                "0000320193-24-000070",
                "0000320193-24-000060",
                "0000320193-24-000050",
            ],
            "filingDate": ["2024-11-01", "2024-08-02", "2024-05-03", "2024-02-01", "2024-01-15"],
            "reportDate": ["2024-09-28", "2024-06-29", "", "", ""],
            "primaryDocument": [
                "aapl-20240928.htm",
                "aapl-20240629.htm",
                "ex991.htm",
                "wk-form4.xml",
                "",
            ],
            "primaryDocDescription": ["10-K", "10-Q", "8-K", "FORM 4", ""],
        },
        "files": [],
    },
}


COMPANY_TICKERS = {
    "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
    "1": {"cik_str": 789019, "ticker": "MSFT", "title": "MICROSOFT CORP"},
}


@pytest.fixture
def apple_fetcher(config) -> FakeFetcher:
    return FakeFetcher(
        {
            "CIK0000320193.json": APPLE_SUBMISSIONS,
            "company_tickers.json": COMPANY_TICKERS,
        },
        config=config,
    )
