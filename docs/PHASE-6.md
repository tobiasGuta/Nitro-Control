# Phase 6 deployment and tray lifecycle

## Repository baseline

Phase 6 began on `feat/app-icon`, tracking `origin/feat/app-icon`, at `7defa01f18136fda3baa9847246281737445e6a8`. There were no staged changes. The working tree contained 46 modified tracked files and 23 untracked files. The file inventory contained 109 tracked or non-ignored untracked paths. The baseline patch, status, inventory, and SHA-256 checks were captured before editing.

With Python 3.15.2, the accepted Phase 5 R2 tree passed all 251 local tests and all 31 tests in a separate mandatory isolated lifecycle run, with zero failures or skips. The full run included the strict C11 protocol harness and both disposable kernel builds. The independent review supplied with the brief reported 242 counted tests, 240 passes, and three environmental skip records.

## Findings and evidence

| Area | Baseline reproduction | Phase 6 result |
| --- | --- | --- |
| Argument validation | `install-fedora.sh --help` created the launcher. `uninstall.sh --bogus` removed an unrelated file under the application directory. `disable-tray-autostart-fedora.sh --help` deleted a locally edited file that retained the marker. | The five public scripts validate arguments before touching settings or installed files. Help and version leave a private home unchanged. Unsupported arguments return exit status 2. |
| Upgrade and removal | Injecting a copy failure with exit status 17 after the old package was removed left the previous launcher without its package. The original uninstaller removed the whole application directory. | A complete release is staged before activation. An interruption at the upgrade switch leaves the original launcher working. A first-install interruption before launcher publication, retry, reinstall, source rollback, and repeated removal are covered. Content checks preserve edited and unrelated files. |
| Desktop launch | An original unquoted `Exec` using a path with spaces, quotes, Unicode and a field-code-looking substring passed `desktop-file-validate`. Gio rejected it. | The entries use a fixed ASCII command that resolves the home-relative launcher at runtime and calls it with an argument vector. Real Gio launches delivered exactly the expected Live, Demo, and Tray arguments from a home containing spaces, quotes, non-ASCII characters, percent, equals, backslash, colon, and shell metacharacters. |
| Tray autostart | A copied marker was enough to authorize removal, and the old helper rejected some valid home paths outright. | Enable and disable recognize whole entry contents. Uninstallation removes recognized login entries, including those from the default configuration directory when XDG configuration is overridden. A locally edited entry is preserved. |
| Tray process lifecycle | Two baseline Open actions spawned two children. In a tray polling worker startup injection, `Thread.start()` raised and left the busy flag set. | Open first asks the Live application to activate over D-Bus. While a fallback child starts, more clicks cannot spawn another. Gio reaps both launch children. A poll startup failure releases the busy flag and can recover on the next poll. |
| Behavior already in place | The original GTK3 tray already used an exclusive runtime-file lock. The Phase 5 dashboard already had separate Live and Demo IDs, read-only monitoring, and opt-in tray preferences. | Those contracts still pass. A second process sees the existing lock and a later process can reacquire it. A tray without a session watcher exits before starting hardware polling. |

## Supported commands

| Command | Behavior |
| --- | --- |
| `./scripts/install-fedora.sh` | Stage and select the application from this checkout in the current user's home. |
| `./scripts/install-fedora.sh --source DIRECTORY` | Stage and select a reviewed source tree, including an earlier release for rollback. |
| `./scripts/uninstall.sh` | Remove recognized application links, content-matching release files, and recognized tray autostart entries. |
| `./scripts/enable-tray-autostart-fedora.sh` | Create the managed opt-in login entry in the user's XDG configuration directory. |
| `./scripts/disable-tray-autostart-fedora.sh` | Remove matching tray login entries. |
| `./scripts/smoke-test-gui-install-rollback-fedora.sh` | Exercise installation, upgrade, rollback, and reinstall in a private home using the archived preview.4 source and this checkout. |

