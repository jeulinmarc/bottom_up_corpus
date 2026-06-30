import json

from bottom_up_corpus.config import Config
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


class _BrregFetcher:
    def __init__(self, entries): self._e = entries
    def get_json(self, url, **kw): return self._e   # the brreg accounts list

def test_build_register_financials_writes_rows(tmp_path):
    from bottom_up_corpus.registers.financials import build_register_financials
    fetcher = _BrregFetcher([_ENTRY])
    cfg = Config(data_dir=tmp_path)
    rep = build_register_financials([{"orgnr": "999"}], fetcher=fetcher, config=cfg, write=True)
    assert rep["with_financials"] == 1 and rep["periods"] == 1
    rows = [json.loads(x) for x in (tmp_path / "financials_register" / "999.jsonl").read_text().splitlines()]
    rev = next(r for r in rows if r["kind"] == "reported" and r["concept"] == "revenue")
    assert rev["value"] == 1000 and rev["entity_id"] == "999" and rev["source"] == "brreg"
    assert rev["basis"] == "consolidated" and rev["currency"] == "NOK"
    d2e = next(r for r in rows if r["kind"] == "derived" and r["concept"] == "debt_to_equity")
    assert abs(d2e["value"] - (3500 / 1500)) < 1e-6   # total liabilities / equity (NGAAP gearing)

def test_dry_run_writes_nothing(tmp_path):
    from bottom_up_corpus.registers.financials import build_register_financials
    cfg = Config(data_dir=tmp_path)
    rep = build_register_financials([{"orgnr": "999"}], fetcher=_BrregFetcher([_ENTRY]), config=cfg, write=False)
    assert rep["coverage_path"] is None and not (tmp_path / "financials_register").exists()


from bottom_up_corpus import cli

def test_register_financials_cli(monkeypatch, tmp_path):
    captured = {}
    def fake(specs, *, fetcher, config, write):
        captured.update(specs=specs, write=write)
        return {"entities": 1, "with_financials": 1, "periods": 3, "coverage_path": None, "paths": []}
    monkeypatch.setattr(cli, "build_register_financials", fake)
    args = cli.build_parser().parse_args(["register-financials", "--orgnrs", "1,2", "--write"])
    assert args.func(args) == 0
    assert captured["specs"] == [{"orgnr": "1"}, {"orgnr": "2"}] and captured["write"] is True
