from __future__ import annotations

import pytest

from bottom_up_corpus.config import (
    SEC_MAX_REQUESTS_PER_SECOND,
    Config,
    cusip6,
    normalize_cik,
    normalize_cusip,
)


def test_user_agent_carries_contact():
    cfg = Config(contact="someone@example.com")
    assert "someone@example.com" in cfg.user_agent
    assert cfg.user_agent.startswith("bottom_up_corpus/")


def test_no_contact_means_no_email_in_user_agent(monkeypatch):
    # With the env var unset there is no default contact, and the User-Agent
    # must not embed any email address.
    monkeypatch.delenv("BOTTOM_UP_CORPUS_CONTACT", raising=False)
    cfg = Config()
    assert cfg.contact == ""
    assert "@" not in cfg.user_agent
    assert cfg.user_agent == "bottom_up_corpus/0.1"


def test_rps_above_sec_limit_is_rejected():
    with pytest.raises(ValueError):
        Config(requests_per_second=SEC_MAX_REQUESTS_PER_SECOND + 1)


def test_min_delay_matches_rps():
    cfg = Config(requests_per_second=10)
    assert cfg.min_delay_seconds == pytest.approx(0.1)


def test_manifest_file_path_is_zero_padded():
    cfg = Config()
    assert cfg.manifest_file("320193").name == "0000320193.jsonl"


@pytest.mark.parametrize(
    "raw, expected",
    [
        (320193, "0000320193"),
        ("320193", "0000320193"),
        ("0000320193", "0000320193"),
        ("CIK0000320193", "0000320193"),
    ],
)
def test_normalize_cik(raw, expected):
    assert normalize_cik(raw) == expected


def test_normalize_cik_rejects_empty():
    with pytest.raises(ValueError):
        normalize_cik("abc")


@pytest.mark.parametrize("raw, expected", [
    ("00037BAC6", "00037BAC6"),
    (" 00037bac6 ", "00037BAC6"),
    ("00037BAC", "00037BAC"),
])
def test_normalize_cusip(raw, expected):
    assert normalize_cusip(raw) == expected


@pytest.mark.parametrize("bad", ["", "123", "00037BAC63", "00037B@C6"])
def test_normalize_cusip_rejects_bad_length_or_chars(bad):
    with pytest.raises(ValueError):
        normalize_cusip(bad)


@pytest.mark.parametrize("raw, expected", [
    ("00037BAC6", "00037B"),
    ("00037bac6", "00037B"),
    ("US00037BAC63", "00037B"),
])
def test_cusip6(raw, expected):
    assert cusip6(raw) == expected
