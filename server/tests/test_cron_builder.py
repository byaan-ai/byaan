"""Tests for cron encode/decode helpers used by the interactive monitor card."""

from __future__ import annotations

import pytest

from server.utils.cron_builder import (
    ALL_DAYS,
    WEEKDAYS,
    cron_from_time_and_days,
    humanize_days,
    time_and_days_from_cron,
)


def test_encode_every_day():
    assert cron_from_time_and_days(9, 0, ALL_DAYS) == "0 9 * * *"


def test_encode_weekdays():
    assert cron_from_time_and_days(23, 15, WEEKDAYS) == "15 23 * * 1,2,3,4,5"


def test_encode_zero_pad_minutes_disallowed_as_strings():
    with pytest.raises(ValueError):
        cron_from_time_and_days(24, 0, ALL_DAYS)
    with pytest.raises(ValueError):
        cron_from_time_and_days(0, 60, ALL_DAYS)
    with pytest.raises(ValueError):
        cron_from_time_and_days(0, 0, set())


def test_roundtrip_every_day():
    cron = cron_from_time_and_days(9, 30, ALL_DAYS)
    parsed = time_and_days_from_cron(cron)
    assert parsed == (9, 30, set(ALL_DAYS))


def test_roundtrip_weekdays():
    cron = cron_from_time_and_days(23, 15, WEEKDAYS)
    parsed = time_and_days_from_cron(cron)
    assert parsed == (23, 15, set(WEEKDAYS))


def test_decode_range_syntax():
    parsed = time_and_days_from_cron("0 8 * * 1-5")
    assert parsed == (8, 0, {1, 2, 3, 4, 5})


def test_decode_returns_none_for_exotic():
    assert time_and_days_from_cron("*/15 * * * *") is None
    assert time_and_days_from_cron("0 9 1 * *") is None
    assert time_and_days_from_cron("0 9 * 1 *") is None
    assert time_and_days_from_cron("not a cron") is None
    assert time_and_days_from_cron("") is None


def test_humanize_days_labels():
    assert humanize_days(set(ALL_DAYS)) == "Every day"
    assert humanize_days(set(WEEKDAYS)) == "Weekdays"
    assert humanize_days({0, 6}) == "Weekends"
    assert humanize_days({1, 3}) == "Mon, Wed"
