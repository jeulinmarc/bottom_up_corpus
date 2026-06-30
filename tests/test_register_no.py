from bottom_up_corpus.registers.concepts_no import map_brreg_entry
from bottom_up_corpus.registers.identity import resolve_register_specs

_ENTRY = {
    "regnskapsperiode": {"fraDato": "2024-01-01", "tilDato": "2024-12-31"},
    "regnskapstype": "KONSERN", "valuta": "NOK",
    "resultatregnskapResultat": {"sumDriftsinntekter": 1000, "driftsresultat": 200,
        "ordinaertResultatFoerSkattekostnad": 180, "ordinaertResultatSkattekostnad": 40,
        "aarsresultat": 140},
    "eiendeler": {"sumEiendeler": 5000, "sumOmloepsmidler": 2000,
        "sumBankinnskuddOgKontanter": 600, "sumVarer": 300, "sumFordringer": 700},
    "egenkapitalGjeld": {"sumEgenkapital": 1500, "sumGjeld": 3500,
        "sumKortsiktigGjeld": 1200, "sumLangsiktigGjeld": 2300},
}

def test_map_brreg_entry_maps_fields_basis_currency():
    m = map_brreg_entry(_ENTRY)
    assert m["period_end"] == "2024-12-31"
    assert m["basis"] == "consolidated"        # KONSERN
    assert m["currency"] == "NOK"
    v = m["values"]
    assert v["revenue"]["value"] == 1000 and v["revenue"]["tag"] == "sumDriftsinntekter"
    assert v["net_income"]["value"] == 140
    assert v["assets"]["value"] == 5000 and v["equity"]["value"] == 1500
    assert v["short_term_debt"]["value"] == 1200 and v["long_term_debt"]["value"] == 2300

def test_selskap_is_company_basis():
    m = map_brreg_entry({**_ENTRY, "regnskapstype": "SELSKAP"})
    assert m["basis"] == "company"

def test_no_period_returns_none():
    assert map_brreg_entry({"regnskapstype": "KONSERN"}) is None


class _GleifFetcher:
    def __init__(self, country, registered_as):
        self._c, self._r = country, registered_as
    def get_json(self, url, **kw):
        return {"data": {"attributes": {"entity": {
            "legalName": {"name": "ACME NORGE AS"},
            "legalAddress": {"country": self._c}, "registeredAs": self._r}}}}

def test_orgnr_passes_through():
    r = resolve_register_specs([{"orgnr": "923609016"}], fetcher=None)[0]
    assert r["orgnr"] == "923609016" and r["status"] == "ok" and r["country"] == "NO"

def test_lei_resolves_via_gleif_registeredas():
    r = resolve_register_specs([{"lei": "L1"}], fetcher=_GleifFetcher("NO", "NO 923 609 016"))[0]
    assert r["orgnr"] == "923609016" and r["lei"] == "L1" and r["status"] == "ok"

def test_non_norwegian_lei_is_unresolved():
    r = resolve_register_specs([{"lei": "L2"}], fetcher=_GleifFetcher("SE", "5560000000"))[0]
    assert r["orgnr"] is None and r["status"] == "unresolved"

def test_gleif_exception_is_unresolved():
    class _Bad:
        def get_json(self, url, **kw):
            raise OSError("timeout")
    r = resolve_register_specs([{"lei": "L3"}], fetcher=_Bad())[0]
    assert r["orgnr"] is None and r["status"] == "unresolved"
