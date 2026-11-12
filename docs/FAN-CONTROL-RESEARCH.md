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
  do not assume manual control. `pwm_enable_read_errno` preserves a
  numeric read error when available (e.g. **6** for the observed ENXIO).
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
The owner subsequently read both mode files directly **without sudo**:
`pwm1_enable` and `pwm2_enable` each returned **OSError errno 6
(`ENXIO`, No such device or address)**. This is **not a permissions
error**. In the inspected upstream Linux v7.2 `acer-wmi.c`,
`WMID_gaming_get_fan_behavior()` specifically returns `-ENXIO`
when WMI method **15** succeeds but the decoded firmware mode is
outside **1=Auto, 2=Turbo, 3=Custom**. The caller's later
`switch (mode)` also has an `-ENXIO` default that should be
unreachable if the getter's range check held.

**Working hypothesis:** the firmware's GET method returns an
out-of-range fan mode (possibly `0`) on both channels. The raw WMI
response **has not** been collected, and the exact Fedora
`7.2.15-300.fc45.x86_64` downstream driver may differ from upstream
v7.2; do not state the numeric mode or cause as confirmed. The probe
now records the per-channel `pwm_enable_read_errno` without exposing
error paths or personally identifying data.

The raw `pwm*=0` results do **not** mean zero fan RPM and do not
verify fan-control writes. The two RPM values above confirm active
fans. Neither an exposed sysfs attribute nor an earlier AN515-58
`Tested-by` is sufficient evidence to enable writes on this BIOS.

**Go/no-go:** remain at read-only Phase 1. Do not attempt a PWM
mode/speed write until its GET failures, independent profile behavior,
reliable Auto restoration and authorization are reviewed.

## Passive kernel-log review — no fan-specific diagnostic (November 11, 2026)

The owner's unprivileged `journalctl -k -b` output confirms stock
`acer_wmi` initialized at 09:25 local time, then logged
`Unknown function number - 9 - 0` at 10:15, 10:57, 20:41 and 21:22.
The supplied filtered log excerpt contains no explicit fan-mode failure
message.

The exact `pr_warn("Unknown function number - %d - %d")` is the **default
branch of `acer_wmi_notify()`**, the Acer **event-notification handler**,
in the upstream Linux v7.2 source. It prints the event's `function`
and `key_num`; function **9** is absent from the handled event IDs
(1 hotkey, 4 backlight, 5 accelerometer/dock, 7 gaming turbo key,
8 AC). This is **not** an error from fan-control method 9 or
`WMID_gaming_get_fan_behavior()` (method **15**). The trigger for
event 9 is unknown from these records and cannot be ascribed to
the keyboard, fans or thermal profile.

In the reviewed `acer-wmi.c`, a decoded fan-mode value outside 1–3
can return `-ENXIO` **without emitting a log line**. Therefore the
absence of a fan-specific kernel log does not resolve the two observed
`pwm*_enable` ENXIO errors. Further work should focus on the runtime
firmware GET response and Fedora source equivalence, not the event
warning. No sysfs writes, module replacement, dynamic debug setting,
driver unloading or reboot is warranted by the warning alone.

## Version-specific source comparison — Fedora's 7.2.15 base (November 11, 2026)

