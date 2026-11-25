# Phase 7 integrated audit and release qualification

## Verdict

| Qualification | Result |
| --- | --- |
| Offline qualification | **Passed** for the reviewed working-tree candidate. The strict local suite ran with the GTK, isolated-root, and native build coverage available. |
| Hosted CI qualification | **Pending.** The workflow, pins, and gate behavior were checked locally. No GitHub-hosted run of the candidate was performed. |
| Physical hardware qualification | **Pending.** The [AN515-58 checklist](HARDWARE-QUALIFICATION.md) was prepared and deliberately left unexecuted. |
| Release readiness | **Hold publication.** The source is suitable for hosted CI and supervised candidate qualification. The final source revision, hosted gate results, and physical sign-off are still required before a release. |

## Final baseline

Phase 7 started on `feat/app-icon`, tracking `origin/feat/app-icon`, at `7defa01f18136fda3baa9847246281737445e6a8`. There were no staged changes. The working tree already contained 53 modified tracked files and 28 untracked files, with 114 source and non-ignored inventory paths. The full intake status, inventory, and hashes were captured before edits and are recorded below.

The application reports `Nitro Control 0.4.0-preview.5`. The README and [preview.5 release notes](RELEASE-v0.4.0-preview.5.md) identify that version. The historical tag resolves to `087e1b2b02fbc145f502759e20a80d568687886b`; it identifies a different snapshot from this working-tree candidate. Historical release notes and physical observations were kept as records.

The supplied independent Phase 6 R1 evidence reported 253 counted tests, 249 passes, zero failures, and five environmental skip records. The local Phase 7 intake run passed all 262 discovered tests without skips under Python 3.15.2. The closure matrix below uses the original independent deep engineering audit and its NC identifiers from the supplied audit attachment, then checks the accepted phase changes against present source and runnable tests.

## Integrated architecture assessment

- The per-user deployment publishes a complete release through an atomic activation link. A generation-pinned GUI stays with its loaded code. The independent tray's Open action targets the Live D-Bus application and falls back to the current installed launcher, so a tray from an older generation does not pull the dashboard back to that generation. A new GTK3 test combines actual staged install, upgrade, and removal with a harmless dashboard stub to verify that boundary.
- Demo has its own application ID and uses simulated readings. Live telemetry and capabilities are collected outside GTK and expire separately. Readout ordering, request revisions, and main-thread completion handling keep stale results from undoing current user selections. Fan, power, and RGB operations use bounded workers and serialized write state. Close handling and worker startup rollback preserve the accepted Phase 5 behavior.
- The unprivileged GUI and tray remain separate from privileged helper state. Fan operations depend on the exact supported stock PWM path. Power uses the desktop profile service. RGB capability discovery checks helper and backend compatibility before authorization, and current helpers verify installation and call the native module under the relevant private locks. Software response handling and verified RGB readback remain distinct from the visible lighting result.
- Private module staging, adoption and removal, exposure tracking, and shared native lifecycle locks are exercised with fake tools in disposable roots. The default module and experimental diagnostic build are separately verified. A helper timeout still leaves uncertainty about a previously authorized process or firmware request; offline evidence cannot establish cross-process firmware timing or stop a kernel call.
- Documentation now distinguishes hosted GTK fixture coverage from live GNOME and hardware testing. The current keyboard guide also distinguishes legacy Static payload compatibility from access to the migrated private native module location. Native install and recovery instructions, tray opt-in, user deployment rollback and uninstall guarantees, and hardware scope were reviewed without rewriting historical releases.

## Original audit closure matrix

