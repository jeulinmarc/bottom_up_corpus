"""Tests for the Belgium BNB CBSO register: the stdlib XBRL parser (no Arelle)
and the dimensional concept pack + NO-FALSE-DATA confidence gate."""
import pytest

from bottom_up_corpus.registers.bnb_xbrl import parse_bnb_data_xbrl, open_bnb_deposit
from bottom_up_corpus.registers.concepts_be import map_bnb_facts, BE_PACK

FIXTURE = "tests/fixtures/be/m02_full_0648822310.xbrl"
FIXTURE_M01 = "tests/fixtures/be/m01_abbrev_0508773215.xbrl"
FIXTURE_M07 = "tests/fixtures/be/m07_micro_0563659278.xbrl"


def _val(res, key):
    """The numeric value emitted for ``key`` (KeyError if suppressed/absent)."""
    return res["values"][key]["value"]


def test_parse_bnb_xbrl_count():
    facts = parse_bnb_data_xbrl(FIXTURE)
    assert len(facts) > 1000


def test_parse_bnb_xbrl_dims_keys():
    facts = parse_bnb_data_xbrl(FIXTURE)
    # At least one fact must have both "bas" and "part" dimension keys
    assert any("bas" in f["dims"] and "part" in f["dims"] for f in facts)


def test_parse_bnb_xbrl_total_assets_anchor():
    facts = parse_bnb_data_xbrl(FIXTURE)
    # Context c97: bas=m25, part=m1, prd=m1 → total assets 14 340 301 238.13 €
    target_dims = {"bas": "m25", "part": "m1", "prd": "m1"}
    matches = [f for f in facts if f["dims"] == target_dims]
    assert matches, f"No fact with dims == {target_dims!r}"
    values = [f["value"] for f in matches]
    assert any(abs(v - 14_340_301_238.13) < 1 for v in values), (
        f"Total-assets anchor not found; values seen: {values}"
    )


def test_open_bnb_deposit(tmp_path):
    import zipfile

    data_bytes = b"<xbrl>data</xbrl>"
    contact_bytes = b"<xbrl>contact</xbrl>"
    vendor_bytes = b"<xbrl>vendor</xbrl>"

    zip_path = tmp_path / "test_deposit.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("filing-contact.xbrl", contact_bytes)
        zf.writestr("filing-data.xbrl", data_bytes)
        zf.writestr("filing-vendor.xbrl", vendor_bytes)

    result = open_bnb_deposit(zip_path.read_bytes())
    assert result == data_bytes


# --------------------------------------------------------------------------- #
# Concept pack + confidence gate — asserted against the REAL m02 filing.       #
# --------------------------------------------------------------------------- #

def _map_fixture(path):
    return map_bnb_facts(parse_bnb_data_xbrl(path), period_end="2020-12-31")


def test_map_bnb_m02_core_values():
    """Every curated key on the real m02 filing == its validated total (±1 EUR)."""
    res = _map_fixture(FIXTURE)
    expected = {
        "equity": 6734534627,
        "revenue": 484777143,
        "assets": 14340301238,
        "liabilities": 7298623904,
        "provisions": 307142707,
        "income_tax": 36038793,
        "depreciation": 63038416,
        "inventory": 4606684,
        "receivables": 232625124,
    }
    for key, want in expected.items():
        assert round(_val(res, key)) == want, f"{key}: {_val(res, key)!r} != {want}"
        assert res["values"][key]["unit"] == "EUR"
        assert res["values"][key]["label"] == key


def test_map_bnb_m02_net_income_is_total_not_pretax_trap():
    """net_income is the breakdown-free m59/m4 total (115.9M), NOT the m59/spec=m16
    pre-tax disaggregation (54.4M) — the false-data trap."""
    res = _map_fixture(FIXTURE)
    assert round(_val(res, "net_income")) == 115942395
    assert round(_val(res, "net_income")) != 54441434


