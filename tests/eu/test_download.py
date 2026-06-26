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
