# Phase 5 validation

## Repository baseline

Work began on `feat/app-icon`, tracking `origin/feat/app-icon`, at `7defa01f18136fda3baa9847246281737445e6a8`. The index had no staged changes. The working tree had 39 tracked modifications and 20 untracked files, including the accepted earlier-phase work listed below.

The local Python 3.15.2 baseline passed all 238 discovered tests. A separate mandatory isolated-root run passed all 31 module lifecycle tests. Both runs had zero failures and zero skips in this environment. The full run included the strict C11 protocol harness and the default and experimental module builds in disposable directories.

### Pre-existing tracked modifications

```text
.github/workflows/tests.yml
docs/FAN-CONTROL.md
docs/KEYBOARD-RGB.md
docs/NATIVE-RGB.md
docs/RGB-HARDWARE.md
experiments/acer-wmi-rgb-enable-poc/Makefile
experiments/acer-wmi-rgb-enable-poc/README.md
experiments/acer-wmi-rgb-enable-poc/nitro_rgb_enable_poc.c
nitro_control/demo.py
nitro_control/fan_client.py
nitro_control/fan_control.py
nitro_control/fan_presentation.py
nitro_control/hardware.py
nitro_control/models.py
nitro_control/native_rgb.py
nitro_control/rgb_client.py
nitro_control/rgb_hardware.py
nitro_control/rgb_privileged.py
nitro_control/tray_model.py
nitro_control/ui.py
scripts/install-native-rgb-fedora.sh
scripts/install-rgb-driver-fedora.sh
scripts/install-rgb-helper-fedora.sh
scripts/rgb-driver-service-fedora.sh
scripts/uninstall-native-rgb-fedora.sh
scripts/uninstall-rgb-driver-fedora.sh
scripts/uninstall-rgb-helper-fedora.sh
tests/test_app_icons.py
tests/test_dashboard_ui.py
tests/test_fan_control.py
tests/test_fan_presentation.py
tests/test_hardware.py
tests/test_native_install_contracts.py
tests/test_native_rgb.py
tests/test_release_metadata.py
tests/test_rgb_driver_integration.py
tests/test_rgb_hardware.py
tests/test_rgb_privileged.py
tests/test_tray_indicator.py
```

### Pre-existing untracked source and tests

```text
experiments/acer-wmi-rgb-enable-poc/nitro_rgb_protocol.h
nitro_control/module_staging.py
tests/c/test_nitro_rgb_protocol.c
tests/test_module_lifecycle.py
tests/test_nitro_rgb_c_protocol.py
```

### Pre-existing untracked kernel build artifacts

The SHA-256 values matched before and after Phase 5. Paths are relative to `experiments/acer-wmi-rgb-enable-poc/`.

```text
6d49c3dbd84be6283122fb6d66104d7427d7ac034917c7e1a7b2ab073eedb4d4  ..module-common.o.cmd
15627bb33004a886886b1529dbcb4fab5b54aaad4a6e1ade8072cd84be9fbe41  .Module.symvers.cmd
5ee59c8b8462d589d9701a7589a7b42403f36fc498854ff12e7562bbc6db4167  .module-common.o
0fa38096e80c15223e8cdea47682915895fe98c87a4ddc416c44d1216e400488  .modules.order.cmd
84a1d7dd212fe408624d5a91c262c275dca06daa924dafe0b3ec0fa2b52d81ad  .nitro_rgb_enable_poc.ko.cmd
1a4819528d9cfffef3989bfaa1dd7446b8d0186761592b9f4cc6aa258a5411e0  .nitro_rgb_enable_poc.mod.cmd
04134974670fc347b03951f1f518567766c5d86e74f31d019c2df72563e44d83  .nitro_rgb_enable_poc.mod.o.cmd
5dcc9c7a1c4f4a11371d037ff0a4c96972fb0683fbd49ca08051b5a6becccf91  .nitro_rgb_enable_poc.o.cmd
e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855  Module.symvers
a718387bae6a5703206ffcd1c9d97a3973daed9fc8316ca8885026db87c96a4f  modules.order
e0140f91e6c0f0070659745733f9bb4cd67c1aa1242c72d81632fe4c0485ece1  nitro_rgb_enable_poc.ko
187925611ca8a921eae72270b13f59760a959b661c47522fde7bc4b4326eec15  nitro_rgb_enable_poc.mod
f22488020008c4a05f3073e0668cc7dc308cc4c38a7f3dc92f61e0f56191176f  nitro_rgb_enable_poc.mod.c
6c3defc83ebd33b326b228a44de0a5bcf1322568058487d2e636cdb9aa6e1afb  nitro_rgb_enable_poc.mod.o
6b4ebcfed56def9f98e3fe1d3da904b3f5f903c1097d8b24d9d743c47ebd298a  nitro_rgb_enable_poc.o
```

