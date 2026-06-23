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
import warnings
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass
from pathlib import Path

from .config import Config, normalize_cik
from .http import Fetcher

COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"


@dataclass(frozen=True)
class Issuer:
    """One company in the universe.

    ``cik`` may be empty when it could not be resolved (e.g. a since-delisted
    index member). ``first_seen``/``last_seen`` are optional index-membership
    dates (ISO, or ``"current"`` for present members).
    """

    cik: str
    ticker: str = ""
    company: str = ""
    first_seen: str = ""
    last_seen: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "cik", normalize_cik(self.cik) if self.cik else "")


def load_company_tickers(fetcher: Fetcher) -> dict[str, Issuer]:
    """Fetch the SEC ticker map; return ``{TICKER: Issuer}`` (upper-cased).

    When a ticker maps to more than one CIK (rare, but possible across the SEC
    feed), the lowest CIK wins -- a deterministic tie-break independent of the
    feed's row order -- and a warning is emitted so the collision is visible
    instead of silently resolving to whichever row happened to come last.
    """
    data = fetcher.get_json(COMPANY_TICKERS_URL)
    # The map is keyed by arbitrary index strings: {"0": {cik_str, ticker, title}, ...}
    rows = data.values() if isinstance(data, dict) else data
    out: dict[str, Issuer] = {}
    collisions: set[str] = set()
    for row in rows:
        ticker = str(row.get("ticker", "")).upper()
        if not ticker:
            continue
        issuer = Issuer(
            cik=normalize_cik(row["cik_str"]),
            ticker=ticker,
            company=row.get("title", ""),
        )
        existing = out.get(ticker)
        if existing is None:
            out[ticker] = issuer
        elif existing.cik != issuer.cik:
            collisions.add(ticker)
            if issuer.cik < existing.cik:  # deterministic: lowest CIK wins
                out[ticker] = issuer
    if collisions:
        sample = ", ".join(sorted(collisions)[:10])
        warnings.warn(
            f"company_tickers.json: {len(collisions)} ticker(s) map to multiple "
            f"CIKs; kept the lowest CIK for each ({sample})",
            stacklevel=2,
        )
    return out


def resolve_tickers(
    tickers: Iterable[str], fetcher: Fetcher
) -> tuple[list[Issuer], list[str]]:
    """Resolve tickers to :class:`Issuer`s. Returns ``(issuers, unresolved)``.

    NOTE: ``company_tickers.json`` lists *currently* trading issuers only, so a
    ticker-built universe has **survivorship bias** — delisted, acquired, or
    failed companies (Lehman, Twitter/TWTR, Enron, …) won't resolve. For
    historical coverage, anchor on CIK via :func:`resolve_ciks` or crawl the
    full-index (see ``sources.edgar_index``).
    """
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


def resolve_ciks(ciks: Iterable[str], fetcher: Fetcher) -> list[Issuer]:
    """Build issuers directly from CIKs via the submissions API.

    Unlike :func:`resolve_tickers`, this works for delisted / merged / renamed
    issuers that no longer appear in the current ticker map — the CIK is the
    permanent anchor. The (current) name and primary ticker are attached when
    available.
    """
    # Imported here to avoid a circular import at module load.
    from .sources.edgar_submissions import SUBMISSIONS_URL

    issuers: list[Issuer] = []
    for raw in ciks:
        cik = normalize_cik(raw)
        name, ticker = "", ""
        try:
            data = fetcher.get_json(SUBMISSIONS_URL.format(cik=cik))
            name = data.get("name", "")
            tks = data.get("tickers") or []
            ticker = tks[0] if tks else ""
        except Exception:  # noqa: BLE001 - keep the CIK even if metadata is unavailable
            pass
        issuers.append(Issuer(cik=cik, ticker=ticker, company=name))
    return issuers


def issuers_from_sp500(
    fetcher: Fetcher, *, start: str | None = None, current_only: bool = False
) -> tuple[list[Issuer], list[dict], list[str]]:
    """Build an S&P 500 universe from Wikipedia composition.

    Returns ``(issuers, changes, unresolved)``. With ``current_only`` it's just
    today's members (CIK from the table). Otherwise it's the **historical union**
    over the window from ``start`` (every company that was a member), with dated
    membership (``first_seen``/``last_seen``) and the raw dated ``changes`` for
    point-in-time reconstruction. CIKs for since-removed members are resolved
    best-effort via the SEC ticker map; those still unresolved keep ``cik=""`` and
    are returned in ``unresolved``.
    """
    from .indices import sp500_current, sp500_membership

    if current_only:
        members = [{**m, "first_seen": "", "last_seen": "current"} for m in sp500_current(fetcher)]
        changes: list[dict] = []
    else:
        members, changes = sp500_membership(fetcher, start=start)

    # Resolve missing CIKs (since-removed members) via the SEC map, in one fetch.
    need = [m["ticker"] for m in members if not m.get("cik")]
    resolved: dict[str, Issuer] = {}
    if need:
        table = load_company_tickers(fetcher)
        resolved = {t: table[t] for t in need if t in table}

    issuers: list[Issuer] = []
    unresolved: list[str] = []
    for m in members:
        cik = m.get("cik") or (resolved[m["ticker"]].cik if m["ticker"] in resolved else "")
        if not cik:
            unresolved.append(m["ticker"])
        issuers.append(Issuer(cik=cik, ticker=m["ticker"], company=m.get("company", ""),
                              first_seen=m.get("first_seen", ""), last_seen=m.get("last_seen", "")))
    return issuers, changes, unresolved


class Universe:
    """Load/save committed curated issuer lists under ``data/universe/``."""

    def __init__(self, config: Config | None = None):
        self.config = config or Config()

    def path(self, name: str) -> Path:
        return self.config.universe_dir / f"{name}.jsonl"

    def save(self, name: str, issuers: Iterable[Issuer]) -> Path:
        path = self.path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        # De-duplicate by CIK when present, else by ticker (unresolved members),
        # preserving order.
        seen: set[str] = set()
        with path.open("w", encoding="utf-8") as fh:
            for issuer in issuers:
                key = issuer.cik or f"ticker:{issuer.ticker}"
                if key in seen:
                    continue
                seen.add(key)
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
            issuers.append(Issuer(
                cik=row.get("cik", ""), ticker=row.get("ticker", ""),
                company=row.get("company", ""),
                first_seen=row.get("first_seen", ""), last_seen=row.get("last_seen", ""),
            ))
        return issuers

    def names(self) -> list[str]:
        d = self.config.universe_dir
        if not d.exists():
            return []
        return sorted(p.stem for p in d.glob("*.jsonl"))

    def iter_ciks(self, name: str) -> Iterator[str]:
        for issuer in self.load(name):
            if issuer.cik:  # skip unresolved (CIK-less) members
                yield issuer.cik
