"""Bounded previews and numeric summaries.

Test result sets and measurement traces are large. Tools never dump the full
payload into model context; they return counts plus a capped preview.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import TypeVar

PREVIEW_LIMIT = 20
WAVEFORM_PREVIEW = 50

T = TypeVar("T")


def bounded_preview(items: Sequence[T], limit: int = PREVIEW_LIMIT) -> list[T]:
    if limit < 0:
        limit = 0
    return list(items[:limit])


def numeric_stats(values: Sequence[float]) -> dict[str, float | int | None]:
    if not values:
        return {
            "count": 0,
            "min": None,
            "max": None,
            "mean": None,
            "stdev": None,
        }
    n = len(values)
    mean = sum(values) / n
    variance = sum((v - mean) ** 2 for v in values) / n
    return {
        "count": n,
        "min": min(values),
        "max": max(values),
        "mean": mean,
        "stdev": math.sqrt(variance),
    }


def downsample(values: Sequence[float], max_points: int = WAVEFORM_PREVIEW) -> list[float]:
    """Evenly sample a trace so a 2000-point waveform still fits in context."""
    if max_points <= 0:
        return []
    if len(values) <= max_points:
        return list(values)
    if max_points == 1:
        return [values[0]]
    last = len(values) - 1
    return [values[round(i * last / (max_points - 1))] for i in range(max_points)]
