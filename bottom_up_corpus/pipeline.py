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


@dataclass
class DownloadReport:
    """Aggregate outcome of a download run."""

    downloaded: int = 0
    skipped: int = 0
    empty: int = 0
    errors: int = 0
    bytes: int = 0
    error_items: list[dict] = field(default_factory=list)


def download_universe(
    ciks: Iterable[str],
    *,
    scope: Sequence[FormType] | None = None,
    dry_run: bool = True,
    overwrite: bool = False,
    limit: int | None = None,
    config: Config | None = None,
    fetcher: Fetcher | None = None,
    storage: Storage | None = None,
) -> DownloadReport:
    """Download + decompose filings already present in the issuers' manifests.

    Reads each issuer's manifest, fetches the complete submission for each record
    (optionally filtered by ``scope``), decomposes it, and persists the updated
    record. ``limit`` caps the number of *new* downloads across the run (handy
    for live smoke tests). Idempotent: already-downloaded filings are skipped.
    """
    config = config or Config()
    fetcher = fetcher or Fetcher(config)
    storage = storage or Storage(config)
    scope_set = set(scope) if scope else None

    report = DownloadReport()
    for cik in ciks:
        manifest = storage.load_manifest(cik)
        records = [
            r for r in manifest.values()
            if scope_set is None or r.form_type in scope_set
        ]
        records.sort(key=lambda r: (r.filing_date or date.min), reverse=True)

        touched = []
        for rec in records:
            if limit is not None and report.downloaded >= limit:
                break
            res = storage.fetch_and_store(rec, fetcher, dry_run=dry_run, overwrite=overwrite)
            touched.append(rec)
            if res.status == "downloaded":
                report.downloaded += 1
                report.bytes += res.bytes
            elif res.status == "skipped":
                report.skipped += 1
            elif res.status == "error":
                report.errors += 1
                report.error_items.append(
                    {"source": "download", "context": rec.doc_id, "url": rec.submission_url, "error": res.error}
                )

        if not dry_run and touched:
            storage.save_records(touched, dry_run=False)

    if not dry_run and report.error_items:
        storage.record_errors(report.error_items)
    return report