| Finding and original concern | Current implementation and independently exercised evidence | Remaining limitation | Disposition |
| --- | --- | --- | --- |
| **NC-01 — Fan advisory lock on a public sysfs path.** An unrelated lock could block changes, including Restore Auto. | The privileged fan path uses a checked private lock inode. `test_sysfs_directory_flock_does_not_block_fan_control_or_auto_recovery` and the stable-inode and unsafe-metadata cases passed on fixtures. | The installed helper must be the current implementation. | **Corrected and verified offline.** |
| **NC-02 — ACPI result decoding could treat malformed output as success.** | The kernel path parses a bounded response and propagates firmware rejection and readback errors. The strict C11 behavioral harness exercised malformed response cases. Both disposable Kbuild variants compiled, and mocked helper failures remained failures. | No physical firmware response was exercised in Phase 7. | **Corrected and verified offline.** |
| **NC-03 — Persistent Custom fan mode and thermal uncertainty.** | Explicit Custom confirmation explains persistence, and Restore Auto remains available. Dashboard confirmation and cancellation, failed Custom writes, and fan readback and recovery tests passed. | Custom can persist after close. There is no thermal watchdog. EC fallback and combined two-fan response require supervised verification. | **Requires physical hardware qualification.** |
| **NC-04 — GUI/helper capability drift surfaced after authorization.** | Read-only capability discovery checks protocol, provenance, and available backend. The compatibility test kept supported Static requests available while blocking unsupported Dynamic requests before authorization. Interrupted helper staging, missing policy, and malformed helper reply cases also passed. | Payload compatibility does not give a legacy helper access to private native staging. Live PolicyKit behavior remains separate. | **Corrected and verified offline.** |
| **NC-05 — Driver/module exposure and incomplete uninstall.** | Private native staging, exact-kernel checks, positive depmod and initramfs clearance, and explicit legacy adoption replace the older ambiguous flow. All 31 mandatory isolated lifecycle tests passed, including multi-kernel migrations, interruption boundaries, and unresolved exposure retention. | Real initramfs, enrolled-key policy, and boot coexistence still depend on the target installation. | **Corrected and verified offline.** |
| **NC-06 — Timeout uncertainty, lock ordering, and overlapping WMI work.** | Integrity verification runs inside the native lifecycle lock. Shared backend lifecycle and contention fixtures, timeout/partial insert/conditional unload tests, and the GTK write scheduling tests passed. The dashboard defers a new refresh while a write is pending. The installer checks signing prerequisites and the kernel remains the final loader authority. | Client timeout does not imply privileged termination, and an already-running poll, separate tray reader, or external firmware client can still overlap the operation. Physical WMI timing, signing-policy changes after installation, and recovery need target qualification. | **Mitigated with documented limitations.** |
| **NC-07 — Native brightness guard and experimental parameters.** | GUI, helper/backend, and kernel paths enforce supported full-brightness native initialization. The strict harness, backend refusal before insertion, and both Kbuild parameter/alias checks passed. Production staging refuses experimental diagnostic parameters. | Full-brightness initialization is a guard, and the historical blackout cause remains unverified. Actual effect and brightness behavior need observation. | **Requires physical hardware qualification.** |
| **NC-08 — Demo/Live application collision and UI state races.** | Private-session activation exercised independent IDs. GTK tests passed for expired per-domain observations, malformed or failed refresh work, stale completions, draft preservation, and all write-worker startup/close paths. | A separately authorized request can outlive the window and still needs its own completion evidence. | **Corrected and verified offline.** |
| **NC-09 — Lexical sensor order, fan-slot identity, GPU polling and graph artifacts.** | Numeric-sort, identity, missing/zero, multi-record PCI targeting, suspend-state avoidance, and gap-preserving history tests passed. CPU history continuity follows the backing sensor, and a package change breaks the line. | Runtime-state observation is a snapshot and cannot eliminate a transition between the check and the NVIDIA query. Actual temperature, RPM, and power impact still need measurement. | **Mitigated with documented limitations.** |
| **NC-10 — Install/remove ownership, launcher paths, and tray process lifecycle.** | Deployer and helper tests exercised ownership, source preflight, activation, local-file preservation, autostart lock contention, and no-bytecode payload staging. Gio parsed and launched all desktop modes from a special-character home. A private GTK3 fixture exercised the runtime lock, polling recovery, subprocess reaping, and a running tray opening the current release after upgrade. | Legacy files without verified ownership can remain after removal. Indicator rendering and next-login behavior were not tested on a live GNOME desktop. | **Corrected and verified offline.** |
| **NC-11 — Mutable CI actions and non-enforced behavioral coverage.** | Actions use full verified pins, permissions are read-only, and required GTK, desktop, native, and isolated lifecycle jobs use an explicit failing-on-skips runner. Local strict and compatibility runs and workflow lint passed. | A GitHub-hosted execution of this exact candidate is pending. | **Mitigated with documented limitations.** |

