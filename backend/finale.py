"""Finale spotlight selection."""

from __future__ import annotations

import math


def clamp_finalist_index(index: int | float, count: int) -> int:
    safe_count = max(0, math.floor(count))
    if safe_count == 0:
        return 0
    numeric = index if isinstance(index, int) else math.trunc(index) if math.isfinite(index) else 0
    return max(0, min(safe_count - 1, numeric))