def test_map_bnb_m02_financial_debt_block_emitted():
    """The m51 borrowings block is emitted (x-check reconciles vs m50[ntr=m3])."""
    res = _map_fixture(FIXTURE)
    assert round(_val(res, "long_term_debt")) == 2300987436   # 986217794 + 1314769642
    assert round(_val(res, "short_term_debt")) == 4338796392
    # engine total_debt = LT + ST = real borrowings
    assert round(_val(res, "long_term_debt") + _val(res, "short_term_debt")) == 6639783828
    assert res["values"]["long_term_debt"]["tag"] == "m51 (derived, x-checked)"


def test_map_bnb_m02_balanced_and_operating_profit_suppressed():
    res = _map_fixture(FIXTURE)
    assert res["unbalanced"] is False
    assert res["basis"] == "company"
    assert res["currency"] == "EUR"
    assert res["period_end"] == "2020-12-31"
    # operating_profit is ALWAYS suppressed (label ambiguous, pending 2nd example)
    assert "operating_profit" not in res["values"]
    assert any(k == "operating_profit" for k, _ in res["suppressed"])


@pytest.mark.parametrize("path", [FIXTURE_M01, FIXTURE_M07])
def test_map_bnb_small_models_equity_present_revenue_absent(path):
    """Abbreviated (m01) & micro (m07) models: parse must not crash; equity is
    present (~3.616M) but turnover is omitted by small models -> revenue absent."""
    res = _map_fixture(path)
    assert res is not None
    assert 3_616_000 <= round(_val(res, "equity")) <= 3_617_000
    assert "revenue" not in res["values"]
    assert res["unbalanced"] is False


def test_map_bnb_unbalanced_gate_blanks_values():
    """Synthetic: total assets (m25/m1) != total passif (m25/m3) beyond tol ->
    the whole filing is untrustworthy: unbalanced, no values emitted."""
    flat = [
        {"dims": {"bas": "m25", "part": "m1", "prd": "m1"}, "value": 1_000_000.0, "unit": "EUR"},
        {"dims": {"bas": "m25", "part": "m3", "prd": "m1"}, "value": 2_000_000.0, "unit": "EUR"},
        {"dims": {"bas": "m37", "part": "m3", "prd": "m1", "ntr": "m4"}, "value": 500_000.0, "unit": "EUR"},
    ]
    res = map_bnb_facts(flat)
    assert res["unbalanced"] is True
    assert res["values"] == {}
    assert res["suppressed"]  # a reason was recorded


def _balanced_base():
    """A minimal balanced flat (m25/m1 == m25/m3) carrying equity."""
    return [
        {"dims": {"bas": "m25", "part": "m1", "prd": "m1"}, "value": 100_000.0, "unit": "EUR"},
        {"dims": {"bas": "m25", "part": "m3", "prd": "m1"}, "value": 100_000.0, "unit": "EUR"},
        {"dims": {"bas": "m37", "part": "m3", "prd": "m1", "ntr": "m4"}, "value": 40_000.0, "unit": "EUR"},
    ]


def test_map_bnb_debt_block_suppressed_when_xcheck_fails():
    """Synthetic: m51 tranches that do NOT reconcile with m50[ntr=m3] ->
    the debt block is suppressed (reason recorded); other values still emit."""
    flat = _balanced_base() + [
        # m51 tranches sum to 3,000 ...
        {"dims": {"bas": "m51", "part": "m3", "prd": "m1", "ntr": "m3", "rst": "m1", "typ": "m1"}, "value": 1_000.0, "unit": "EUR"},
        {"dims": {"bas": "m51", "part": "m3", "prd": "m1", "ntr": "m3", "rst": "m2", "typ": "m1"}, "value": 2_000.0, "unit": "EUR"},
        # ... but the independent m50[ntr=m3] witness says 20,000 -> mismatch.
        {"dims": {"bas": "m50", "part": "m3", "prd": "m1", "ntr": "m3", "rst": "m1"}, "value": 10_000.0, "unit": "EUR"},
        {"dims": {"bas": "m50", "part": "m3", "prd": "m1", "ntr": "m3", "rst": "m2"}, "value": 10_000.0, "unit": "EUR"},
    ]
    res = map_bnb_facts(flat)
    assert "long_term_debt" not in res["values"]
    assert "short_term_debt" not in res["values"]
    assert any(k in ("long_term_debt", "short_term_debt") for k, _ in res["suppressed"])
    assert round(_val(res, "equity")) == 40_000          # other values still emitted
    assert res["unbalanced"] is False


