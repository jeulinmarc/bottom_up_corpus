from bottom_up_corpus.registers.concepts_no import map_brreg_entry

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