## Independent verification

| Area | Observation on the saved baseline | Phase 5 behavior |
| --- | --- | --- |
| Mode isolation | On a private D-Bus session, Demo registered as a remote of the running Live application. | Each mode has its own application ID and single-instance activation. Demo bypasses live reads, writes, and tray startup. The per-user desktop installer supplies hidden Demo metadata with the existing icon. |
| Worker scheduling | The refresh busy flag already prevented overlap. GTK rendering synchronously called the RGB backend probe and repeatedly inspected helper capabilities during rendering and editor signals. | One refresh can be in flight. Hardware reads and capability checks run in the worker. A single dashboard write may run alongside a refresh that had already started. Animation and rendering remain on the GTK thread. |
| Fan state | Numeric ordering, channel-based placement, stock-controller precedence, and the fan revision guard already passed. Ambiguous fallback Acer devices could still supply duplicate channels. | The fan revision guard and accepted channel mapping remain in use. Discovery selects one coherent Acer source. An ambiguous fallback yields unavailable fan readings. |
| Power state | A pre-write poll could replace verified Performance with Balanced. A completion could erase a newer Balanced selection, and a failed write could leave the old active state in use. | A power revision gates the active profile and firmware metadata. The editor retains the newer choice. A failed write makes the active state unknown until a fresh read. |
| RGB state | A Breathing completion appeared under a newly selected Wave draft. A 100% Static completion appeared under a 75% draft. | Completion records the submitted plan and scopes its feedback to that request. RGB revisions reject pre-write capability results. The editor retains the current draft. |
| Close and quit | Window close was blocked for a pending fan write and allowed for pending power and RGB writes. Timeout sources were left to observe the closed flag on their next invocation. | Normal close waits for a pending fan, power, or RGB write result and explains the wait. Close during a refresh, forced destruction, and application quit detach the window and remove its sources. |
| NVIDIA | A mocked multi-row response selected the first record. A synthetic runtime-suspended GPU still triggered `nvidia-smi`. | Queries target a uniquely identified NVIDIA display adapter by PCI address and match its result by that address. An ambiguous response is unavailable. Periodic reads skip suspended and transitional runtime states. |
| Numeric parsing | Fractional RPM was truncated, and the AMD fixture picked a 67 °C Tctl reading before the available 47 °C Tdie reading. Existing parsers correctly treated missing values and non-finite values as unavailable. | Integer hwmon samples preserve zero and reject malformed data. AMD die readings take precedence, with a separately labeled Tctl fallback on systems that expose only Tctl. NVIDIA utilization outside 0–100% is unavailable. |
| History | Explicit missing values broke segments, but delayed points used even spacing and late observations were appended in arrival order. Failed refreshes left the previous graph endpoint displayed. | The graph places samples by elapsed time. Missing data, delays over seven seconds, and sensor identity changes break the line. Out-of-order observations are discarded, stale endpoints clear, and history retains at most 90 samples over six minutes. |

## Operation lifecycle

- Startup and periodic refreshes collect immutable values off the GTK thread and deliver results through tracked GLib idle sources. RGB backend and helper inspection normally runs every 30 seconds. A changed WMI interface or an RGB write prompts an earlier inspection.
- Fan, power, and RGB confirmations recheck the draft and available capability before starting a write. Fan keeps the accepted revision mechanism and Restore Auto behavior. Power separates the requested profile from the verified active profile, and RGB distinguishes the last helper response from the current editor values.
- A write invalidates any earlier read for the same domain. The next eligible read refreshes that domain. Hardware writes are submitted once per confirmed request, using the existing helper and D-Bus timeouts.
- Closing the window starts no hardware operation. The Custom fan warning and Restore Auto action remain available under the existing conditions. The separate opt-in GTK3 tray keeps its independent lifetime.
- Forced application shutdown stops widget callbacks while allowing the authorized worker to finish under the existing client timeout. A privileged operation or a D-Bus service may continue beyond the client deadline. RGB helper readback is software confirmation. Physical LED output still requires observation.

