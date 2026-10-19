"""Years-of-experience arithmetic over (verified) employment date ranges."""

from __future__ import annotations

import re
from datetime import date


def month_index(value: str | None, *, today: date | None = None, end: bool = False) -> int | None:
    """``YYYY-MM`` / ``YYYY`` / ``present`` -> months since year 0. Year-only ends count as December."""
    if not value:
        return None
    value = value.strip().lower()
    if value == "present":
        today = today or date.today()
        return today.year * 12 + today.month - 1
    if match := re.fullmatch(r"(\d{4})-(\d{1,2})", value):
        return int(match.group(1)) * 12 + int(match.group(2)) - 1
    if match := re.fullmatch(r"\d{4}", value):
        return int(value) * 12 + (11 if end else 0)
    return None


def total_years(ranges: list[tuple[str | None, str | None]], *, today: date | None = None) -> float:
    """Sum of non-overlapping employment time in years (overlapping roles count once)."""
    intervals: list[tuple[int, int]] = []
    for start, end in ranges:
        s = month_index(start, today=today)
        e = month_index(end, today=today, end=True) if end else None
        if s is None:
            continue
        if e is None:
            e = s  # unknown end: count the start month only
        if e < s:
            continue
        intervals.append((s, e + 1))
    intervals.sort()
    merged: list[list[int]] = []
    for s, e in intervals:
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    months = sum(e - s for s, e in merged)
    return round(months / 12, 1)


def sort_key(start: str | None, end: str | None) -> tuple[int, int]:
    """Most recent first when used with reverse=True."""
    return (month_index(end, end=True) or month_index(start) or 0, month_index(start) or 0)
