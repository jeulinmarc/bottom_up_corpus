import copy
import json

from bottom_up_corpus.config import Config
from bottom_up_corpus.registers.concepts_no import map_brreg_entry
from bottom_up_corpus.registers.identity import resolve_register_specs

# --- Canonical fixtures -------------------------------------------------------
# Two real Brreg entries (Equinor, orgnr 923609016, FY2022), trimmed from the 6
# real entries the live API returns but with the REAL nesting preserved verbatim.
# This exercises the recursive `_leaves` flatten + last-wins dedup, including the
# `driftsresultat.driftsresultat` collision (a scalar named the same as its parent
# object). All values were validated against the live API.
_KONSERN_2022 = {
    "id": 4080685,
    "journalnr": "2023469485",
    "regnskapstype": "KONSERN",
    "regnskapsperiode": {"fraDato": "2022-01-01", "tilDato": "2022-12-31"},
    "valuta": "USD",
    "egenkapitalGjeld": {
        "sumEgenkapitalGjeld": 158021000000.0,
        "egenkapital": {
            "sumEgenkapital": 53989000000.0,
            "opptjentEgenkapital": {},
            "innskuttEgenkapital": {"sumInnskuttEgenkaptial": 53988000000.0},
        },
        "gjeldOversikt": {
            "sumGjeld": 104032000000.0,
            "kortsiktigGjeld": {"sumKortsiktigGjeld": 43806000000.0},
            "langsiktigGjeld": {"sumLangsiktigGjeld": 60226000000.0},
        },
    },
    "eiendeler": {
        "sumVarer": 5205000000.0,
        "sumFordringer": 22452000000.0,
        "sumInvesteringer": 33915000000.0,
        "sumBankinnskuddOgKontanter": 16598000000.0,
        "sumEiendeler": 158021000000.0,
        "omloepsmidler": {"sumOmloepsmidler": 78170000000.0},
        "anleggsmidler": {"sumAnleggsmidler": 79851000000.0},
    },
    "resultatregnskapResultat": {
        "ordinaertResultatFoerSkattekostnad": 78604000000.0,
        "ordinaertResultatSkattekostnad": 49860000000.0,
        "aarsresultat": 28744000000.0,
        "finansresultat": {
            "nettoFinans": -207000000.0,
            "finansinntekt": {},
            "finanskostnad": {
                "annenRentekostnad": 1379000000.0,   # gross "other" interest
                "sumFinanskostnad": 207000000.0,      # NET aggregate (|nettoFinans|)
            },
        },
        "driftsresultat": {
            "driftsresultat": 78811000000.0,           # scalar named like its parent
            "driftsinntekter": {
                "salgsinntekter": 149004000000.0,
                "sumDriftsinntekter": 150806000000.0,
            },
            "driftskostnad": {"sumDriftskostnad": 71995000000.0},
        },
    },
}

_SELSKAP_2022 = {
    "id": 4080683,
    "journalnr": "2023469485",
    "regnskapstype": "SELSKAP",
    "regnskapsperiode": {"fraDato": "2022-01-01", "tilDato": "2022-12-31"},
    "valuta": "USD",
    "egenkapitalGjeld": {
        "sumEgenkapitalGjeld": 159342000000.0,
        "egenkapital": {
            "sumEgenkapital": 50914000000.0,
            "opptjentEgenkapital": {"sumOpptjentEgenkapital": 49772000000.0},
            "innskuttEgenkapital": {"sumInnskuttEgenkaptial": 1142000000.0},
        },
        "gjeldOversikt": {
            "sumGjeld": 108428000000.0,
            "kortsiktigGjeld": {"sumKortsiktigGjeld": 74830000000.0},
            "langsiktigGjeld": {"sumLangsiktigGjeld": 33598000000.0},
        },
    },
    "eiendeler": {
        "sumVarer": 1771000000.0,
        "sumFordringer": 40603000000.0,
        "sumInvesteringer": 30445000000.0,
        "sumBankinnskuddOgKontanter": 10204000000.0,
        "sumEiendeler": 159342000000.0,
        "omloepsmidler": {"sumOmloepsmidler": 83023000000.0},
        "anleggsmidler": {"sumAnleggsmidler": 76319000000.0},
    },
    "resultatregnskapResultat": {
        "ordinaertResultatFoerSkattekostnad": 27478000000.0,
        "ordinaertResultatSkattekostnad": -68000000.0,
        "aarsresultat": 27546000000.0,
        "finansresultat": {
            "nettoFinans": -887000000.0,
            "finansinntekt": {},
            # Only sumFinanskostnad, NO annenRentekostnad -> the F1 case.
            "finanskostnad": {"sumFinanskostnad": 887000000.0},
        },
        "driftsresultat": {
            "driftsresultat": 28365000000.0,
            "driftsinntekter": {
                "salgsinntekter": 68154000000.0,
                "sumDriftsinntekter": 96784000000.0,
            },
            "driftskostnad": {"sumDriftskostnad": 68419000000.0},
        },
    },
}


