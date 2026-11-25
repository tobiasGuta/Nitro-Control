"""Immutable measurements. Unknown readings are None, never fabricated zeros."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass


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
    channel: int | None = None


@dataclass(frozen=True, slots=True)
class GPU:
    name: str
    celsius: float | None
    utilization_percent: float | None
    memory_used_mib: float | None
    memory_total_mib: float | None
    power_watts: float | None
    pci_bus_id: str | None = None


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
    gpu_status: str | None = None
    cpu_source: str | None = None
    cpu_identity: str | None = None

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, ensure_ascii=False)