[Fedora's published 7.2.15-300.fc45 update announcement](https://lwn.net/Articles/1105020/)
identifies **Linux v7.2.15** as the kernel base. The owner has independently
confirmed that the local `acer-wmi.ko.xz` belongs to the installed,
matching `kernel-modules-7.2.15-300.fc45.x86_64` RPM; its
`vermagic` equals the running `7.2.15-300.fc45.x86_64` kernel.

The research compared the **actual upstream Linux stable tag v7.2.15**
([stable `acer-wmi.c`](https://github.com/gregkh/linux/blob/v7.2.15/drivers/platform/x86/acer-wmi.c),
reviewed Git blob `61ae622c93d949c48ee1fe0155be8c06a5d3a3d3`)
against the upstream mainline **v7.2**
([`acer-wmi.c`](https://github.com/torvalds/linux/blob/v7.2/drivers/platform/x86/acer-wmi.c),
Git blob `e0eaaefb13d04c45a0b6f264d0278281a6119282`).

**Identical source blocks in the two upstream revisions:**

- `WMID_gaming_get_fan_behavior()` — WMI GET 15, mode decoding and
  `-ENXIO` when the decoded mode is outside the accepted 1–3 range.
- `WMID_gaming_get_gaming_fan_speed()` — WMI GET 17 and status handling.
- `WMID_gaming_set_fan_behavior()` — WMI SET 14; reviewed only, **not executed**.
- `acer_wmi_hwmon_read()` and `acer_wmi_hwmon_write()` — kernel
  hwmon mappings for fan RPM/PWM and mode; inspected only.
- `quirk_acer_nitro_an515_58` — upstream v7.2 and v7.2.15 both enable
  `predator_v4` and `pwm` for the DMI model.

Thus the relevant `ENXIO` logic **was not changed between upstream v7.2
and v7.2.15**; an upstream stable fix in this window does not explain
the owner-observed regression or readback behavior.

**Verification gap:** The research has **not extracted the exact Fedora
`kernel-7.2.15-300.fc45.src.rpm`** nor compared its downstream
patches or the actual loaded `acer_wmi` machine code. An installed,
matching Fedora RPM identifies provenance and kernel ABI, **not
byte-for-byte equivalence with upstream**. Until the Fedora-specific
source is inspected, the upstream invalid-mode branch is a **strong
hypothesis**, not absolute proof of the failure site. Raw firmware WMI
GET 15 data remains unobserved, so mode `0` is **possible but not
confirmed**, and `pwm{1,2}=0` is not itself evidence of the mode.

**Safe decision remains unchanged:** no writes to `pwm*` or
`pwm*_enable`, no kernel/firmware instrumentation or replacement,
no UI fan-speed controls. Further work can statically inspect
Fedora's exact source package, then only separately reviewed
hardware diagnostics if genuinely necessary.

## Fedora source provenance and independent ENXIO precedent (November 11, 2026)

### Fedora package and downstream source status

Fedora published `kernel-7.2.15-300.fc45.src.rpm`, identified by
the matching [Fedora 45 x86_64 package metadata](https://www.rpmfind.net/linux/RPM/fedora/updates/45/x86_64/Packages/k/kernel-debuginfo-common-x86_64-7.2.15-300.fc45.x86_64.html).
The source RPM is also listed in the
[Fedora 45 source-package mirror](https://ftp.luis.uni-hannover.de/fedora/updates/testing/45/Everything/source/tree/Packages/k/),
with its November 2, 2026 timestamp.

The official [Fedora Kernel ARK GitLab project](https://gitlab.com/cki-project/kernel-ark/)
has a corresponding **`kernel-7.2.15-0`** source tag dated November 1,
2026, at short commit `0ccf256a` on its
[tag listing](https://gitlab.com/cki-project/kernel-ark/-/tags).
This tag identifies Fedora's published kernel source lineage, but
the Fedora `300.fc45` build may include packaging configuration,
patches or downstream revisions not proven equivalent to that tag.

**Important limitation:** The exact Fedora source RPM contents and
GitLab-tagged `acer-wmi.c` have **not** been directly inspected here.
Upstream stable v7.2.15 was inspected previously; Fedora's exact
compiled implementation remains unverified. Do not promote the
upstream `-ENXIO` branch from plausible diagnosis to proven runtime
failure site without that comparison.

### A second Linux Acer application documents mode-read ENXIO

[ASense](https://github.com/fladirm/asense) independently documents
that its reference **Predator PHN16-72** may expose functional PWM/RPM
interfaces while `pwm*_enable` initially returns **`ENXIO`**.
Its [fan observation ADR](https://github.com/fladirm/asense/blob/main/docs/adr/0001-fan-observation-availability.md)
states that an unreadable mode is represented as **unknown**, not
automatically as 0/Auto. The Rust hardware implementation
([source](https://github.com/fladirm/asense/blob/main/src/hardware.rs))
treats specifically `ENXIO` from the mode getter as an unavailable
observation, while requiring explicit mode readback verification
after any mode write. Its README separately notes actual hardware
validation is on PHN16-72 and lists AN515-58 capability based
primarily on upstream Linux.

**Relevance to our AN515-58:** Other Acer firmware/driver
combinations have a documented initial mode-read limitation. That
makes a firmware-reported *unknown/uninitialized* mode plausible
here and cautions against treating `ENXIO` as proof the fan-control
transport can never work. It does **not** prove BIOS V2.21 has the same
behavior, that the raw response is 0, or that writes would be safe.
Do not copy ASense's policy of permitting some mode writes before
readback verification without a separately reviewed and explicitly
approved hardware safety experiment.

### Current technical disposition

- Both mode readbacks are **`ENXIO`** on the reference AN515-58.
- Both RPM readings remain genuine and nonzero, and stock `acer_wmi`
  continues to provide hardware telemetry.
- The Fedora source lineage has been located, but exact Fedora
  `acer-wmi` build-source equivalence is still **not demonstrated**.
- Other Acer models document this mode-read failure with otherwise
  useful interfaces; model-specific validation remains essential.
- **No fan-speed writes, no fan-mode writes, no driver replacement,
  no startup daemon, and no changes to the RGB helper.**

## Remaining research gates

1. **Completed:** initial Fedora AN515-58 `--fan-probe` output and
   independent `pwm*_enable` errno capture. Both fan modes return ENXIO.
   Read-only diagnostics do **not** establish valid control.
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
