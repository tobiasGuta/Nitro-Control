"""Bounded read-only history of *actual* hardware samples for dashboard plots.

No interpolation, placeholder measurements, fan speed limits, or I/O.
A missing sensor stays missing instead of becoming a misleading zero.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import math


@dataclass(frozen=True, slots=True)
class Sample:
    timestamp: str
    cpu_celsius: float | None
    gpu_celsius: float | None
    gpu_utilization_percent: float | None


def _finite(value: float | None) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


class MonitorHistory:
    """Retain at most `limit` real samples for the visible monitoring plots."""

    def __init__(self, limit: int = 90):
        if limit < 2:
            raise ValueError("At least two samples are required")
        self._samples: deque[Sample] = deque(maxlen=limit)

    def record(self, timestamp: str, cpu: float | None, gpu: float | None,
               gpu_utilization: float | None) -> None:
        # Some service responses may return non-finite data. Never chart that.
        self._samples.append(Sample(
            timestamp, _finite(cpu), _finite(gpu), _finite(gpu_utilization)
        ))

    @property
    def samples(self) -> tuple[Sample, ...]:
        return tuple(self._samples)

    def series(self, field: str) -> tuple[float | None, ...]:
        if field not in {"cpu_celsius", "gpu_celsius", "gpu_utilization_percent"}:
            raise ValueError("Unknown measurement field")
        return tuple(getattr(sample, field) for sample in self._samples)
