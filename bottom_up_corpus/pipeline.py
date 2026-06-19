"""Discovery orchestration with idempotent convergence.

Parallels ``cb_corpus.pipeline.run``. For each issuer in the universe, discover
filings (metadata only — downloads land in Phase 2) and merge them into the
per-issuer manifest. Multi-round crawling repeats until a round adds/updates
nothing and produces no new errors, matching cb_corpus's convergence contract.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date

from .config import Config
from .http import Fetcher
from .sources.edgar_submissions import EdgarSubmissions
from .storage import SaveStats, Storage
from .taxonomy import FULL_SCOPE, FormType


@dataclass
class RunReport:
    """Aggregate outcome of a discovery run."""

    rounds: int = 0
    issuers: int = 0
    stats: SaveStats = field(default_factory=SaveStats)
    errors: list[dict] = field(default_factory=list)


def discover_universe(
    ciks: Iterable[str],
    *,
    scope: Sequence[FormType] = FULL_SCOPE,
    since: date | None = None,
    dry_run: bool = True,
    max_rounds: int = 1,
    config: Config | None = None,
    fetcher: Fetcher | None = None,
    storage: Storage | None = None,
) -> RunReport:
    """Discover filings for every CIK and merge into manifests.

    Idempotent: re-running with the same inputs converges (no changes). With
    ``dry_run=True`` nothing is persisted but the report reflects what would
    change.
    """
    config = config or Config()
    fetcher = fetcher or Fetcher(config)
    storage = storage or Storage(config)
    cik_list = list(ciks)

    report = RunReport(issuers=len(cik_list))
    for round_no in range(1, max_rounds + 1):
        report.rounds = round_no
        round_stats = SaveStats()
        round_errors: list[dict] = []

        for cik in cik_list:
            source = EdgarSubmissions(fetcher=fetcher, config=config)
            records = list(source.discover(cik, scope=scope, since=since))
            round_stats += storage.save_records(records, dry_run=dry_run)
            round_errors.extend(source.errors)

        report.stats += round_stats
        if round_errors:
            report.errors.extend(round_errors)
            if not dry_run:
                storage.record_errors(round_errors)

        # Converged: a round changed nothing and hit no errors.
        if round_stats.added == 0 and round_stats.updated == 0 and not round_errors:
            break

    return report
