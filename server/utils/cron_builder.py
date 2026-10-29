"""Helpers to translate between structured UI state and 5-field cron.

The interactive monitor card renders Status / Time / Weekdays / Delivery
controls. These helpers turn `(hour, minute, days)` into a cron expression
and vice versa so the card can round-trip without the user ever typing cron.

Non-trivial expressions (e.g. every other hour, month-day lists) decode as
``None`` — the UI should then fall back to showing the raw string read-only.
"""

from __future__ import annotations

DAY_LABELS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
WEEKDAYS = {1, 2, 3, 4, 5}
WEEKEND = {0, 6}
ALL_DAYS = set(range(7))


def cron_from_time_and_days(hour: int, minute: int, days: set[int]) -> str:
    """Build a 5-field cron expression from a daily hour/minute and a set of
    weekdays (0=Sun .. 6=Sat).

    - All 7 days → ``M H * * *``.
    - Any subset → ``M H * * d1,d2,...`` with sorted ints.
    """
    if not isinstance(hour, int) or not 0 <= hour <= 23:
        raise ValueError("hour must be 0-23")
    if not isinstance(minute, int) or not 0 <= minute <= 59:
        raise ValueError("minute must be 0-59")
    normalized = sorted({d for d in days if 0 <= d <= 6})
    if not normalized:
        raise ValueError("days must include at least one weekday 0-6")
    if set(normalized) == ALL_DAYS:
        day_field = "*"
    else:
        day_field = ",".join(str(d) for d in normalized)
    return f"{minute} {hour} * * {day_field}"


def time_and_days_from_cron(expression: str) -> tuple[int, int, set[int]] | None:
    """Reverse of :func:`cron_from_time_and_days`.

    Returns ``(hour, minute, days)`` when the expression is a daily cron with
    optional weekday filtering. Returns ``None`` for anything more exotic
    (ranges, step values on hour/minute, month-day specifications) so the
    caller can fall back to a read-only text display.
    """
    if not expression:
        return None
    parts = expression.strip().split()
    if len(parts) != 5:
        return None
    minute_s, hour_s, dom, month, dow = parts
    if dom != "*" or month != "*":
        return None
    try:
        minute = int(minute_s)
        hour = int(hour_s)
    except ValueError:
        return None
    if not (0 <= minute <= 59 and 0 <= hour <= 23):
        return None

    if dow == "*":
        return hour, minute, set(ALL_DAYS)

    days: set[int] = set()
    for token in dow.split(","):
        token = token.strip()
        if not token:
            return None
        if "-" in token:
            try:
                lo_s, hi_s = token.split("-", 1)
                lo = int(lo_s)
                hi = int(hi_s)
            except ValueError:
                return None
            if not (0 <= lo <= 6 and 0 <= hi <= 6 and lo <= hi):
                return None
            days.update(range(lo, hi + 1))
        else:
            try:
                d = int(token)
            except ValueError:
                return None
            if not 0 <= d <= 6:
                return None
            days.add(d)
    if not days:
        return None
    return hour, minute, days


def humanize_days(days: set[int]) -> str:
    """Return a short label for a weekday set."""
    if not days:
        return ""
    if days == ALL_DAYS:
        return "Every day"
    if days == WEEKDAYS:
        return "Weekdays"
    if days == WEEKEND:
        return "Weekends"
    return ", ".join(DAY_LABELS[d] for d in sorted(days))