Each supports `-h`, `--help`, and `--version` with no mutation. GUI installation still checks Fedora GTK4, Libadwaita, PyGObject, and PyCairo dependencies. Cache refresh utilities are optional. Privileged helper and module commands remain separate from these application commands.

## Deployment and removal guarantees

- The per-user deployment lock serializes installation and removal. A release contains a clean application package, a generation-pinned launcher, both desktop entries, and both icons. The release identity comes from its manifest of modes and content hashes. Reinstalling identical content reuses the release.
- Public links share a single `current` pointer. An upgrade switches that pointer after the complete release exists. First install and legacy migration create a valid target before publishing links and publish the launcher last. Applications and trays already running can still use the generation that launched them. Use the Phase 6 installer with `--source` for rollback.
- The installer checks public destinations before replacing them. Legacy ownership requires an exact match to a recognized launcher, desktop entry, or bundled icon. The legacy package tree lacks a file manifest and is preserved. A locally modified active managed release blocks activation of a replacement rather than discarding its changes.
- Normal removal unlinks matching public entries and removes manifest-listed files whose contents and modes still match. Modified files, unrelated files, unknown desktop entries, preferences, and the source checkout are preserved. It does not terminate a running tray or remove helpers, drivers, or extension settings.
- Desktop entries keep the existing Live and Demo identities and application icon. Fixed desktop arguments go to the launcher without a shell. [Desktop Entry specification](https://specifications.freedesktop.org/desktop-entry/1.4/exec-variables.html)

## Tray lifecycle

The GTK4 window launches the GTK3 tray only when its live opt-in preference is enabled. It tracks a single child and receives asynchronous exit feedback. Closing the window cancels that feedback, allowing the independent tray to continue. The tray acquires its existing user-runtime lock, then waits up to three seconds for a session StatusNotifierWatcher. If the watcher disappears without returning, the tray exits and cleans up the poll timer and queued UI work. A tray may still fail to display if the running shell does not render indicators.

The tray sends an asynchronous Live activation without D-Bus autostart. When the Live name is absent, it can spawn one dashboard while waiting for registration. An installed tray uses the stable launcher even when it still runs from an older release. Gio reaps completed children, and detached children retain their independent lifecycle. [Gio subprocess lifecycle](https://docs.gtk.org/gio/class.Subprocess.html)

## Final validation

| Check | Result |
| --- | --- |
| Complete regression suite | 259 passed, zero failures or skips. The two private-session GUI launchers appear as wrapper tests in this count. |
| Private D-Bus GUI fixtures | 21 GTK4/Libadwaita and 6 GTK3/Ayatana cases passed. |
| Mandatory isolated module lifecycle | 31 passed with `NITRO_REQUIRE_ISOLATED_ROOT_TESTS=1`, zero skips. |
| Native RGB | The strict C11 harness and default and experimental module compilation passed. |
| Python, shell, and output | All 50 Python files compiled, all 16 shell scripts passed individual syntax checks, and Demo JSON parsed. |
| Desktop and rollback | All generated desktop entries passed `desktop-file-validate`. Gio launched all three modes with exact arguments. The archived preview.4 → preview.5 → preview.4 → preview.5 smoke sequence passed in a temporary home. |
| Lint and integrity | Targeted Ruff and diff integrity checks passed. The branch, commit, and all 15 original kernel artifact hashes matched the baseline. |

The accepted privileged helper protocols, native firmware handling, and module staging files matched intake. Phase 5's dashboard state machine, Demo isolation, and hardware telemetry retain their existing regression coverage. Validation used synthetic packages and hardware, temporary homes and roots, private D-Bus sessions, and disposable builds. Host configuration, active hardware settings, and actual GNOME login autostart were left unchanged.

## Remaining limitations

Activation uses an atomic link switch on the local filesystem. The installer does not qualify durability across power loss, network-filesystem behavior, or concurrent manual edits by the same account outside the installer lock. Abrupt termination can leave unpublished staging files. Verified inactive releases remain available until uninstall so earlier processes can keep using them. Modified or ambiguous artifacts can require manual cleanup, and directory symlinks in managed paths are refused. Live GNOME rendering and physical hardware behavior remain outside this offline validation.

## Baseline records

<details>
<summary>Working-tree modifications and untracked entries</summary>

```text
 M .github/workflows/tests.yml
 M docs/FAN-CONTROL.md
 M docs/KEYBOARD-RGB.md
 M docs/NATIVE-RGB.md
 M docs/RGB-HARDWARE.md
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
 M nitro_control/ui.py
 M scripts/install-fedora.sh
 M scripts/install-native-rgb-fedora.sh
 M scripts/install-rgb-driver-fedora.sh
 M scripts/install-rgb-helper-fedora.sh
 M scripts/rgb-driver-service-fedora.sh
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
?? docs/PHASE-5.md
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
?? nitro_control/module_staging.py
?? tests/c/test_nitro_rgb_protocol.c
?? tests/gtk_lifecycle_cases.py
?? tests/test_gtk_lifecycle.py
?? tests/test_module_lifecycle.py
?? tests/test_nitro_rgb_c_protocol.py
```

</details>

<details>
<summary>Complete baseline file inventory</summary>

```text
docs/PHASE-5.md
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
nitro_control/module_staging.py
tests/c/test_nitro_rgb_protocol.c
tests/gtk_lifecycle_cases.py
tests/test_gtk_lifecycle.py
tests/test_module_lifecycle.py
tests/test_nitro_rgb_c_protocol.py
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

### Existing kernel artifacts by SHA-256

Paths are relative to `experiments/acer-wmi-rgb-enable-poc/`.

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

## Independent review corrections

| Finding | Reproduced on the initial Phase 6 tree | Corrected behavior |
| --- | --- | --- |
| H-P6-01 | Enabling autostart accepted an unrelated executable at `.local/bin/nitro-control`, with no deployment store. The executable was never run. | A regular launcher must match the reviewed legacy wrapper and have a structurally valid legacy package. A managed launcher must have the expected public link, active release, manifest, and launcher contents. Tests reject a foreign executable, an incomplete legacy or managed package, and a locally edited managed launcher. |
| H-P6-02 | Pausing enablement after validation, uninstalling, then resuming publication left an autostart entry with no launcher. | Install, uninstall, enable, and disable share a lock on the home directory inode. Its identity is independent of whether the deployment exists. Uninstall invokes the internal cleanup while holding that lock. Deterministic tests confirmed actual lock contention for enable against uninstall, install against disable, and disable against enable. After enable followed by uninstall, both the launcher and login entry are absent. |
| H-P6-03 | Removing `hardware.py` from a copy of the actual source allowed activation. The launcher then failed with `ModuleNotFoundError` even for `--version`. Reinstalling the original source recovered. | Preflight checks the shared essential modules, statically named sibling imports, and the syntax of all packaged Python files before activation. Removing `hardware.py` or `desktop_session.py`, or introducing invalid Python, leaves the selected release and its working version command in place. The archived preview.4 rollback sequence still passes. |

Preflight compiles source without importing it or generating bytecode. It checks package structure and syntax. Dynamic imports, dependency behavior, and the intent of arbitrary source code remain outside this check. The advisory lock coordinates these four commands within the same home. Manual edits and older installer versions can bypass it.

### Correction validation

The full local suite passed 262 discovered tests, including the two private-session wrappers. Separate runs passed all 21 GTK4 cases, 6 GTK3 cases, and 31 mandatory isolated lifecycle tests. The strict C11 harness, default and experimental disposable builds, Python compilation, individual shell syntax checks, targeted Ruff, Demo JSON, desktop-entry validation, and the historical upgrade and rollback smoke sequence passed.

Only the deployment script, its tests, the tray autostart test, and this report changed from the initial Phase 6 intake. Earlier-phase helper, firmware, module staging, and telemetry files matched that intake. The branch and commit matched, and all 15 kernel build artifact hashes matched. Validation used temporary homes and private sessions. Live autostart, authorization, and physical hardware behavior remain outside the offline validation.