# --- Mapping (the recursive flatten + last-wins, over the real nesting) --------
def test_map_konsern_real_nested_values():
    m = map_brreg_entry(_KONSERN_2022)
    assert m["period_end"] == "2022-12-31"
    assert m["basis"] == "consolidated"        # KONSERN
    assert m["currency"] == "USD"
    v = m["values"]
    # (a) operating_income resolves to the scalar `driftsresultat`, not the parent
    #     object of the same name (the collision resolves to the numeric leaf).
    assert v["operating_income"]["value"] == 78811000000
    assert v["operating_income"]["tag"] == "driftsresultat"
    # (b) revenue resolves to the *nested* …driftsinntekter.sumDriftsinntekter
    assert v["revenue"]["value"] == 150806000000
    assert v["revenue"]["tag"] == "sumDriftsinntekter"
    # (c) nested sub-object leaves are picked up from their sub-objects
    assert v["assets_current"]["value"] == 78170000000      # omloepsmidler.sumOmloepsmidler
    assert v["assets_current"]["tag"] == "sumOmloepsmidler"
    assert v["equity"]["value"] == 53989000000              # egenkapital.sumEgenkapital
    assert v["equity"]["tag"] == "sumEgenkapital"
    assert v["liabilities"]["value"] == 104032000000        # gjeldOversikt.sumGjeld
    assert v["liabilities"]["tag"] == "sumGjeld"
    # headline + debt components
    assert v["net_income"]["value"] == 28744000000
    assert v["assets"]["value"] == 158021000000
    assert v["short_term_debt"]["value"] == 43806000000
    assert v["long_term_debt"]["value"] == 60226000000
    # F1: gross interest only (annenRentekostnad), not the net sumFinanskostnad
    assert v["interest_expense"]["value"] == 1379000000
    assert v["interest_expense"]["tag"] == "annenRentekostnad"


def test_map_selskap_real_values_and_basis():
    m = map_brreg_entry(_SELSKAP_2022)
    assert m["basis"] == "company"             # SELSKAP
    assert m["currency"] == "USD"
    v = m["values"]
    assert v["revenue"]["value"] == 96784000000
    assert v["net_income"]["value"] == 27546000000
    assert v["assets"]["value"] == 159342000000
    assert v["equity"]["value"] == 50914000000
    assert v["liabilities"]["value"] == 108428000000


def test_f1_interest_expense_drops_net_finanskostnad():
    # SELSKAP has only `sumFinanskostnad` (a net figure) and no `annenRentekostnad`:
    # interest_expense must be ABSENT, never silently mapped to the net figure.
    m = map_brreg_entry(_SELSKAP_2022)
    assert "interest_expense" not in m["values"]


def test_no_period_returns_none():
    assert map_brreg_entry({"regnskapstype": "KONSERN"}) is None


def test_f6_non_iso_tildato_returns_none():
    bad = {**_KONSERN_2022, "regnskapsperiode": {"tilDato": "31.12.2024"}}
    assert map_brreg_entry(bad) is None


# --- Identity resolution ------------------------------------------------------
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