## Newly reproduced gaps and corrections

| Observation | Before | After |
| --- | --- | --- |
| Shell syntax gate can mask an early failure | The old loop run as a plain Bash script returned 0 after a broken first script and a valid final script. | The workflow explicitly selects GitHub's Bash shell with error-on-failure and pipe failure. The same sequence returns 2 and stops on the broken script. |
| Optional fixture result was not a release guarantee | With X11 directed to an unavailable display, the ordinary GTK wrapper reported two skips and returned 0. | The required gate for the same test returns 1 and names the dependency failure. Compatibility testing still reports environmental skips explicitly. |
| Python 3.14 incomplete PyGObject namespace | The installed 3.14 interpreter could import an empty `gi` namespace left in its package directory. Both `--demo` and `--tray` exited 1 with an attribute traceback. | The GTK entry points import the required API directly, so the CLI reports the missing dependency and exits 2. A deterministic subprocess test models the incomplete namespace for both modes. On the unsupported local 3.14 GTK environment the fixture wrappers now report skips. |
| Installed tray across generations lacked direct integration coverage | The baseline had application activation, standalone child reaping, and deployment upgrade tests in separate fixtures. | One private-session fixture now upgrades an installed toy application while retaining the old tray source path, verifies that Open launches the new release, and checks that the retired tray reports the missing launcher after removal. |

## CI design and reproducibility

