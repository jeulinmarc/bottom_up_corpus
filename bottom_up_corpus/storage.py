"""Manifest storage, deduplication, and the discovery-error audit trail.

Parallels ``cb_corpus.storage`` (manifest portion). Records are kept in
per-issuer JSONL at ``data/manifest/<cik>.jsonl``, keyed by the stable
``doc_id``. Saving is idempotent: an existing ``doc_id`` is updated in place
(metadata corrections) rather than duplicated. Raw-file download lands in
Phase 2; this module owns the metadata layer.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from .config import Config, normalize_cik
from .models import FilingRecord


@dataclass
class SaveStats:
    """Outcome of merging a batch of records into a manifest."""

    seen: int = 0
    added: int = 0
    updated: int = 0
    unchanged: int = 0

    def __iadd__(self, other: "SaveStats") -> "SaveStats":
        self.seen += other.seen
        self.added += other.added
        self.updated += other.updated
        self.unchanged += other.unchanged
        return self


class Storage:
    """Read/write per-issuer manifests and append discovery errors."""

    def __init__(self, config: Config | None = None):
        self.config = config or Config()

    # ---- manifests ----
    def load_manifest(self, cik: str) -> dict[str, FilingRecord]:
        """Return ``{doc_id: FilingRecord}`` for an issuer (empty if none)."""
        path = self.config.manifest_file(cik)
        records: dict[str, FilingRecord] = {}
        if not path.exists():
            return records
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            rec = FilingRecord.from_row(json.loads(line))
            records[rec.doc_id] = rec
        return records

    def save_records(
        self, records: Iterable[FilingRecord], *, dry_run: bool = True
    ) -> SaveStats:
        """Merge ``records`` into their per-issuer manifests.

        With ``dry_run=True`` (default) nothing is written; the returned stats
        still reflect what *would* change. Records are grouped by CIK so a batch
        spanning issuers is handled in one call.
        """
        by_cik: dict[str, list[FilingRecord]] = {}
        for rec in records:
            by_cik.setdefault(normalize_cik(rec.cik), []).append(rec)

        total = SaveStats()
        for cik, recs in by_cik.items():
            total += self._save_cik(cik, recs, dry_run=dry_run)
        return total

    def _save_cik(
        self, cik: str, records: list[FilingRecord], *, dry_run: bool
    ) -> SaveStats:
        existing = self.load_manifest(cik)
        stats = SaveStats(seen=len(records))
        changed = False
        for rec in records:
            prior = existing.get(rec.doc_id)
            if prior is None:
                existing[rec.doc_id] = rec
                stats.added += 1
                changed = True
            elif prior.to_row() != rec.to_row():
                existing[rec.doc_id] = rec
                stats.updated += 1
                changed = True
            else:
                stats.unchanged += 1

        if changed and not dry_run:
            self._write_manifest(cik, existing.values())
        return stats

    def _write_manifest(self, cik: str, records: Iterable[FilingRecord]) -> None:
        path = self.config.manifest_file(cik)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Deterministic order: by filing date then accession, for stable diffs.
        ordered = sorted(
            records,
            key=lambda r: (r.filing_date or date.min, r.accession),
        )
        with path.open("w", encoding="utf-8") as fh:
            for rec in ordered:
                fh.write(json.dumps(rec.to_row(), ensure_ascii=False) + "\n")

    # ---- discovery errors ----
    def record_errors(self, errors: Iterable[dict]) -> int:
        """Append discovery errors to the audit trail. Returns count written."""
        errors = list(errors)
        if not errors:
            return 0
        path = self.config.discovery_errors_path
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            for err in errors:
                fh.write(json.dumps(err, ensure_ascii=False) + "\n")
        return len(errors)