def test_map_bnb_debt_block_suppressed_when_tranche_lacks_typ():
    """Synthetic: an m51 balance-sheet fact missing its typ tranche -> the total
    cannot be confirmed, so the debt block is suppressed atomically."""
    flat = _balanced_base() + [
        # a breakdown-free m51 fact with rst but no typ (a deviating structure)
        {"dims": {"bas": "m51", "part": "m3", "prd": "m1", "ntr": "m3", "rst": "m1"}, "value": 1_000.0, "unit": "EUR"},
        {"dims": {"bas": "m50", "part": "m3", "prd": "m1", "ntr": "m3", "rst": "m1"}, "value": 1_000.0, "unit": "EUR"},
    ]
    res = map_bnb_facts(flat)
    assert "long_term_debt" not in res["values"]
    assert "short_term_debt" not in res["values"]
    assert any(k in ("long_term_debt", "short_term_debt") for k, _ in res["suppressed"])
    assert round(_val(res, "equity")) == 40_000


def test_be_pack_shape():
    """The pack is (bas, part, required-members) triples for the documented keys."""
    for key in ("assets", "equity", "revenue", "net_income", "income_tax",
                "depreciation", "inventory", "receivables", "liabilities",
                "liabilities_current", "provisions", "cash", "assets_fixed",
                "assets_current"):
        assert key in BE_PACK
        bas, part, required = BE_PACK[key]
        assert isinstance(bas, str) and isinstance(part, str) and isinstance(required, dict)


# ---------------------------------------------------------------------------
# BE identity: _norm_kbo + resolve_register_specs BE branch
# ---------------------------------------------------------------------------

def test_norm_kbo_strips_dots():
    from bottom_up_corpus.registers.identity import _norm_kbo
    assert _norm_kbo("0648.822.310") == "0648822310"


def test_norm_kbo_zero_pads_to_10():
    from bottom_up_corpus.registers.identity import _norm_kbo
    assert _norm_kbo("417497106") == "0417497106"   # 9 digits -> pad to 10


class _GleifFetcherBE:
    """Minimal GLEIF stub returning one BE entity record."""
    def __init__(self, country, registered_as, name="AGEAS SA/NV"):
        self._c, self._r, self._n = country, registered_as, name

    def get_json(self, url, **kw):
        return {"data": {"attributes": {"entity": {
            "legalName": {"name": self._n},
            "legalAddress": {"country": self._c},
            "registeredAs": self._r,
        }}}}


def test_be_lei_resolves_via_gleif():
    """LEI for a BE entity resolves via GLEIF entity.registeredAs -> be_number (KBO)."""
    from bottom_up_corpus.registers.identity import resolve_register_specs
    r = resolve_register_specs(
        [{"lei": "L1BE"}],
        fetcher=_GleifFetcherBE("BE", "0417.497.106"),
    )[0]
    assert r["be_number"] == "0417497106"
    assert r["country"] == "BE"
    assert r["status"] == "ok"
    assert r["lei"] == "L1BE"


def test_non_be_lei_unresolved():
    """LEI for a non-BE entity (e.g. FR) stays unresolved; no be_number returned."""
    from bottom_up_corpus.registers.identity import resolve_register_specs
    r = resolve_register_specs(
        [{"lei": "L2FR"}],
        fetcher=_GleifFetcherBE("FR", "417497106"),
    )[0]
    assert r["status"] == "unresolved"
    assert not r.get("be_number")


# ---------------------------------------------------------------------------
# CBSO Authentic Data API acquisition — fetch_bnb_deposit
# ---------------------------------------------------------------------------

