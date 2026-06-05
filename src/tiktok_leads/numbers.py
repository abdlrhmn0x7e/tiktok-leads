from __future__ import annotations


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
