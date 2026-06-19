from __future__ import annotations

from bottom_up_corpus.cli import main


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