_CBSO_REFS = [
    {
        "ReferenceNumber": "REF001",
        "DepositDate": "2019-04-10",
        "ExerciseDates": {"StartDate": "2018-01-01", "EndDate": "2018-12-31"},
        "ModelType": "M02",
        "AccountingDataURL": "https://ws.cbso.nbb.be/authentic/deposit/REF001/accountingData",
    },
    {
        "ReferenceNumber": "REF002",
        "DepositDate": "2021-03-15",
        "ExerciseDates": {"StartDate": "2020-01-01", "EndDate": "2020-12-31"},
        "ModelType": "M02",
        "AccountingDataURL": "https://ws.cbso.nbb.be/authentic/deposit/REF002/accountingData",
    },
]

_FIXTURE_BYTES = open("tests/fixtures/be/m02_full_0648822310.xbrl", "rb").read()


class _CbsoFetcherOK:
    """Stub: get_json returns _CBSO_REFS; get() returns fixture bytes."""

    def __init__(self, refs=_CBSO_REFS, acct_bytes=_FIXTURE_BYTES):
        self._refs = refs
        self._acct = acct_bytes
        self.last_get_json_headers = None
        self.last_get_headers = None

    def get_json(self, url, *, headers=None, **kw):
        self.last_get_json_headers = headers
        return self._refs

    def get(self, url, *, headers=None, **kw):
        self.last_get_headers = headers

        class _Resp:
            pass

        r = _Resp()
        r.content = self._acct
        return r


class _CbsoFetcherRefsError:
    """Stub: get_json raises (simulates network / auth failure)."""

    def get_json(self, url, **kw):
        raise RuntimeError("network error")


def test_fetch_bnb_deposit_latest_bytes():
    """Two deposits with different DepositDates → bytes of the LATEST returned."""
    from bottom_up_corpus.registers.bnb_cbso import fetch_bnb_deposit

    fetcher = _CbsoFetcherOK()
    result = fetch_bnb_deposit("0648822310", fetcher=fetcher, key="test-key")
    assert result == _FIXTURE_BYTES


def test_fetch_bnb_deposit_headers_sent():
    """Subscription-key and X-Request-Id headers are forwarded on both calls."""
    from bottom_up_corpus.registers.bnb_cbso import fetch_bnb_deposit

    fetcher = _CbsoFetcherOK()
    fetch_bnb_deposit("0648822310", fetcher=fetcher, key="my-api-key")

    assert fetcher.last_get_json_headers["NBB-CBSO-Subscription-Key"] == "my-api-key"
    assert "X-Request-Id" in fetcher.last_get_json_headers
    assert fetcher.last_get_headers["NBB-CBSO-Subscription-Key"] == "my-api-key"
    assert "X-Request-Id" in fetcher.last_get_headers
    assert fetcher.last_get_headers.get("Accept") == "application/x.xbrl"


def test_fetch_bnb_deposit_empty_refs_returns_none():
    """Empty references list → None (batch-safe)."""
    from bottom_up_corpus.registers.bnb_cbso import fetch_bnb_deposit

    result = fetch_bnb_deposit("0000000000", fetcher=_CbsoFetcherOK(refs=[]), key="k")
    assert result is None


def test_fetch_bnb_deposit_refs_error_returns_none():
    """get_json raising → None (batch-safe, never raises)."""
    from bottom_up_corpus.registers.bnb_cbso import fetch_bnb_deposit

    result = fetch_bnb_deposit("0648822310", fetcher=_CbsoFetcherRefsError(), key="k")
    assert result is None


def test_fetch_bnb_deposit_acct_error_returns_none():
    """get() raising on accounting-data URL → None (batch-safe)."""
    from bottom_up_corpus.registers.bnb_cbso import fetch_bnb_deposit

    class _AcErrFetcher(_CbsoFetcherOK):
        def get(self, url, **kw):
            raise RuntimeError("download failed")

    result = fetch_bnb_deposit("0648822310", fetcher=_AcErrFetcher(), key="k")
    assert result is None
