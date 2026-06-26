from datetime import date
from pathlib import Path
import json
from bottom_up_corpus.config import Config
from bottom_up_corpus.eu.documents import Document
from bottom_up_corpus.eu.download import download_document


class _DLFetcher:
    def download(self, url, dest, **_):
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"PKG" + url.encode())
        return len(b"PKG" + url.encode())


class _FailingFetcher:
    """Fetcher whose download always raises, simulating a mid-stream failure."""
    def download(self, url, dest, **_):
        # Write partial content to dest (simulating corruption) then raise.
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"PARTIAL")
        raise OSError("connection reset")


def test_atomic_download_no_truncated_file_on_failure(tmp_path):
    """A failing download must leave NO file at dest — only a manifest error entry.
    The atomic .part + os.replace pattern ensures a truncated artifact can never
    be trusted by the idempotency check (dest.exists())."""
    cfg = Config(data_dir=tmp_path / "data", contact="t@e.com")
    doc = Document(doc_id="atomic-1", lei="L2", country="DE", doc_type="annual_report",
                   period_end=date(2023, 12, 31), published_ts="2024-03-01", discovered_ts="x",
                   language="de", source="filings.xbrl.org",
                   files=[{"name": "report.html", "url": "http://x/report.html", "kind": "report_url"}],
                   native_meta={})
    man = download_document(doc, fetcher=_FailingFetcher(), config=cfg)
    dest = cfg.raw_dir / "L2" / "ESEF-AR" / "2023" / "atomic-1" / "report.html"
    # dest must NOT exist — the failed .part file must have been cleaned up
    assert not dest.exists(), "truncated file must not survive a download failure"
    # The .part file must also be gone
    part = dest.with_name(dest.name + ".part")
    assert not part.exists(), ".part temp file must be cleaned up on failure"
    # The manifest must record the error so the failure is visible
    assert len(man["files"]) == 1 and "error" in man["files"][0]


def test_download_writes_all_files_and_manifest(tmp_path):
    cfg = Config(data_dir=tmp_path / "data", contact="t@e.com")
    doc = Document(doc_id="fxo-1", lei="L1", country="DE", doc_type="annual_report",
                   period_end=date(2023, 12, 31), published_ts="2024-03-01", discovered_ts="x",
                   language="de", source="filings.xbrl.org",
                   files=[{"name": "a.zip", "url": "http://x/a.zip", "kind": "package_url"},
                          {"name": "r.html", "url": "http://x/r.html", "kind": "report_url"}],
                   native_meta={})
    man = download_document(doc, fetcher=_DLFetcher(), config=cfg)
    base = cfg.raw_dir / "L1" / "ESEF-AR" / "2023" / "fxo-1"
    assert (base / "a.zip").exists() and (base / "r.html").exists()
    assert len(man["files"]) == 2 and all(f["sha256"] for f in man["files"])
    mpath = cfg.data_dir / "manifest" / "L1" / "fxo-1.json"
    assert mpath.exists() and json.loads(mpath.read_text())["source"] == "filings.xbrl.org"


class _NoNetFetcher:
    """Fetcher whose .download must NEVER be called (inline-content path)."""
    def download(self, url, dest, **_):
        raise AssertionError("download() must not be called when content is inline")


def test_inline_content_is_written_without_fetching(tmp_path):
    """A file carrying inline `content` (e.g. Bundesanzeiger session-bound capture) is
    written directly; the network is never touched and `content` never leaks to the manifest."""
    cfg = Config(data_dir=tmp_path / "data", contact="t@e.com")
    html = "<html><body>Dividendenbekanntmachung SAP SE</body></html>"
    doc = Document(doc_id="de-1", lei="L9", country="DE", doc_type="inside_information",
                   period_end=date(2023, 6, 1), published_ts="2023-06-01", discovered_ts="x",
                   language="de", source="oam-de",
                   files=[{"name": "publication.html", "kind": "html",
                           "url": "https://www.bundesanzeiger.de/pub/de/suchen2?2-1.-ephemeral",
                           "content": html}],
                   native_meta={})
    man = download_document(doc, fetcher=_NoNetFetcher(), config=cfg)
    dest = cfg.raw_dir / "L9" / "MAR" / "2023" / "de-1" / "publication.html"
    assert dest.exists() and dest.read_text() == html
    f = man["files"][0]
    assert f["sha256"] and "content" not in f and f["kind"] == "html"
    assert f["url"].endswith("ephemeral"), "ephemeral url kept for provenance"
