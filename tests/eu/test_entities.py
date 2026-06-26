import json
from pathlib import Path
import pytest
from bottom_up_corpus.eu.entities import Entity, resolve_entities

FIX = Path(__file__).parent.parent / "fixtures" / "eu"


class _Fetcher:
    def __init__(self, routes): self.routes = routes; self.calls = []
    def get_json(self, url, **_):
        self.calls.append(url)
        for needle, val in self.routes.items():
            if needle in url:
                return json.loads(val) if isinstance(val, str) else val
        raise RuntimeError(f"no route for {url}")


def test_resolve_by_lei_uses_gleif_record():
    f = _Fetcher({"lei-records/529900D6BF99LW9R2E68": (FIX / "gleif_lei_sap.json").read_text()})
    [e] = resolve_entities([{"lei": "529900D6BF99LW9R2E68"}], fetcher=f)
    assert e.lei == "529900D6BF99LW9R2E68"
    assert e.name == "SAP SE"
    assert e.country == "DE"
    assert e.resolution == "lei"


def test_resolve_by_name_picks_single_match():
    f = _Fetcher({"filter": (FIX / "gleif_name_sap.json").read_text()})
    [e] = resolve_entities([{"name": "SAP SE", "country": "DE"}], fetcher=f)
    assert e.lei == "529900D6BF99LW9R2E68"
    assert e.resolution == "name"


def test_unresolvable_is_recorded_not_guessed():
    f = _Fetcher({"filter": json.dumps({"data": [], "meta": {"pagination": {"total": 0}}})})
    [e] = resolve_entities([{"name": "Nonexistent Co", "country": "FR"}], fetcher=f)
    assert e.lei is None and e.resolution == "unresolved"


def test_resolve_by_isin_uses_gleif_isin_filter():
    f = _Fetcher({"filter%5Bisin%5D": (FIX / "gleif_isin_sap.json").read_text()})
    [e] = resolve_entities([{"isin": "DE0007164600"}], fetcher=f)
    assert e.lei == "529900D6BF99LW9R2E68"
    assert e.name == "SAP SE"
    assert e.country == "DE"
    assert e.resolution == "isin"
