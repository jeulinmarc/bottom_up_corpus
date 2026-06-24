from __future__ import annotations

import json
import types
from datetime import date

from bottom_up_corpus.cli import _parse_years, main
from bottom_up_corpus.config import Config
from bottom_up_corpus.universe import Issuer, Universe


def _stats():
    return types.SimpleNamespace(seen=0, added=0, updated=0, unchanged=0)


def _disc_report():
    return types.SimpleNamespace(issuers=1, rounds=1, stats=_stats(), errors=[])


def _dl_report():
    return types.SimpleNamespace(downloaded=0, skipped=0, errors=0, bytes=0,
                                 empty=0, error_items=[])


def test_list_forms_default_scope(capsys):
    rc = main(["list-forms"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "A1" in out and "10-K" in out
    assert "E1" in out  # --forms all shows opt-in families too


def test_list_forms_family_filter(capsys):
    rc = main(["list-forms", "--forms", "A"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "A1" in out
    assert "B1" not in out


def test_config_command(capsys):
    rc = main(["config"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "user_agent" in out
    assert "SEC max 10" in out


def test_parse_years_is_an_inclusive_range():
    assert _parse_years("2015-2018") == [2015, 2016, 2017, 2018]
    assert _parse_years("2024") == [2024]


def test_data_dir_flag_overrides_config(capsys, tmp_path):
    rc = main(["--data-dir", str(tmp_path), "config"])
    out = capsys.readouterr().out
    assert rc == 0
    assert str(tmp_path) in out  # the override is reflected, not ./data


def test_insecure_flag_disables_tls_verification(capsys):
    rc = main(["--insecure", "config"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "verify_tls        : False" in out


def test_tls_verification_on_by_default_in_config(capsys):
    rc = main(["config"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "verify_tls        : True" in out


def test_data_dir_threaded_into_pipeline(monkeypatch, tmp_path):
    captured: dict = {}
    monkeypatch.setattr("bottom_up_corpus.cli.discover_universe",
                        lambda ciks, **kw: _disc_report())

    def fake_download(ciks, **kw):
        captured.update(kw)
        return _dl_report()

    monkeypatch.setattr("bottom_up_corpus.cli.download_universe", fake_download)
    main(["--data-dir", str(tmp_path), "discover", "--ciks", "320193", "--download"])
    assert captured["config"].data_dir == tmp_path


def test_discover_download_without_years_passes_no_window(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr("bottom_up_corpus.cli.discover_universe",
                        lambda ciks, **kw: _disc_report())

    def fake_download(ciks, **kw):
        captured.update(kw)
        return _dl_report()

    monkeypatch.setattr("bottom_up_corpus.cli.download_universe", fake_download)
    main(["discover", "--ciks", "320193", "--download"])
    # No period flags -> no implicit 20-year cap on the download step.
    assert captured["year_min"] is None and captured["year_max"] is None
    assert captured["since"] is None and captured["until"] is None


def test_discover_download_threads_period_flags(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr("bottom_up_corpus.cli.discover_universe",
                        lambda ciks, **kw: _disc_report())

    def fake_download(ciks, **kw):
        captured.update(kw)
        return _dl_report()

    monkeypatch.setattr("bottom_up_corpus.cli.download_universe", fake_download)
    main(["discover", "--ciks", "320193", "--download",
          "--years", "2015-2018", "--since", "2016-06-01"])
    assert captured["year_min"] == 2015 and captured["year_max"] == 2018
    assert captured["since"] == date(2016, 6, 1)


def test_xbrl_years_passes_both_bounds(monkeypatch):
    captured: dict = {}

    def fake_fetch(ciks, **kw):
        captured.update(kw)
        return types.SimpleNamespace(issuers=1, periods=0, stats=_stats(), errors=[])

    monkeypatch.setattr("bottom_up_corpus.cli.fetch_financials", fake_fetch)
    main(["xbrl", "--ciks", "320193", "--years", "2015-2018"])
    # --years is a real range now, not a lower bound only.
    assert captured["since_year"] == 2015 and captured["until_year"] == 2018


def _patch_ticker_table(monkeypatch):
    table = {
        "AAPL": Issuer(cik="320193", ticker="AAPL", company="Apple Inc."),
        "DT": Issuer(cik="1773383", ticker="DT", company="Dynatrace, Inc."),
    }
    monkeypatch.setattr("bottom_up_corpus.cli.load_company_tickers", lambda fetcher: table)


def _write_bonds_and_crosswalk(tmp_path):
    bonds = tmp_path / "u.csv"
    bonds.write_text(
        "Ticker,CUSIP,Issuer\n"
        "AAPL,037833AA0,Apple Inc\n"
        "DT,25156PAA0,Deutsche Telekom Intl\n",
        encoding="utf-8",
    )
    xw = tmp_path / "xw.csv"
    xw.write_text("cik,cusip6,cusip8\n320193.0,037833,03783310\n999999.0,25156P,25156P10\n",
                  encoding="utf-8")
    return bonds, xw


def test_build_universe_from_file_keeps_collision_preferring_cusip(monkeypatch, tmp_path):
    _patch_ticker_table(monkeypatch)
    bonds, xw = _write_bonds_and_crosswalk(tmp_path)
    rc = main(["--data-dir", str(tmp_path / "data"), "build-universe",
               "--from-file", str(bonds), "--crosswalk", str(xw), "--name", "u", "--write"])
    assert rc == 0
    cfg = Config(data_dir=tmp_path / "data")
    by_ticker = {i.ticker: i for i in Universe(cfg).load("u")}
    assert by_ticker["AAPL"].cik == "0000320193"
    assert by_ticker["DT"].cik == "0000999999"
    assert by_ticker["DT"].resolution.startswith("collision")
    coll = Universe(cfg).path("u").with_name("u_collisions.jsonl")
    rows = [json.loads(l) for l in coll.read_text().splitlines() if l.strip()]
    assert rows[0]["ticker"] == "DT" and rows[0]["kind"] == "name_mismatch"


def test_build_universe_from_file_drop_collisions(monkeypatch, tmp_path):
    _patch_ticker_table(monkeypatch)
    bonds, xw = _write_bonds_and_crosswalk(tmp_path)
    rc = main(["--data-dir", str(tmp_path / "data"), "build-universe", "--from-file",
               str(bonds), "--crosswalk", str(xw), "--name", "u", "--drop-collisions", "--write"])
    assert rc == 0
    cfg = Config(data_dir=tmp_path / "data")
    tickers = {i.ticker for i in Universe(cfg).load("u")}
    assert "AAPL" in tickers and "DT" not in tickers


def test_build_universe_from_file_warns_without_crosswalk(monkeypatch, tmp_path, capsys):
    _patch_ticker_table(monkeypatch)
    bonds = tmp_path / "u.csv"
    bonds.write_text("Ticker,CUSIP\nAAPL,037833AA0\n", encoding="utf-8")
    rc = main(["--data-dir", str(tmp_path / "data"), "build-universe",
               "--from-file", str(bonds), "--name", "u", "--write"])
    assert rc == 0
    err = capsys.readouterr().err
    assert "no --crosswalk" in err.lower() or "without a crosswalk" in err.lower()
    cfg = Config(data_dir=tmp_path / "data")
    assert {i.ticker for i in Universe(cfg).load("u")} == {"AAPL"}
