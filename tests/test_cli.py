from __future__ import annotations

import types
from datetime import date

from bottom_up_corpus.cli import _parse_years, main


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
