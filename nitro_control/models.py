"""Immutable measurements. Unknown readings are None, never fabricated zeros."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json


@dataclass(frozen=True, slots=True)
class Temperature:
    name: str
    celsius: float
    source: str


@dataclass(frozen=True, slots=True)
class Fan:
    name: str
    rpm: int | None
    control_mode: str


@dataclass(frozen=True, slots=True)
class GPU:
    name: str
    celsius: float | None
    utilization_percent: float | None
    memory_used_mib: float | None
    memory_total_mib: float | None
    power_watts: float | None


@dataclass(frozen=True, slots=True)
class RGB:
    present: bool
    driver: str | None
    interface: str | None


@dataclass(frozen=True, slots=True)
class Snapshot:
    sampled_at: str
    model: str
    cpu_celsius: float | None
    fans: tuple[Fan, ...]
    acer_temperatures: tuple[Temperature, ...]
    drives: tuple[Temperature, ...]
    gpu: GPU | None
    firmware_profile: str | None
    firmware_profile_choices: tuple[str, ...]
    tuned_profile: str | None
    rgb: RGB
    demo: bool = False

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, ensure_ascii=False)
