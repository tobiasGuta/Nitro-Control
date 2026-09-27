"""Clearly labeled static demo snapshot, not claimed as live hardware data."""

from datetime import datetime

from . import RGB_WMI_GUID
from .models import Fan, GPU, RGB, Snapshot, Temperature


def demo_snapshot() -> Snapshot:
    return Snapshot(
        sampled_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        model="Acer Nitro AN515-58 (demo)",
        cpu_celsius=45,
        fans=(Fan("Fan 1", 1986, "Automatic"), Fan("Fan 2", 2112, "Automatic")),
        acer_temperatures=(
            Temperature("Acer sensor 1 (unidentified)", 48, "acer"),
            Temperature("Acer sensor 2 (unidentified)", 40, "acer"),
            Temperature("Acer sensor 3 (unidentified)", 44, "acer"),
        ),
        drives=(Temperature("NVMe drive 1", 35, "nvme"), Temperature("NVMe drive 2", 36, "nvme")),
        gpu=GPU("NVIDIA GeForce RTX 3050 Ti", 40, 0, 12, 4096, 6),
        firmware_profile="balanced",
        firmware_profile_choices=("low-power", "quiet", "balanced", "balanced-performance", "performance"),
        tuned_profile="balanced",
        rgb=RGB(True, None, f"{RGB_WMI_GUID}-8"),
        demo=True,
    )