NVIDIA polling uses a four-second dashboard refresh and a 12-second tray refresh. The collector also rate-limits closely spaced dashboard requests. Runtime status is checked before a query, so the check reduces avoidable wakes but cannot eliminate a race with a suspend transition. When several NVIDIA adapters are present, the dashboard leaves the intended adapter unresolved. The one-shot CLI can accept a unique identified NVIDIA record when the PCI inventory is unavailable. [NVIDIA SMI documentation](https://docs.nvidia.com/deploy/nvidia-smi/index.html) · [Linux runtime power management](https://docs.kernel.org/power/runtime_pm.html)

The verified stock Acer controller takes precedence over fallback hwmon sensors. An unrelated vendor or ambiguous fallback provides no Acer fan telemetry. NVMe display numbering does not shift when a drive remains enumerated but its temperature becomes unreadable. CPU temperature source labels distinguish Intel package temperature, AMD Tdie, and AMD Tctl. [Linux hwmon interface](https://docs.kernel.org/hwmon/sysfs-interface.html) · [Linux k10temp documentation](https://docs.kernel.org/hwmon/k10temp.html)

## Initial Phase 5 validation

| Check | Result |
| --- | --- |
| Full unittest discovery | 249 passed, zero failures, zero skips. The GTK launcher counts as one discovered test. |
| Python 3.11 compatibility | 248 passed and one GTK wrapper skipped because PyGObject was unavailable for that interpreter. |
| Actual GTK4/Libadwaita fixtures | 13 passed on a private D-Bus session using real GTK widgets, event-controlled mock operations, and a synthetic clock. |
| Mandatory isolated module lifecycle | 31 passed with `NITRO_REQUIRE_ISOLATED_ROOT_TESTS=1`, zero skips. |
| Native RGB C regressions | Strict C11 behavioral harness, default module compilation, and experimental module compilation passed. |
| Python and CLI | `compileall` passed for the package and tests. Demo snapshot parsed through `json.tool`. |
| Shell syntax | All 16 shell scripts passed individual `bash -n` checks. |
| Lint and diff | Targeted Ruff checks and `git diff --check` passed. |
| Workspace integrity | Branch and commit matched the baseline. All 20 originally untracked files matched their baseline hashes, including all 15 kernel build artifacts. |

Accepted helper clients and protocols, native firmware code, module staging and installation, and the kernel experiment sources retained their pre-Phase-5 contents. Their regression tests ran with the dashboard changes in place. The desktop installer and uninstaller were exercised against temporary home directories. Validation used mock hardware interfaces, synthetic sysfs, isolated roots, and disposable build directories, with no privileged commands, live kernel operations, physical writes, or host configuration changes.

Live dGPU power behavior, physical fan response, and keyboard LED output remain unverified on hardware. A slow backend can delay a refresh because the dashboard allows one read at a time. The GTK fixtures require GTK4, Libadwaita, Cairo, D-Bus, and a display. This run exercised them without skips.

## Independent review corrections

The correction started from the working tree documented above on the same branch and commit. A pre-correction local run passed all 249 discovered tests. The focused reproductions exposed three cases that suite did not cover.

| Finding | Before correction | After correction |
| --- | --- | --- |
| H-P5-01 | With a clock of 100, a poll observed at 80, a history expiry marker at 95, and matching revisions, the telemetry stayed hidden. The fan and power callbacks still received Auto and Balanced, RGB became available, and the next probe moved to 130. | Identical inputs leave the fan and power callbacks untouched and RGB unavailable, with its next-probe value at zero. A gated GTK refresh also confirmed that a newer history marker leaves recent power and RGB inspections usable while older telemetry and fan observations remain expired. |
| H-P5-02 | An injected discovery `RuntimeError` escaped the worker. No completion was posted and the refresh stayed busy. | The same error posts one completion and releases the busy flag. It leaves the fan controls unavailable while keeping valid observations from other inspections. GTK tests also exercise an unexpected power error, a thread-start failure, and recovery on the next refresh. |
| H-P5-03 | The graph joined 45 °C from Intel package 0 to 75 °C from package 1 under the same display label. | The same readings form two segments. Synthetic sysfs tests also preserve continuity through renumbering of the same Intel or AMD backing device, including an Intel reading of 0 °C. |

Each inspection records its own start time. Fan and power state expire after 12 seconds, independently of the history graph. An arriving RGB inspection has the same acceptance window. An accepted RGB inspection retains the existing 30-second probe cadence and 12-second cache grace, measured from the inspection start. The GTK timer expires these states while a refresh is pending. Confirmation checks enforce the deadlines between ticks. Revision checks still reject reads that predate a write. Recovery schedules reads only.

CPU history uses a driver, backing-device path, and sensor label as its identity when sysfs exposes a backing device, while the display keeps its readable label. The kernel package number supplies a fallback for Intel. An AMD entry without a backing path uses its hwmon slot and label, so renumbering such an entry can conservatively break the line. Replacement at the same backing path and with the same sensor label is indistinguishable from continued observation. `cpu_identity` is an additive snapshot JSON field. [Linux hwmon interface](https://docs.kernel.org/hwmon/sysfs-interface.html) · [Linux coretemp documentation](https://docs.kernel.org/hwmon/coretemp.html)

### Correction validation

| Check | Result |
| --- | --- |
| Full local regression suite | 251 passed, zero failures, zero skips. The GTK launcher counts as one discovered test. |
| Real GTK4/Libadwaita fixtures | All 18 passed on a private D-Bus session. |
| Mandatory isolated module lifecycle | All 31 passed with `NITRO_REQUIRE_ISOLATED_ROOT_TESTS=1`, zero skips. |
| Native RGB | Strict C11 behavior and default and experimental module compilation passed in disposable directories. |
| Syntax and output | Python compilation covered 46 source and test files. All 16 shell scripts passed individual syntax checks. Demo JSON parsed successfully. |
| Integrity | Targeted Ruff and diff checks passed. The branch, commit, and file inventory matched the correction intake. All 15 original kernel artifact hashes matched. |

The correction leaves the accepted earlier-phase helper protocols, firmware code, and module staging intact. Demo and Live isolation, single-refresh scheduling, write serialization, selection handling, close behavior, and NVIDIA detection retain their regression coverage. The tests used mocked hardware, synthetic sysfs, private GTK and D-Bus sessions, isolated installer fixtures, and disposable kernel builds. Physical cooling, lighting, and NVIDIA runtime power behavior remain unverified on hardware.

## R1 closure correction

**H-P5-R01.** Injecting `RuntimeError("thread limit")` into `Thread.start()` with real GTK widgets reproduced the same outcome for fan, power, and RGB. The exception escaped, pending stayed true, and normal close was inhibited. The mocked hardware clients recorded zero write calls.

Repeating the injection after the correction produced `pending False` and `close inhibited False` for all three paths. Each path reports that its write could not be started, clears pending state, and restores the corresponding revision before returning to the GTK loop. A read already in flight therefore remains eligible. The fan and firmware readouts are invalidated only once their write worker starts. Fan and power use their existing status areas for the failure. RGB uses a toast so the previous software readback remains visible.

The GTK regression covers fan, power, and both Static and Dynamic RGB. It verifies the previous observations and draft, operation feedback, control availability, completion of the in-flight read, another periodic refresh, and normal close. No mock write client is called. Failures after a worker starts still use the existing operation completion paths. Actual hardware and authorization behavior remain outside this offline validation.

| Check | Closure result |
| --- | --- |
| Full local regression suite | 251 passed, zero failures and skips. Private-session GTK tests run through one discovered wrapper. |
| Real GTK4/Libadwaita fixtures | 19 passed on a private D-Bus session, including four startup-failure cases within one fixture. |
| Mandatory isolated module lifecycle | 31 passed with `NITRO_REQUIRE_ISOLATED_ROOT_TESTS=1`, zero skips. |
| Native RGB | The strict C11 behavioral harness and both disposable kernel builds passed. |
| Syntax, lint, and output | Python compilation covered 46 files. All 16 shell scripts passed syntax checks. Targeted Ruff checks, Demo JSON validation, and diff checks passed. |
| Integrity | Only the GUI, its GTK test fixture, and this report differed from the R1 intake. The branch and commit matched, and all 15 kernel build artifact hashes matched. |

The R1 intake comparison confirmed that the earlier-phase helper protocols, firmware code, and module staging matched their pre-closure contents. Validation used mocked writes, temporary homes and roots, and disposable build directories. Host configuration remained unchanged.
