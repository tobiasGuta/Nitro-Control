# AN515-58 independent fan-control research — Phase 1

**Status (November 11, 2026): Source research and offline diagnostic
implementation only. No fan-control writes have been authorized or performed.**
Track decisions and hardware confirmation in [issue #12](https://github.com/tobiasGuta/Nitro-Control/issues/12).

## Key discovery: stock Linux acer-wmi already has an AN515-58 PWM path

The upstream Linux `acer-wmi.c` in **Linux v7.2** (not merely a
third-party replacement driver) has a specific Acer Nitro AN515-58 DMI
quirk with `predator_v4 = 1` and `pwm = 1`. The upstream `master`
revision uses a corresponding `hwmon = true`, `platform_profile = true`,
`pwm = true` quirk. These are **source-level** capabilities; the exact
local Fedora kernel package, BIOS and runtime firmware return values
still need checking.

Sources:
- [Linux v7.2 acer-wmi driver](https://github.com/torvalds/linux/blob/v7.2/drivers/platform/x86/acer-wmi.c) — reviewed file blob `e0eaaefb13d04c45a0b6f264d0278281a6119282`
- [Current Linux upstream acer-wmi driver](https://github.com/torvalds/linux/blob/master/drivers/platform/x86/acer-wmi.c) — reviewed blob `3a018fdf513891a8a012aeaa7185a3de521ecf47`
- [January 8, 2026 AN515-58 PWM quirk patch](https://www.spinics.net/lists/kernel/msg5993208.html), including a `Tested-by` for **another** AN515-58 owner. That is useful model evidence but not a validation on our reference BIOS.
- [Earlier AN515-58 fan RPM/thermal profile support](https://www.spinics.net/lists/stable/msg809155.html) originally only enabled read-only RPM and platform profiles; the PWM addition was separate.

### Driver interfaces, from the reviewed v7.2 source

| Acer stock hwmon attribute | Meaning in the inspected driver | Notes |
| --- | --- | --- |
| `fan1_input`, `fan2_input` | CPU and GPU fan RPM (from gaming system info) | Read-only measured values |
| `pwm1`, `pwm2` | Fan request/readback in Linux 0–255 units; firmware WMI fan speed is 0–100 | **Not RPM and not a measured duty cycle** |
| `pwm1_enable`, `pwm2_enable` | 0 = Turbo, 1 = Custom, 2 = Auto in *this specific upstream driver* | The hwmon convention for 0 may differ; source mapping controls |
| Firmware profile | Separate Acer platform-profile operation | **Not itself proof** that fan changes cannot affect firmware thermal policy |

The driver maps the PWM channels to Acer fan IDs **1 (CPU)** and **4 (GPU)**.
The driver's sysfs **read** implementation uses WMI GetGamingFanBehavior
(method **15**) and GetGamingFanSpeed (method **17**) for PWM state.
Its **write** implementation uses SetGamingFanBehavior (method **14**)
and SetGamingFanSpeed (method **16**). The AN515-58 platform-profile setter
uses a distinct **misc-setting** interface (method **22**, setting index
`0x0B`). Therefore the driver source separates the requests; it does
**not** establish complete physical independence of fan/thermal policies.

The kernel's `acer_wmi_hwmon_is_visible()` exposes PWM attributes only
when its firmware sensor-discovery bitmap reports a corresponding fan
and the DMI quirk enables PWM. Those attributes may **not exist** on
some installed kernel/BIOS combinations. Even if they exist, a Get
method may fail or report unknown status. This is why runtime probing
is necessary.

### Another community project: different model, different ownership

[Dahuum/nitro-sense-linux](https://github.com/Dahuum/nitro-sense-linux)
documents physical Auto/Max/Custom RPM observations on an **AN515-45**,
using a modified Linuwu-Sense kernel driver with its own
`nitro_sense/fan_speed` interface. Its
[backend contract](https://github.com/Dahuum/nitro-sense-linux/blob/main/docs/backend.md)
and [driver source](https://github.com/Dahuum/nitro-sense-linux/blob/main/driver/linuwu_sense.c)
are useful protocol research, **not** a tested AN515-58 solution. Their
installer can blacklist stock `acer_wmi` for boot-time operation, which
we explicitly **must not** do given our earlier [RGB incident #3](https://github.com/tobiasGuta/Nitro-Control/issues/3).
No GPL kernel-driver code was copied into Nitro-Control.

## Read-only local inventory — the next test

This research branch adds an intentionally narrow CLI probe:

```bash
# From a checkout of research/an51558-stock-fan-probe:
# No sudo, no installing drivers, no kernel module loading.
# No RGB Apply or fan-mode/speed writes.
 /usr/bin/python3 -m nitro_control --fan-probe
```

The JSON output includes only: DMI vendor/model/BIOS **version**,
kernel release, whether `acer_wmi` or `linuwu_sense` modules appear
loaded, the current firmware platform-profile string, and visible
`acer` hwmon RPM/PWM attributes and their readable values. It
**does not include DMI serial numbers, UUID, username or hostname**.
Review output before sharing publicly.

The probe calls only Python file reads and directory discovery of
`/sys` and `/proc`. Some stock-kernel hwmon reads invoke a firmware
**GET** WMI method; the probe never invokes SET, `modprobe`,
`sudo`, Polkit, a service, or a writer. It neither interprets file
presence as verified physical control nor assumes that a returned
`pwm` value is equivalent to actual RPM.

### How to interpret output

- `stock_acer_wmi_loaded=true` and `linuwu_sense_loaded=false`:
  expected ownership baseline, but still verify source attribution.
- `pwm_readback_status=not_exposed`: no current kernel PWM files
  for that channel; **do not** manually create them or load a competing driver.
- `pwm_readback_status=partial_or_unreadable`: the interface is
  incomplete, or a firmware/permission read returned invalid or no data;
  do not assume manual control.
- `pwm_readback_status=readable`: both stock PWM attributes were
  exposed and returned valid formatted data; **still not permission
  to write** or confirmation that fan speed and power profiles are
  independent.
- `upstream_acer_wmi_mode=null`: no valid mode readback, or
  Linuwu-Sense also appears loaded. Do not guess Auto/Custom.

If both the stock and Linuwu module appear loaded, **stop research
at inventory** and review driver ownership; do not unload anything
as a diagnostic shortcut.

## First actual Fedora 45 field report — read-only (November 11, 2026)

The owner ran `--fan-probe` from the separate research worktree, with no
privileged operations. The command completed and returned JSON:

| Runtime fact | Observation |
| --- | --- |
| Model/BIOS | Nitro AN515-58 / V2.21 |
| Kernel | `7.2.15-300.fc45.x86_64` |
| Driver presence | Stock `acer_wmi` loaded, Linuwu-Sense absent |
| Platform profile | `balanced` |
| hwmon device | `acer` (`hwmon6` during this snapshot) |
| Fan 1 | 2238 RPM; `pwm1` present/readback 0; `pwm1_enable` present but **unreadable/unparseable** |
| Fan 2 | 2255 RPM; `pwm2` present/readback 0; `pwm2_enable` present but **unreadable/unparseable** |

**Interpretation:** The live Fedora stock driver exposes PWM sysfs
attribute paths, with genuine RPM telemetry, on the target machine.
But the **mode GET** path has failed to provide valid numeric data on
both channels; the present probe intentionally does not record
read-error details, so `null` could mean I/O error, permission error,
empty response or invalid mode encoding. The correct next action is
**unprivileged read-only diagnostics** to capture those errors.

The raw `pwm*=0` results do **not** mean zero fan RPM and do not
verify fan-control writes. The two RPM values above confirm active
fans. Neither an exposed sysfs attribute nor an earlier AN515-58
`Tested-by` is sufficient evidence to enable writes on this BIOS.

**Go/no-go:** remain at read-only Phase 1. Do not attempt a PWM
mode/speed write until its GET failures, independent profile behavior,
reliable Auto restoration and authorization are reviewed.

## Remaining research gates

1. Capture and review the **actual Fedora AN515-58** `--fan-probe` output.
2. Match running kernel package/driver source to the reviewed
   upstream v7.2 behavior; Fedora may carry patches.
3. Determine whether both PWM channels and mode readback are
   available and stable without requiring a competing WMI driver.
4. Review failure modes: a requested fan mode may survive userspace
   exit, crash, suspend/resume, profile changes, or thermal emergencies.
   The stock hwmon setter does not itself implement an application
   transaction or verified automatic restoration.
5. Design an **explicit opt-in** and authorized rollback path, with
   a separate approval gate before *any* WMI/sysfs fan writes.
6. Investigate profile/fan independence only under a later, reviewed
   and owner-approved physical test; source-level separation is not
   a sufficient validation.

**Current decision: continue read-only investigation. Do not install
Linuwu-Sense, blacklist/unload `acer_wmi`, modify kernel drivers,
write `pwm*` or `pwm*_enable`, enable startup controllers, or
expose UI fan sliders.** All normal Nitro-Control GUI/RGB behavior
and the published `v0.4.0-preview.2` release remain unchanged.