- `actions/checkout` v6.1.0 is pinned to `d23441a48e516b6c34aea4fa41551a30e30af803`. `actions/setup-python` v6.3.0 is pinned to `ece7cb06caefa5fff74198d8649806c4678c61a1`. The upstream release entries and exact tag resolutions were checked independently through GitHub and `git ls-remote`. Workflow permissions are `contents: read`, checkout credentials are not persisted, and jobs use explicit Ubuntu 24.04 labels and timeouts. [Checkout release](https://github.com/actions/checkout/releases/tag/v6.1.0) · [Setup Python release](https://github.com/actions/setup-python/releases/tag/v6.3.0)
- Python 3.11–3.15 are exercised by the compatibility matrix. Dedicated desktop and native gates install the distro introspection libraries and run GTK4/GTK3 on Xvfb in private D-Bus sessions. Desktop entry validation is a required dependency there. The native gate selects a specific installed generic header build directory and runs strict C11 and both disposable module variants. The separate isolated lifecycle gate requires bubblewrap support and fails on missing isolation or skips. [Ubuntu runner image](https://github.com/actions/runner-images/blob/main/images/ubuntu/Ubuntu2404-Readme.md) · [Ubuntu generic headers](https://packages.ubuntu.com/noble/linux-headers-generic)
- Ruff is version- and wheel-hash-pinned for the Linux x86-64 hosted lint environment. ShellCheck is scoped to the five application deployment scripts; every shell script has an individual syntax check. Python compilation includes the deployment script, and Demo JSON is parsed as a fail-fast pipeline. The smoke gate checks out history for the reviewed rollback tag. The runner image and Ubuntu packages can receive updates, and the Python matrix tracks patch releases within each named minor version. [Ruff distribution](https://pypi.org/project/ruff/0.17.4/)

## Final validation results

The local host was Fedora 45, running `7.3.4-300.fc45.x86_64`. All GTK fixtures used a private session bus. Counts below distinguish discovered wrapper tests from the cases they run.

| Gate | Actual result |
| --- | --- |
| Strict full discovery, Python 3.15.2 | 263 passed, zero failures and zero skips. This includes the two GTK wrapper tests and mandatory isolated-root coverage. |
| Private GTK4/Libadwaita and GTK3/Ayatana | 21 GTK4 cases and 7 GTK3 cases passed in separate processes. |
| Mandatory module lifecycle | All 31 passed with `NITRO_REQUIRE_ISOLATED_ROOT_TESTS=1`. |
| Native tests | The strict C11 harness and both disposable Kbuild variants passed against Fedora `7.2.15-300.fc45.x86_64` headers during full discovery, and separately passed against the running kernel's `7.3.4-300.fc45.x86_64` headers. Only compilation and read-only metadata inspection were performed. |
| Python 3.11.18, 3.12.15, 3.13.15, 3.14.10 | Each discovered 263 tests, with 261 passes and two reported GTK skips. The older interpreters lack usable local PyGObject bindings. All other tests, including deployment and native fixtures, passed. |
| Python and shell syntax | `compileall` passed for package, scripts and tests. All 16 shell scripts passed individual `bash -n` checks. |
| Lint and workflow validation | Targeted Ruff checks passed, ShellCheck 0.10.0 passed on the five deployment scripts, and actionlint 1.7.12 passed on the final workflow. A local forced display failure verified the required GTK gate's nonzero exit. |
| Demo JSON, desktop entries, and deployment | Demo JSON parsed. `desktop-file-validate` and actual Gio launches passed for Live, Demo, and Tray. Isolated preview.4 → preview.5 → preview.4 → preview.5 testing passed. The deployment tests also exercised removal, repeated uninstall, and preservation of unrelated artifacts. |
| Diff and artifact integrity | Diff checks passed. All 15 original kernel build artifact hashes matched. Branch and commit matched the intake. |

The Phase 7 product changes are limited to recognizing missing PyGObject API at the two GUI import boundaries. The other changes are qualification infrastructure, tests, and documentation. The accepted fan, power, RGB helper protocols, native firmware code, hardware telemetry, module staging logic, and deployment architecture matched the intake. The work used synthetic hardware, private session buses, temporary homes and filesystem roots, and disposable build directories. Host configuration, live login autostart, physical hardware, and kernel module state were left intact.

## Remaining limitations

Hosted CI and target-system qualification are pending. In particular, real fan thermal response, keyboard appearance and firmware cleanup, dGPU wake behavior, live GNOME display and PolicyKit, and real suspend/resume remain outside the offline result. The qualification checklist defines the required target evidence. Keep historical observations separate from candidate sign-off.

## Baseline records

<details>
<summary>Intake working-tree status</summary>

```text
 M .github/workflows/tests.yml
 M README.md
 M docs/FAN-CONTROL.md
 M docs/KEYBOARD-RGB.md
 M docs/NATIVE-RGB.md
 M docs/RGB-HARDWARE.md
 M docs/TRAY-INDICATOR.md
 M experiments/acer-wmi-rgb-enable-poc/Makefile
 M experiments/acer-wmi-rgb-enable-poc/README.md
 M experiments/acer-wmi-rgb-enable-poc/nitro_rgb_enable_poc.c
 M nitro_control/__main__.py
 M nitro_control/dashboard_visuals.py
 M nitro_control/demo.py
 M nitro_control/fan_client.py
 M nitro_control/fan_control.py
 M nitro_control/fan_presentation.py
 M nitro_control/hardware.py
 M nitro_control/models.py
 M nitro_control/monitor_history.py
 M nitro_control/native_rgb.py
 M nitro_control/rgb_client.py
 M nitro_control/rgb_hardware.py
 M nitro_control/rgb_privileged.py
 M nitro_control/tray_indicator.py
 M nitro_control/tray_model.py
 M nitro_control/tray_preferences.py
 M nitro_control/ui.py
 M scripts/disable-tray-autostart-fedora.sh
 M scripts/enable-tray-autostart-fedora.sh
 M scripts/install-fedora.sh
 M scripts/install-native-rgb-fedora.sh
 M scripts/install-rgb-driver-fedora.sh
 M scripts/install-rgb-helper-fedora.sh
 M scripts/rgb-driver-service-fedora.sh
 M scripts/smoke-test-gui-install-rollback-fedora.sh
 M scripts/uninstall-native-rgb-fedora.sh
 M scripts/uninstall-rgb-driver-fedora.sh
 M scripts/uninstall-rgb-helper-fedora.sh
 M scripts/uninstall.sh
 M tests/test_app_icons.py
 M tests/test_dashboard_ui.py
 M tests/test_fan_control.py
 M tests/test_fan_presentation.py
 M tests/test_hardware.py
 M tests/test_launcher.py
 M tests/test_native_install_contracts.py
 M tests/test_native_rgb.py
 M tests/test_release_metadata.py
 M tests/test_rgb_driver_integration.py
 M tests/test_rgb_hardware.py
 M tests/test_rgb_privileged.py
 M tests/test_tray_indicator.py
 M tests/test_tray_preferences.py
?? docs/PHASE-5.md
?? docs/PHASE-6.md
?? experiments/acer-wmi-rgb-enable-poc/..module-common.o.cmd
?? experiments/acer-wmi-rgb-enable-poc/.Module.symvers.cmd
?? experiments/acer-wmi-rgb-enable-poc/.module-common.o
?? experiments/acer-wmi-rgb-enable-poc/.modules.order.cmd
?? experiments/acer-wmi-rgb-enable-poc/.nitro_rgb_enable_poc.ko.cmd
?? experiments/acer-wmi-rgb-enable-poc/.nitro_rgb_enable_poc.mod.cmd
?? experiments/acer-wmi-rgb-enable-poc/.nitro_rgb_enable_poc.mod.o.cmd
?? experiments/acer-wmi-rgb-enable-poc/.nitro_rgb_enable_poc.o.cmd
?? experiments/acer-wmi-rgb-enable-poc/Module.symvers
?? experiments/acer-wmi-rgb-enable-poc/modules.order
?? experiments/acer-wmi-rgb-enable-poc/nitro_rgb_enable_poc.ko
?? experiments/acer-wmi-rgb-enable-poc/nitro_rgb_enable_poc.mod
?? experiments/acer-wmi-rgb-enable-poc/nitro_rgb_enable_poc.mod.c
?? experiments/acer-wmi-rgb-enable-poc/nitro_rgb_enable_poc.mod.o
?? experiments/acer-wmi-rgb-enable-poc/nitro_rgb_enable_poc.o
?? experiments/acer-wmi-rgb-enable-poc/nitro_rgb_protocol.h
?? nitro_control/desktop_session.py
?? nitro_control/module_staging.py
?? scripts/gui_deploy.py
?? tests/c/test_nitro_rgb_protocol.c
?? tests/gtk_lifecycle_cases.py
?? tests/test_gtk_lifecycle.py
?? tests/test_gui_deployment.py
?? tests/test_module_lifecycle.py
?? tests/test_nitro_rgb_c_protocol.py
?? tests/tray_session_cases.py
```

</details>

<details>
<summary>Intake source and non-ignored file inventory</summary>

```text
docs/PHASE-5.md
docs/PHASE-6.md
experiments/acer-wmi-rgb-enable-poc/..module-common.o.cmd
experiments/acer-wmi-rgb-enable-poc/.Module.symvers.cmd
experiments/acer-wmi-rgb-enable-poc/.module-common.o
experiments/acer-wmi-rgb-enable-poc/.modules.order.cmd
experiments/acer-wmi-rgb-enable-poc/.nitro_rgb_enable_poc.ko.cmd
experiments/acer-wmi-rgb-enable-poc/.nitro_rgb_enable_poc.mod.cmd
experiments/acer-wmi-rgb-enable-poc/.nitro_rgb_enable_poc.mod.o.cmd
experiments/acer-wmi-rgb-enable-poc/.nitro_rgb_enable_poc.o.cmd
experiments/acer-wmi-rgb-enable-poc/Module.symvers
experiments/acer-wmi-rgb-enable-poc/modules.order
experiments/acer-wmi-rgb-enable-poc/nitro_rgb_enable_poc.ko
experiments/acer-wmi-rgb-enable-poc/nitro_rgb_enable_poc.mod
experiments/acer-wmi-rgb-enable-poc/nitro_rgb_enable_poc.mod.c
experiments/acer-wmi-rgb-enable-poc/nitro_rgb_enable_poc.mod.o
experiments/acer-wmi-rgb-enable-poc/nitro_rgb_enable_poc.o
experiments/acer-wmi-rgb-enable-poc/nitro_rgb_protocol.h
nitro_control/desktop_session.py
nitro_control/module_staging.py
scripts/gui_deploy.py
tests/c/test_nitro_rgb_protocol.c
tests/gtk_lifecycle_cases.py
tests/test_gtk_lifecycle.py
tests/test_gui_deployment.py
tests/test_module_lifecycle.py
tests/test_nitro_rgb_c_protocol.py
tests/tray_session_cases.py
.github/workflows/tests.yml
.gitignore
LICENSE
README.md
dashboard.png
docs/FAN-CONTROL.md
docs/KEYBOARD-RGB.md
docs/NATIVE-RGB.md
docs/NITROSENSE-UI-REDESIGN.md
docs/RELEASE-v0.4-preview.1.md
docs/RELEASE-v0.4.0-preview.2.md
docs/RELEASE-v0.4.0-preview.3.md
docs/RELEASE-v0.4.0-preview.4.md
docs/RELEASE-v0.4.0-preview.5.md
docs/RGB-HARDWARE.md
docs/RGB-INCIDENT-2026-11.md
docs/TRAY-INDICATOR.md
docs/screenshots/.gitkeep
docs/screenshots/dashboard.png
docs/screenshots/hardware-details.png
docs/screenshots/keyboard-rgb.png
experiments/acer-wmi-rgb-enable-poc/Makefile
experiments/acer-wmi-rgb-enable-poc/README.md
experiments/acer-wmi-rgb-enable-poc/nitro_rgb_enable_poc.c
experiments/acer-wmi-rgb-enable-poc/restore-after-boot.sh
hardware-details.png
keyboard-rgb.png
nitro_control/__init__.py
nitro_control/__main__.py
nitro_control/dashboard_visuals.py
nitro_control/demo.py
nitro_control/fan_animation.py
nitro_control/fan_client.py
nitro_control/fan_control.py
nitro_control/fan_presentation.py
nitro_control/hardware.py
nitro_control/icons/hicolor/scalable/apps/io.github.tobiasguta.NitroControl.svg
nitro_control/icons/hicolor/symbolic/apps/io.github.tobiasguta.NitroControl-symbolic.svg
nitro_control/keyboard_preview.py
nitro_control/models.py
nitro_control/monitor_history.py
nitro_control/native_rgb.py
nitro_control/profiles.py
nitro_control/rgb.py
nitro_control/rgb_client.py
nitro_control/rgb_hardware.py
nitro_control/rgb_privileged.py
nitro_control/tray_indicator.py
nitro_control/tray_model.py
nitro_control/tray_preferences.py
nitro_control/ui.py
scripts/disable-tray-autostart-fedora.sh
scripts/enable-tray-autostart-fedora.sh
scripts/install-fan-helper-fedora.sh
scripts/install-fedora.sh
scripts/install-native-rgb-fedora.sh
scripts/install-rgb-driver-fedora.sh
scripts/install-rgb-helper-fedora.sh
scripts/nitro-control-rgb-driver.service
scripts/rgb-driver-service-fedora.sh
scripts/smoke-test-gui-install-rollback-fedora.sh
scripts/test-rgb-gui-session-fedora.sh
scripts/uninstall-fan-helper-fedora.sh
scripts/uninstall-native-rgb-fedora.sh
scripts/uninstall-rgb-driver-fedora.sh
scripts/uninstall-rgb-helper-fedora.sh
scripts/uninstall.sh
tests/test_app_icons.py
tests/test_dashboard_ui.py
tests/test_fan_animation.py
tests/test_fan_control.py
tests/test_fan_presentation.py
tests/test_hardware.py
tests/test_keyboard_preview.py
tests/test_launcher.py
tests/test_native_install_contracts.py
tests/test_native_rgb.py
tests/test_profiles.py
tests/test_release_metadata.py
tests/test_rgb.py
tests/test_rgb_driver_integration.py
tests/test_rgb_hardware.py
tests/test_rgb_polkit.py
tests/test_rgb_privileged.py
tests/test_tray_indicator.py
tests/test_tray_preferences.py
```

</details>

### Original kernel artifact hashes

Paths are relative to `experiments/acer-wmi-rgb-enable-poc/`. SHA-256 was checked at intake and after validation.

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
