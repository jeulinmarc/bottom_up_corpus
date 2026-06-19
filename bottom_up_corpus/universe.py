"""Issuer universe registry.

Parallels ``cb_corpus.banks``: a version-controlled list of the entities we
crawl. cb_corpus targets 63 named central banks; here the curated tier is a list
of company issuers identified by CIK (with ticker/name for humans).

Ticker -> CIK resolution uses the SEC's official map
``https://www.sec.gov/files/company_tickers.json``. Curated lists are stored as
JSONL under ``data/universe/<name>.jsonl`` and committed to the repo.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass
from pathlib import Path

from .config import Config, normalize_cik
from .http import Fetcher

COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"


@dataclass(frozen=True)
class Issuer:
    """One company in the universe."""

    cik: str
    ticker: str = ""
    company: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "cik", normalize_cik(self.cik))


def load_company_tickers(fetcher: Fetcher) -> dict[str, Issuer]:
    """Fetch the SEC ticker map; return ``{TICKER: Issuer}`` (upper-cased)."""
    data = fetcher.get_json(COMPANY_TICKERS_URL)
    # The map is keyed by arbitrary index strings: {"0": {cik_str, ticker, title}, ...}
    rows = data.values() if isinstance(data, dict) else data
    out: dict[str, Issuer] = {}
    for row in rows:
        ticker = str(row.get("ticker", "")).upper()
        if not ticker:
            continue
        out[ticker] = Issuer(
            cik=normalize_cik(row["cik_str"]),
            ticker=ticker,
            company=row.get("title", ""),
        )
    return out


def resolve_tickers(
    tickers: Iterable[str], fetcher: Fetcher
) -> tuple[list[Issuer], list[str]]:
    """Resolve tickers to :class:`Issuer`s. Returns ``(issuers, unresolved)``."""
    table = load_company_tickers(fetcher)
    issuers: list[Issuer] = []
    unresolved: list[str] = []
    for raw in tickers:
        t = raw.strip().upper()
        if not t:
            continue
        issuer = table.get(t)
        if issuer:
            issuers.append(issuer)
        else:
            unresolved.append(t)
    return issuers, unresolved


class Universe:
    """Load/save committed curated issuer lists under ``data/universe/``."""

    def __init__(self, config: Config | None = None):
        self.config = config or Config()

    def path(self, name: str) -> Path:
        return self.config.universe_dir / f"{name}.jsonl"

    def save(self, name: str, issuers: Iterable[Issuer]) -> Path:
        path = self.path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        # De-duplicate by CIK, preserve order.
        seen: set[str] = set()
        with path.open("w", encoding="utf-8") as fh:
            for issuer in issuers:
                if issuer.cik in seen:
                    continue
                seen.add(issuer.cik)
                fh.write(json.dumps(asdict(issuer), ensure_ascii=False) + "\n")
        return path

    def load(self, name: str) -> list[Issuer]:
        path = self.path(name)
        if not path.exists():
            raise FileNotFoundError(f"universe not found: {path}")
        issuers: list[Issuer] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            issuers.append(Issuer(cik=row["cik"], ticker=row.get("ticker", ""), company=row.get("company", "")))
        return issuers

    def names(self) -> list[str]:
        d = self.config.universe_dir
        if not d.exists():
            return []
        return sorted(p.stem for p in d.glob("*.jsonl"))

    def iter_ciks(self, name: str) -> Iterator[str]:
        for issuer in self.load(name):
            yield issuer.cik