# --- Producer -----------------------------------------------------------------
class _BrregFetcher:
    def __init__(self, entries): self._e = entries
    def get_json(self, url, **kw): return self._e   # the brreg accounts list


def _run(tmp_path, entries, orgnr="923609016", write=True):
    from bottom_up_corpus.registers.financials import build_register_financials
    cfg = Config(data_dir=tmp_path)
    rep = build_register_financials([{"orgnr": orgnr}], fetcher=_BrregFetcher(entries),
                                    config=cfg, write=write)
    path = tmp_path / "financials_register" / f"{orgnr}.jsonl"
    rows = [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []
    return rep, rows


def test_build_register_financials_real_fixture(tmp_path):
    rep, rows = _run(tmp_path, [_KONSERN_2022, _SELSKAP_2022])
    assert rep["with_financials"] == 1 and rep["periods"] == 2
    # consolidated headline (validated against the live API)
    krev = next(r for r in rows if r["kind"] == "reported"
                and r["concept"] == "revenue" and r["basis"] == "consolidated")
    assert krev["value"] == 150806000000 and krev["currency"] == "USD"
    assert krev["entity_id"] == "923609016" and krev["source"] == "brreg"
    # company headline
    srev = next(r for r in rows if r["kind"] == "reported"
                and r["concept"] == "revenue" and r["basis"] == "company")
    assert srev["value"] == 96784000000
    # debt_to_equity = total liabilities / equity (NGAAP gearing), both bases
    kd2e = next(r for r in rows if r["kind"] == "derived"
                and r["concept"] == "debt_to_equity" and r["basis"] == "consolidated")
    assert abs(kd2e["value"] - (104032000000 / 53989000000)) < 1e-6   # ~1.93
    sd2e = next(r for r in rows if r["kind"] == "derived"
                and r["concept"] == "debt_to_equity" and r["basis"] == "company")
    assert abs(sd2e["value"] - (108428000000 / 50914000000)) < 1e-6   # ~2.13
    # No TTM rows are ever written for register inputs (annual-only -> inert).
    assert not any(r["kind"] == "derived_ttm" for r in rows)


def test_f1_interest_coverage_only_when_gross_interest_present(tmp_path):
    _, rows = _run(tmp_path, [_KONSERN_2022, _SELSKAP_2022])
    bases = {r["basis"] for r in rows
             if r["kind"] == "derived" and r["concept"] == "interest_coverage"}
    # consolidated has annenRentekostnad -> coverage; company has only the net
    # figure (sumFinanskostnad) -> interest_coverage is not emitted.
    assert bases == {"consolidated"}


def test_f4_dedupe_keeps_highest_submission_id(tmp_path):
    # Same (period, type) twice with different submission ids: only the higher-id
    # (latest resubmission) one is emitted, no double-count. The higher-id entry is
    # fed FIRST to prove selection is id-based, not merely last-seen.
    new = copy.deepcopy(_KONSERN_2022); new["id"] = 2          # real 150_806_000_000
    old = copy.deepcopy(_KONSERN_2022); old["id"] = 1
    old["resultatregnskapResultat"]["driftsresultat"]["driftsinntekter"]["sumDriftsinntekter"] = 99000000.0
    rep, rows = _run(tmp_path, [new, old])
    assert rep["periods"] == 1
    revs = [r["value"] for r in rows if r["kind"] == "reported" and r["concept"] == "revenue"]
    assert revs == [150806000000]                              # only the higher-id survived


def test_f6_bad_date_skips_one_entry_not_batch(tmp_path):
    # A non-ISO tilDato skips THAT entry; the rest of the batch still produces rows.
    bad = {**_SELSKAP_2022, "regnskapsperiode": {"tilDato": "31.12.2024"}}
    rep, rows = _run(tmp_path, [bad, _KONSERN_2022])
    assert rep["with_financials"] == 1 and rep["periods"] == 1
    assert any(r["concept"] == "revenue" and r["value"] == 150806000000 for r in rows)


def test_dry_run_writes_nothing(tmp_path):
    rep, _ = _run(tmp_path, [_KONSERN_2022], write=False)
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
