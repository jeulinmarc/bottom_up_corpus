from __future__ import annotations

from datetime import date

from bottom_up_corpus.naming import name_as_of, parse_former_names

FORMER = [{"name": "Facebook Inc",
           "from": "2005-05-06T04:00:00.000Z",
           "to": "2021-10-27T04:00:00.000Z"}]


def test_parse_former_names():
    periods = parse_former_names(FORMER)
    assert len(periods) == 1
    assert periods[0].name == "Facebook Inc"
    assert periods[0].start == date(2005, 5, 6)
    assert periods[0].end == date(2021, 10, 27)


def test_parse_empty():
    assert parse_former_names(None) == []
    assert parse_former_names([]) == []


def test_name_as_of_within_former_window():
    periods = parse_former_names(FORMER)
    assert name_as_of(date(2015, 1, 1), "Meta Platforms, Inc.", periods) == "Facebook Inc"
    # Boundary dates are inclusive.
    assert name_as_of(date(2021, 10, 27), "Meta Platforms, Inc.", periods) == "Facebook Inc"


def test_name_as_of_after_window_is_current():
    periods = parse_former_names(FORMER)
    assert name_as_of(date(2022, 1, 1), "Meta Platforms, Inc.", periods) == "Meta Platforms, Inc."


def test_name_as_of_unknown_date_is_current():
    assert name_as_of(None, "Meta Platforms, Inc.", parse_former_names(FORMER)) == "Meta Platforms, Inc."


def test_name_as_of_no_former_names():
    assert name_as_of(date(2015, 1, 1), "Apple Inc.", []) == "Apple Inc."
