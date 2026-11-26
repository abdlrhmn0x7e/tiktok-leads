from __future__ import annotations

import math


def robust_average_view_count(values: list[int]) -> int | None:
    """25th percentile (P25) with linear interpolation, matching the
    Viral Automations Ext (MVC) Chrome extension."""
    if not values:
        return None

    sorted_values = sorted(values)
    idx = 0.25 * (len(sorted_values) - 1)
    lower = math.floor(idx)
    upper = math.ceil(idx)
    if lower == upper:
        return sorted_values[lower]
    raw = sorted_values[lower] + (sorted_values[upper] - sorted_values[lower]) * (idx - lower)
    return int(math.floor(raw + 0.5))


def median(sorted_values: list[int]) -> int:
    count = len(sorted_values)
    midpoint = count // 2
    if count % 2:
        return sorted_values[midpoint]
    return round((sorted_values[midpoint - 1] + sorted_values[midpoint]) / 2)


def floor_view_count(value: int) -> int:
    """Round to the nearest 1,000 (half up), matching MVC's Math.round(n / 1000) * 1000."""
    return int(math.floor(value / 1000 + 0.5)) * 1000
