"""Small shared helpers."""

from __future__ import annotations

import numpy as np


def decimate_indices(n: int, max_points: int) -> np.ndarray:
    """Evenly spaced indices (always including the last sample)."""
    if n <= max_points:
        return np.arange(n)
    idx = np.unique(np.linspace(0, n - 1, max_points).round().astype(int))
    return idx
