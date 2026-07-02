"""Tests for bottom_up_corpus.registers.sk_registeruz — Task 1.

Network-free: all assertions run against committed fixtures.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

FIXTURES = Path(__file__).parent / "fixtures" / "sk"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load(name: str) -> dict:
    with open(FIXTURES / name, encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# parse_vykaz — positional extractor
# ---------------------------------------------------------------------------

def test_parse_vykaz_metadata():
    """Top-level metadata fields are extracted correctly."""
    from bottom_up_corpus.registers.sk_registeruz import parse_vykaz

    v = _load("sk_36319007_POD.json")
    s = _load("sk_sablona_699.json")
    p = parse_vykaz(v, s)

    assert p["idSablony"] == 699
    assert p["ico"] == "36319007"
    assert p["pristupnostDat"] == "Verejné"


def test_parse_vykaz_assets_netto_col2():
    """Table 0, cisloRiadku=1 (SPOLU MAJETOK) — netto-current = column 2 → 1 051 307."""
    from bottom_up_corpus.registers.sk_registeruz import parse_vykaz

    v = _load("sk_36319007_POD.json")
    s = _load("sk_sablona_699.json")
    p = parse_vykaz(v, s)

    # Table 0 has ncols=4 from sablona.  cisloRiadku=1 is the first row
    # (row_arr_idx=0).  data[0*4 + 2] = "1051307" → 1 051 307.0
    assert p["cells"][(0, 1)][2] == 1051307.0


def test_parse_vykaz_equity_col0():
    """Table 1, cisloRiadku=80 (Vlastné imanie / Equity) — col 0 → 262 763."""
    from bottom_up_corpus.registers.sk_registeruz import parse_vykaz

    v = _load("sk_36319007_POD.json")
    s = _load("sk_sablona_699.json")
    p = parse_vykaz(v, s)

    # Table 1 has ncols=2; row_arr_idx of cisloRiadku=80 = 1 (after 79).
    # data[1*2 + 0] = "262763" → 262 763.0
    assert p["cells"][(1, 80)][0] == 262763.0


def test_parse_vykaz_lt_bank_loans_col0():
    """Table 1, cisloRiadku=121 (Dlhodobé bankové úvery) — col 0 → 150 722."""
    from bottom_up_corpus.registers.sk_registeruz import parse_vykaz

    v = _load("sk_36319007_POD.json")
    s = _load("sk_sablona_699.json")
    p = parse_vykaz(v, s)

    # cisloRiadku=121 is at row_arr_idx=42 (79→0, 80→1, ..., 121→42).
    # data[42*2 + 0] = "150722" → 150 722.0
    assert p["cells"][(1, 121)][0] == 150722.0


def test_parse_vykaz_empty_cells_are_none():
    """Empty strings in the data array become None, not 0."""
    from bottom_up_corpus.registers.sk_registeruz import parse_vykaz

    v = _load("sk_36319007_POD.json")
    s = _load("sk_sablona_699.json")
    p = parse_vykaz(v, s)

    # Table 0, cisloRiadku=5 (Softvér) is all-empty in this fixture.
    assert p["cells"][(0, 5)] == [None, None, None, None]


def test_parse_vykaz_cells_keyed_by_table_and_cislo():
    """Cells dict is keyed by (table_idx, cisloRiadku) tuples."""
    from bottom_up_corpus.registers.sk_registeruz import parse_vykaz

    v = _load("sk_36319007_POD.json")
    s = _load("sk_sablona_699.json")
    p = parse_vykaz(v, s)

    assert isinstance(p["cells"], dict)
    # Spot-check a key exists and is the right shape
    row = p["cells"][(0, 1)]
    assert len(row) == 4  # ncols=4 for table 0


# ---------------------------------------------------------------------------
# API client functions — network-free, using a mock fetcher
# ---------------------------------------------------------------------------

def _make_fetcher(responses: dict) -> MagicMock:
    """Build a mock fetcher whose get_json returns pre-canned payloads by URL prefix."""
    fetcher = MagicMock()
    def get_json(url, *, params=None, **_):
        for prefix, payload in responses.items():
            if url.startswith(prefix):
                return payload
        raise ValueError(f"Unexpected URL: {url}")
    fetcher.get_json.side_effect = get_json
    return fetcher


def test_fetch_vykaz():
    """fetch_vykaz returns parsed JSON from the /uctovny-vykaz endpoint."""
    from bottom_up_corpus.registers.sk_registeruz import fetch_vykaz

    payload = {"id": 9000014, "idSablony": 699}
    fetcher = _make_fetcher({"https://www.registeruz.sk/cruz-public/api/uctovny-vykaz": payload})
    result = fetch_vykaz(9000014, fetcher=fetcher)
    assert result == payload


def test_fetch_sablona():
    """fetch_sablona returns parsed JSON from the /sablona endpoint."""
    from bottom_up_corpus.registers.sk_registeruz import fetch_sablona

    payload = {"id": 699, "nazov": "Úč POD"}
    fetcher = _make_fetcher({"https://www.registeruz.sk/cruz-public/api/sablona": payload})
    result = fetch_sablona(699, fetcher=fetcher)
    assert result == payload


def test_fetch_entity():
    """fetch_entity returns parsed JSON from the /uctovna-jednotka endpoint."""
    from bottom_up_corpus.registers.sk_registeruz import fetch_entity

    payload = {"id": 123, "ico": "36319007"}
    fetcher = _make_fetcher({"https://www.registeruz.sk/cruz-public/api/uctovna-jednotka": payload})
    result = fetch_entity(123, fetcher=fetcher)
    assert result == payload


def test_fetch_zavierka():
    """fetch_zavierka returns parsed JSON from the /uctovna-zavierka endpoint."""
    from bottom_up_corpus.registers.sk_registeruz import fetch_zavierka

    payload = {"id": 456, "idUctovnejJednotky": 123}
    fetcher = _make_fetcher({"https://www.registeruz.sk/cruz-public/api/uctovna-zavierka": payload})
    result = fetch_zavierka(456, fetcher=fetcher)
    assert result == payload


def test_fetch_vykaz_returns_none_on_error():
    """fetch_vykaz is batch-safe and returns None on network error."""
    from bottom_up_corpus.registers.sk_registeruz import fetch_vykaz

    fetcher = MagicMock()
    fetcher.get_json.side_effect = OSError("network down")
    assert fetch_vykaz(9000014, fetcher=fetcher) is None


def test_iter_entity_ids_paginates():
    """iter_entity_ids paginates until the returned list is empty."""
    from bottom_up_corpus.registers.sk_registeruz import iter_entity_ids

    page1 = {"pokracovat": 1001, "id": list(range(1, 1001))}
    page2 = {"pokracovat": 2001, "id": list(range(1001, 1101))}
    page3 = {"id": []}   # signals stop

    call_count = 0
    def get_json(url, *, params=None, **_):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return page1
        elif call_count == 2:
            return page2
        else:
            return page3

    fetcher = MagicMock()
    fetcher.get_json.side_effect = get_json

    ids = list(iter_entity_ids(fetcher=fetcher))
    assert ids[:5] == [1, 2, 3, 4, 5]
    assert len(ids) == 1100  # 1000 + 100


def test_iter_entity_ids_stops_on_error():
    """iter_entity_ids stops gracefully if the API raises."""
    from bottom_up_corpus.registers.sk_registeruz import iter_entity_ids

    fetcher = MagicMock()
    fetcher.get_json.side_effect = OSError("WAF block")

    ids = list(iter_entity_ids(fetcher=fetcher))
    assert ids == []
