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


def test_resolve_by_name_ambiguous_country_is_unresolved():
    # The re-captured gleif_name_sap.json (page[size]=10) returns 3 records total;
    # filtering to country="DE" leaves 2 candidates (SAP SE + SAP Deutschland SE & Co. KG).
    # Under the new rule (exactly-one wins), this must be unresolved — never bind to wrong LEI.
    f = _Fetcher({"filter": (FIX / "gleif_name_sap.json").read_text()})
    [e] = resolve_entities([{"name": "SAP SE", "country": "DE"}], fetcher=f)
    assert e.lei is None
    assert e.resolution == "unresolved"


def test_ambiguous_name_resolves_unresolved():
    # Two records both with country="FR" → ambiguous → unresolved.
    f = _Fetcher({"filter": (FIX / "gleif_name_ambiguous.json").read_text()})
    [e] = resolve_entities([{"name": "Foo", "country": "FR"}], fetcher=f)
    assert e.lei is None
    assert e.resolution == "unresolved"


def test_unique_name_resolves_with_name_tier():
    # Single record with country="FR" → exactly one candidate → resolves.
    f = _Fetcher({"filter": (FIX / "gleif_name_unique.json").read_text()})
    [e] = resolve_entities([{"name": "UniqueBar SA", "country": "FR"}], fetcher=f)
    assert e.lei == "FR0000000000000000C3"
    assert e.resolution == "name"


def test_unresolvable_is_recorded_not_guessed():
    f = _Fetcher({"filter": json.dumps({"data": [], "meta": {"pagination": {"total": 0}}})})
    [e] = resolve_entities([{"name": "Nonexistent Co", "country": "FR"}], fetcher=f)
    assert e.lei is None and e.resolution == "unresolved"


def test_resolve_by_isin_uses_gleif_isin_filter():
    f = _Fetcher({"filter%5Bisin%5D": (FIX / "gleif_isin_sap.json").read_text(),
                  "/isins": json.dumps({"data": []})})
    [e] = resolve_entities([{"isin": "DE0007164600"}], fetcher=f)
    assert e.lei == "529900D6BF99LW9R2E68"
    assert e.name == "SAP SE"
    assert e.country == "DE"
    assert e.resolution == "isin"
    # The ISIN we resolved by is always carried (seed), even if GLEIF lists none extra.
    assert "DE0007164600" in e.isins


def test_resolve_by_lei_populates_isins_from_gleif():
    # Route the LEI record AND the LEI->isins relationship (order matters: /isins first
    # so it isn't shadowed by the broader lei-records/<lei> route).
    f = _Fetcher({
        "/isins": (FIX / "gleif_isins_abinbev.json").read_text(),
        "lei-records/5493008H3828EMEXB082": (FIX / "gleif_lei_sap.json").read_text(),
    })
    [e] = resolve_entities([{"lei": "5493008H3828EMEXB082"}], fetcher=f)
    assert len(e.isins) == 24, "all of the issuer's ISINs should be carried"
    assert "BE0974293251" in e.isins, "the equity ISIN (STORI search key) must be present"


def test_populate_isins_false_skips_the_extra_call():
    f = _Fetcher({"lei-records/5493008H3828EMEXB082": (FIX / "gleif_lei_sap.json").read_text()})
    [e] = resolve_entities([{"lei": "5493008H3828EMEXB082"}], fetcher=f, populate_isins=False)
    assert e.isins == ()
    assert not any("/isins" in c for c in f.calls), "no LEI->isins call when disabled"


def test_isin_population_caps_the_count(monkeypatch):
    import bottom_up_corpus.eu.entities as ent
    monkeypatch.setattr(ent, "_ISIN_CAP", 5)
    f = _Fetcher({
        "/isins": (FIX / "gleif_isins_abinbev.json").read_text(),
        "lei-records/X": (FIX / "gleif_lei_sap.json").read_text(),
    })
    [e] = resolve_entities([{"lei": "X"}], fetcher=f)
    assert len(e.isins) == 5
