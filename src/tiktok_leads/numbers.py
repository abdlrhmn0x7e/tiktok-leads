from __future__ import annotations


def robust_average_view_count(values: list[int]) -> int | None:
    if not values:
        return None

    sorted_values = sorted(values)
    count = len(sorted_values)
    if count < 5:
        return median(sorted_values)

    trim_count = max(1, round(count * 0.15))
    trimmed = sorted_values[trim_count:-trim_count]
    if not trimmed:
        trimmed = sorted_values
    return round(sum(trimmed) / len(trimmed))


def median(sorted_values: list[int]) -> int:
    count = len(sorted_values)
    midpoint = count // 2
    if count % 2:
        return sorted_values[midpoint]
    return round((sorted_values[midpoint - 1] + sorted_values[midpoint]) / 2)


def floor_view_count(value: int) -> int:
    if value < 10_000:
        step = 1_000
    elif value < 100_000:
        step = 5_000
    elif value < 1_000_000:
        step = 10_000
    else:
        step = 100_000
    return (value // step) * step
