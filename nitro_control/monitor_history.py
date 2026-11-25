"""Bounded time series of observed readings. Missing readings break the plot."""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True, slots=True)
class Sample:
    timestamp: str
    cpu_celsius: float | None
    gpu_celsius: float | None
    gpu_utilization_percent: float | None
    observed_at: float
    gpu_identity: str | None = None
    cpu_identity: str | None = None


def _finite(value: float | None) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


class MonitorHistory:
    """Keep recent readings ordered by observation time."""

    def __init__(self, limit: int = 90, *, max_gap: float = 7, max_age: float = 360):
        if limit < 2:
            raise ValueError("At least two samples are required")
        if max_gap <= 0 or max_age <= 0:
            raise ValueError("History intervals must be positive")
        self._samples: deque[Sample] = deque(maxlen=limit)
        self._max_gap = max_gap
        self._max_age = max_age

    def record(self, timestamp: str, cpu: float | None, gpu: float | None,
               gpu_utilization: float | None, *, observed_at: float | None = None,
               gpu_identity: str | None = None, cpu_identity: str | None = None) -> None:
        if observed_at is None:
            try:
                parsed = datetime.fromisoformat(timestamp)
                observed_at = (parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)).timestamp()
            except ValueError:
                return
        if not math.isfinite(observed_at) or (self._samples and observed_at <= self._samples[-1].observed_at):
            return
        utilization = _finite(gpu_utilization)
        self._samples.append(Sample(
            timestamp, _finite(cpu), _finite(gpu),
            utilization if utilization is not None and 0 <= utilization <= 100 else None,
            observed_at, gpu_identity, cpu_identity,
        ))
        while self._samples[0].observed_at < observed_at - self._max_age:
            self._samples.popleft()

    @property
    def samples(self) -> tuple[Sample, ...]:
        return tuple(self._samples)

    def series(self, field: str) -> tuple[float | None, ...]:
        if field not in {"cpu_celsius", "gpu_celsius", "gpu_utilization_percent"}:
            raise ValueError("Unknown measurement field")
        return tuple(getattr(sample, field) for sample in self._samples)

    def segments(self, field: str) -> list[list[tuple[float, float]]]:
        """Return contiguous runs as (fraction of elapsed time, value) points."""
        values = self.series(field)
        if not values:
            return []
        first = self._samples[0].observed_at
        span = self._samples[-1].observed_at - first
        segments: list[list[tuple[float, float]]] = []
        previous = None
        previous_identity = None
        for sample, value in zip(self._samples, values, strict=True):
            if value is None:
                previous = None
                continue
            identity = sample.cpu_identity if field == "cpu_celsius" else sample.gpu_identity
            if previous is None or sample.observed_at - previous > self._max_gap or identity != previous_identity:
                segments.append([])
            position = (sample.observed_at - first) / span if span else 0
            segments[-1].append((position, value))
            previous = sample.observed_at
            previous_identity = identity
        return segments
