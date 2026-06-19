"""Manifest storage, deduplication, and the discovery-error audit trail.

Parallels ``cb_corpus.storage`` (manifest portion). Records are kept in
per-issuer JSONL at ``data/manifest/<cik>.jsonl``, keyed by the stable
``doc_id``. Saving is idempotent: an existing ``doc_id`` is updated in place
(metadata corrections) rather than duplicated. Raw-file download lands in
Phase 2; this module owns the metadata layer.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .config import Config, normalize_cik
from .extract import clean_text
from .models import FilingRecord
from .submission import filename_from_url, parse_submission, select_primary


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


@dataclass
class DownloadResult:
    """Outcome of fetching + decomposing a single filing."""

    doc_id: str
    status: str  # downloaded | skipped | would-download | empty | error
    bytes: int = 0
    error: str | None = None


@dataclass
class RenderResult:
    """Outcome of rendering a single filing's primary document to PDF."""

    doc_id: str
    status: str  # rendered | skipped | would-render | no-primary | error
    error: str | None = None


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

    # ---- download + decomposition (Phase 2) ----
    def raw_dir_for(self, record: FilingRecord) -> Path:
        year = str(record.year) if record.year else "unknown"
        return self.config.raw_dir / record.cik / record.form_type.code / year

    def _rel(self, path: Path) -> str:
        return str(path.relative_to(self.config.data_dir))

    def fetch_and_store(
        self,
        record: FilingRecord,
        fetcher,
        *,
        dry_run: bool = False,
        overwrite: bool = False,
    ) -> DownloadResult:
        """Download a filing's complete submission and decompose it.

        Writes three layered artifacts under ``data/raw/<cik>/<form>/<year>/``:
        the full submission (``.submission.txt``), the decomposed primary
        document (``.primary<ext>``), and cleaned text (``.txt``). Mutates
        ``record`` with the resulting paths + sha256. Idempotent: an existing
        submission is skipped unless ``overwrite`` is set.
        """
        dest_dir = self.raw_dir_for(record)
        sub_path = dest_dir / f"{record.doc_id}.submission.txt"

        if sub_path.exists() and not overwrite:
            record.local_path = self._rel(sub_path)
            return DownloadResult(record.doc_id, "skipped")
        if dry_run:
            return DownloadResult(record.doc_id, "would-download")
        if not record.submission_url:
            return DownloadResult(record.doc_id, "error", error="no submission_url")

        try:
            raw = fetcher.get_text(record.submission_url)
        except Exception as exc:  # noqa: BLE001
            return DownloadResult(record.doc_id, "error", error=str(exc))

        data = raw.encode("utf-8", "replace")
        dest_dir.mkdir(parents=True, exist_ok=True)
        sub_path.write_text(raw, encoding="utf-8")
        record.local_path = self._rel(sub_path)
        record.sha256 = hashlib.sha256(data).hexdigest()

        primary = select_primary(
            parse_submission(raw),
            primary_filename=filename_from_url(record.primary_doc_url),
            sec_form=record.sec_form,
        )
        if primary and primary.text:
            ext = Path(primary.filename).suffix or ".txt"
            primary_path = dest_dir / f"{record.doc_id}.primary{ext}"
            primary_path.write_text(primary.text, encoding="utf-8")
            record.primary_path = self._rel(primary_path)

            text_path = dest_dir / f"{record.doc_id}.txt"
            text_path.write_text(clean_text(primary.text, primary.filename), encoding="utf-8")
            record.text_path = self._rel(text_path)

        return DownloadResult(record.doc_id, "downloaded", bytes=len(data))

    # ---- PDF rendering (Phase 3, separate batch) ----
    def render_record(
        self,
        record: FilingRecord,
        renderer,
        *,
        dry_run: bool = False,
        overwrite: bool = False,
    ) -> RenderResult:
        """Render a filing's primary document to PDF via ``renderer``.

        ``renderer`` is a ``Callable[[Path, Path], None]`` (see
        :func:`bottom_up_corpus.render.make_chrome_renderer`). Requires the
        primary document to have been downloaded (Phase 2). Mutates ``record``
        with ``pdf_path``. Idempotent: an existing PDF is skipped unless
        ``overwrite``.
        """
        if not record.primary_path:
            return RenderResult(record.doc_id, "no-primary")

        src = self.config.data_dir / record.primary_path
        if not src.exists():
            return RenderResult(record.doc_id, "no-primary",
                                error=f"primary not on disk: {record.primary_path}")

        pdf_path = self.raw_dir_for(record) / f"{record.doc_id}.pdf"
        if pdf_path.exists() and not overwrite:
            record.pdf_path = self._rel(pdf_path)
            return RenderResult(record.doc_id, "skipped")
        if dry_run:
            return RenderResult(record.doc_id, "would-render")

        try:
            renderer(src, pdf_path)
        except Exception as exc:  # noqa: BLE001
            return RenderResult(record.doc_id, "error", error=str(exc))

        record.pdf_path = self._rel(pdf_path)
        return RenderResult(record.doc_id, "rendered")

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
