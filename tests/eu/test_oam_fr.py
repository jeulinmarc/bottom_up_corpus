import json
from pathlib import Path
from bottom_up_corpus.eu.entities import Entity
from bottom_up_corpus.eu.sources.oam_fr import InfoFinanciereFR

FIX = Path(__file__).parent.parent / "fixtures" / "eu"


class _Fetcher:
    def __init__(self, routes): self.routes = routes
    def get_json(self, url, **_):
        for n, v in self.routes.items():
            if n in url:
                return json.loads(v) if isinstance(v, str) else v
        raise RuntimeError(url)


def test_fr_discover_maps_records_to_documents():
    f = _Fetcher({"/records": (FIX / "oam_fr_records.json").read_text()})
    src = InfoFinanciereFR(fetcher=f)
    docs = src.discover(Entity(lei="969500P31E3EW0YOR413", name="TotalEnergies SE", country="FR"))
    assert docs
    assert all(d.source == "oam-fr" and d.country == "FR" for d in docs)
    assert all(d.doc_type in __import__("bottom_up_corpus.eu.documents", fromlist=["DOC_TYPES"]).DOC_TYPES for d in docs)
    assert all(d.files and d.files[0]["url"].startswith("http") for d in docs)


def test_fr_discover_records_truncation_when_total_count_exceeds_results():
    """When total_count > len(results), the backend must record a 'truncated' error
    so the incompleteness is visible — never silent. The fixture has total_count=3782
    with only 3 results, so truncation fires."""
    f = _Fetcher({"/records": (FIX / "oam_fr_records.json").read_text()})
    src = InfoFinanciereFR(fetcher=f)
    src.discover(Entity(lei="969500P31E3EW0YOR413", name="TotalEnergies SE", country="FR"))
    assert any(e.get("context") == "truncated" for e in src.errors)


def test_fr_discover_queries_lei_and_isins():
    """When entity has ISINs, the where clause must include ISIN predicates (OR).
    Ensures pre-LEI-era records are not silently dropped."""
    captured_urls = []

    class _CaptureFetcher:
        def get_json(self, url, **_):
            captured_urls.append(url)
            return {"total_count": 0, "results": []}

    src = InfoFinanciereFR(fetcher=_CaptureFetcher())
    src.discover(Entity(lei="TESTLEI123", name="Test Co", country="FR",
                        isins=("FR0000123456", "FR0000789012")))
    assert captured_urls, "fetcher should have been called"
    url = captured_urls[0]
    assert "TESTLEI123" in url
    assert "FR0000123456" in url
    assert "FR0000789012" in url
