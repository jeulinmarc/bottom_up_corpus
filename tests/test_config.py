from __future__ import annotations

import pytest

from bottom_up_corpus.config import (
    SEC_MAX_REQUESTS_PER_SECOND,
    Config,
    normalize_cik,
)


def test_user_agent_carries_contact():
    cfg = Config(contact="someone@example.com")
    assert "someone@example.com" in cfg.user_agent
    assert cfg.user_agent.startswith("bottom_up_corpus/")


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
