from datetime import date
from bottom_up_corpus.config import Config
from bottom_up_corpus.eu import acquire as acq
from bottom_up_corpus.eu.documents import Document
from bottom_up_corpus.eu.entities import Entity


def test_acquire_resolves_dispatches_merges_and_reconciles(monkeypatch, tmp_path):
    cfg = Config(data_dir=tmp_path / "data", contact="t@e.com")
    ent = Entity("L1", "SAP SE", "DE", resolution="lei")
    monkeypatch.setattr(acq, "resolve_entities", lambda specs, *, fetcher: [ent])

    class _Backend:
        def __init__(self, *a, **k): self.errors = []
        def discover(self, e):
            return [Document("de-1", "L1", "DE", "annual_report", date(2023, 12, 31),
                             None, "x", "de", "oam-de", [{"name": "r", "sha256": "h"}], {})]
    monkeypatch.setattr(acq, "COUNTRY_BACKENDS", {"DE": _Backend})
    monkeypatch.setattr(acq, "FilingsXbrlOrg", _Backend)

    summary = acq.acquire([{"lei": "L1"}], fetcher=object(), config=cfg, download=False)
    assert summary["entities"] == 1
    assert summary["documents"] == 1  # both backends return the same doc -> deduped to 1
    assert (cfg.data_dir / "reports" / "eu_coverage.jsonl").exists()


def test_acquire_surfaces_download_errors(monkeypatch, tmp_path):
    """acquire() must record per-file download failures in summary['errors']
    and expose them via download_errors count — never silently drop them."""
    cfg = Config(data_dir=tmp_path / "data", contact="t@e.com")
    ent = Entity("L2", "Test Corp", "FR", resolution="lei")
    monkeypatch.setattr(acq, "resolve_entities", lambda specs, *, fetcher: [ent])

    doc = Document(
        "fr-bad", "L2", "FR", "annual_report", date(2023, 12, 31),
        None, "x", "fr", "oam-fr",
        [{"name": "bad.pdf", "url": "https://example.com/bad.pdf", "kind": "document"}],
        {}
    )

    class _DiscoverBackend:
        def __init__(self, *a, **k): self.errors = []
        def discover(self, e): return [doc]

    monkeypatch.setattr(acq, "COUNTRY_BACKENDS", {"FR": _DiscoverBackend})
    monkeypatch.setattr(acq, "FilingsXbrlOrg", _DiscoverBackend)

    # Fake fetcher whose download always raises
    class _FailFetcher:
        def download(self, url, dest): raise RuntimeError("network unreachable")

    # Monkeypatch download_document to simulate a file-level error in the manifest
    def _fake_download(doc, *, fetcher, config):
        return {
            "doc_id": doc.doc_id,
            "files": [{"name": "bad.pdf", "url": "https://example.com/bad.pdf",
                       "error": "network unreachable"}],
        }

    monkeypatch.setattr(acq, "download_document", _fake_download)

    summary = acq.acquire([{"lei": "L2"}], fetcher=_FailFetcher(), config=cfg, download=True)

    assert summary["download_errors"] >= 1
    assert any(e.get("context") == "download" for e in summary["errors"])
