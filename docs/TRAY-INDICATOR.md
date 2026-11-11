# Optional Nitro Control GNOME tray indicator

**Status:** Merged into `main` via
[PR #9](https://github.com/tobiasGuta/Nitro-Control/pull/9)
(commit `efa45e92834918e311a9973636e81ba88cfa4317`),
after successful real Fedora 45 / AN515-58 GNOME tray tests.
The tray is **optional**, starts explicitly with `--tray` or
after the user enables the proposed GTK4 dashboard **Keep in tray**
setting in this branch. GNOME login autostart is **disabled by default**.
The published
`v0.4.0-preview.2` tag does **not** contain this post-release feature.
The GTK/Ayatana warning investigation remains open in
[issue #10](https://github.com/tobiasGuta/Nitro-Control/issues/10).

## What it does

The existing [GNOME AppIndicator and KStatusNotifierItem Support
extension](https://extensions.gnome.org/extension/615/appindicator-support/)
displays icons provided by applications through the desktop's
StatusNotifierItem/AppIndicator protocol. The extension is **not**
the Nitro Control app, and Nitro Control does **not** install, update or
modify that extension.

The tray process presents a small icon with a menu showing only:

- **CPU package temperature**, if the existing hardware reader identifies it;
- **NVIDIA GPU temperature**, if an NVIDIA reading is available;
- the two **actual Acer HWMON fan RPM** sensors, named CPU/GPU only on the
  verified AN515-58 model; unknown models keep generic Fan 1 / Fan 2;
- last sensor update timestamp;
- **Open Nitro Control** (activates the existing full GTK4 dashboard);
- **Refresh readings** (a new read-only sample);
- **Quit tray** (stops the indicator and its polling, not the full dashboard).

An unavailable sensor says **Unavailable**. A fan reporting 0 RPM
shows **0 RPM**, not an assumed error. No temperatures, fans,
control modes, percentages or hardware features are guessed. Refresh
runs every **12 seconds** while the user explicitly keeps the tray
running; hardware reads occur off the tray menu's GTK main thread.

## Why there is a separate process

The full Nitro Control app is **GTK4/Libadwaita**. Fedora's
`libayatana-appindicator-gtk3` exports a `Gtk.Menu` using **GTK3**.
GTK3 and GTK4 must not be imported into the same interpreter. Therefore
`python3 -m nitro_control --tray` launches a standalone, user-session
GTK3 process; selecting **Open Nitro Control** spawns/activates the
normal GTK4 app as a separate process. GNOME's extension supplies the
top-bar visual integration. The tray's own icon uses the available
`utilities-system-monitor` icon theme name.

## Fedora dependencies

The dashboard already requires Python 3.11+, PyGObject, GTK4,
Libadwaita and PyCairo. For the **optional tray** add Fedora's
GTK3 Ayatana typelib and runtime:

```bash
sudo dnf install python3-gobject gtk3 libayatana-appindicator-gtk3
```

The Fedora 45
[package](https://packages.fedoraproject.org/pkgs/libayatana-appindicator/libayatana-appindicator-gtk3/)
provides `AyatanaAppIndicator3-0.1.typelib`. The extension shown in
the owner's screenshot must remain **enabled** in GNOME Extensions;
without it, GNOME may not display the indicator menu.

## Safe source-only preview

Do **not** reinstall the published GUI just to try this unmerged branch.
From the existing Nitro Control Git repository, create a separate worktree:

```bash
cd /mnt/Development/Tools/Nitro-Control
git fetch origin feat/optional-tray-indicator
git worktree add --detach "$HOME/Nitro-Control-tray-preview" origin/feat/optional-tray-indicator
cd "$HOME/Nitro-Control-tray-preview"
# Only after installing the optional Fedora dependency above:
/usr/bin/python3 -m nitro_control --tray
```

The command runs in the foreground. Look for the Nitro Control icon in
the GNOME top bar, open its menu, inspect real fan/temperature values,
and try **Open Nitro Control**. Confirm that **Quit tray** removes the
icon and ends the process. Run `--tray` a second time in another
terminal to confirm it reports **already running** without duplicating
icons. Ctrl+C also ends the development preview. The normal dashboard
can be launched independently and continues to work normally.

`--tray` cannot be combined with `--demo`, `--once` or `--rgb-probe`.
No fake sample values will appear in a live tray.

## Test the new dashboard setting on Fedora

Because the new setting is **not** in the published release yet,
update only the existing separate tray preview checkout. First choose
**Quit tray** if the old `--tray` process is still running, so the
test starts without an existing icon. Then use:

```bash
cd "$HOME/Nitro-Control-tray-preview"
git status --short
git fetch origin feat/dashboard-tray-setting
# Continue only when git status has no local edits:
git switch --detach origin/feat/dashboard-tray-setting
/usr/bin/python3 -m nitro_control
```

In the GTK4 dashboard, click the **gear icon at top right**, enable
**Keep Nitro-Control in the system tray**, and verify **one** indicator
appears without a manual `--tray` command. Close the dashboard window,
then use the GNOME indicator's **Open Nitro Control** to reopen the
dashboard. Confirm the icon stays available after closing the GUI.
Quit tray, reopen the dashboard normally from the same checkout, and
confirm the setting automatically restarts a **single** indicator.

To turn the feature back off, use the **same gear menu** and disable
the switch, then select **Quit tray** to stop the current process.
New dashboard openings must no longer start the tray.

**Do not remove the source preview worktree while its tray is
still running.** The tray's Open Nitro Control action launches the
dashboard from that same checkout. Close both processes first.

**No sudo, driver rebuild, or login autostart setup is required**
for this preview. The existing `v0.4.0-preview.2` installation and
experimental native RGB module are unaffected.

## Dashboard setting: keep Nitro Control in the tray

A post-release improvement under review in
`feat/dashboard-tray-setting` adds **Keep Nitro-Control in the system
tray** to the dashboard's **top-right settings (gear) menu**. This
does **not** change the published `v0.4.0-preview.2` tag or enable
login autostart.

1. Open the GUI normally from the **development source checkout**
   using `/usr/bin/python3 -m nitro_control` (no `--tray` command).
2. Open the header gear menu and enable
   **Keep Nitro-Control in the system tray**.
3. The GTK4 app saves a **user-only, default-off preference** in
   `$XDG_CONFIG_HOME/nitro-control/tray-preferences.json` (or
   `~/.config/nitro-control/tray-preferences.json`) and starts the
   separate GTK3 tray process immediately. The GNOME AppIndicator
   extension is required for the top-bar icon to be visible.
4. Close the main GTK4 window: its tray process **keeps running**.
   Select **Open Nitro Control** from that menu to reopen the GUI.
5. The next time the GUI is opened normally, an enabled preference
   starts the tray **only if needed**: the existing private
   per-session lock prevents duplicates.
6. **Quit tray** ends background monitoring. If the setting remains
   enabled, starting the dashboard again will start the tray again.
7. Turning the switch **off** stops *future automatic launches from
   the dashboard*; it deliberately does not kill a tray process that
   is already running. Use the tray's **Quit tray** menu item to stop
   it immediately.

The option is disabled in **demo mode**, ensuring that running the
simulated dashboard never unexpectedly starts live sensor polling.
Other CLI paths such as `--once`, `--rgb-probe`, and `--version`
never start the tray.

**This is not login autostart.** Closing/reopening the dashboard works
as described after opting in, but to start the indicator at GNOME
login *without opening the dashboard*, use the separate explicit
autostart scripts documented below. They remain off by default.

**Dependency failure:** If the optional
`libayatana-appindicator-gtk3` package or GNOME extension is missing,
the dashboard stays usable, and the settings menu displays a diagnostic
message. Installing/enabling those dependencies is a separate user
choice. The GUI setting itself never asks for root and never changes
RGB, PWM or power hardware.

### Fedora test checklist

- [ ] Without enabling the switch, opening the GUI does **not** add a tray icon.
- [ ] Switch on in the gear menu: one GNOME tray icon appears.
- [ ] Close the dashboard: indicator remains visible, with real values.
- [ ] Open the dashboard from the tray, then close it again: no duplicate icons.
- [ ] Quit tray: icon disappears, sensor polling stops.
- [ ] Launch dashboard normally again: one indicator reappears, because the preference persisted.
- [ ] Switch off: no tray is auto-launched on the **next** dashboard launch; stop any already running indicator via **Quit tray**.
- [ ] Confirm `--demo` doesn't start live monitoring, and the existing RGB WMI safety gates remain unchanged.

Keep this feature under review until the owner confirms those live
Fedora 45 GNOME results; automated tests alone cannot establish
real top-bar behavior.

## Optional start at login — disabled by default

The normal per-user `scripts/install-fedora.sh` **does not enable
tray auto-start** and does not install GTK3 tray dependencies. After
reviewing/testing the branch and *deliberately installing* a version
that contains `--tray`, the user may opt in with:

```bash
bash scripts/enable-tray-autostart-fedora.sh
```

This creates **only**
`~/.config/autostart/io.github.tobiasguta.NitroControl.Tray.desktop`,
pointing at the *per-user* `~/.local/bin/nitro-control --tray` launcher.
It starts at the **next GNOME login**, not immediately. The script
refuses root, unknown existing entries and symlink overwrites.

Disable future automatic launches with:

```bash
bash scripts/disable-tray-autostart-fedora.sh
```

Disabling auto-start does not terminate a tray already running;
select **Quit tray** for that. Neither script installs/loads modules,
modifies the user's GNOME extension or changes RGB/fan/power state.

## First real Fedora GNOME test — November 11, 2026

The owner installed Fedora 45's `libayatana-appindicator-gtk3` and
related Ayatana dependencies, then ran the **unmerged branch** from
`$HOME/Nitro-Control-tray-preview` at commit `2bb621c`. The owner
provided a GNOME screenshot showing the **indicator icon in the top
right**, with its actual menu visible:

- **CPU temperature:** 81°C
- **GPU temperature:** 48°C
- **CPU fan:** 2,803 RPM
- **GPU fan:** 3,157 RPM
- Timestamp, **Open Nitro Control**, **Refresh readings**, and **Quit
  tray** present in the menu

They subsequently reported that **Open Nitro Control works** and
**Quit tray works**: the icon disappeared and the foreground
`python3 -m nitro_control --tray` command returned to the normal
terminal shell prompt after approximately 1m16s. Returning the prompt
is expected; the user explicitly ended the foreground tray process.

**Confirmed passing:** GNOME AppIndicator integration, real read-only
CPU/GPU telemetry, the open-dashboard action, the quit action, and
clean completion of the foreground command. These were **live Fedora
results**, not a screenshot-only demo.

**Nonfatal runtime warnings in that initial test:**

```text
libayatana-appindicator-WARNING: libayatana-appindicator is deprecated.
Please use libayatana-appindicator-glib in newly written code.

Gtk-CRITICAL: gtk_widget_get_scale_factor:
assertion 'GTK_IS_WIDGET (widget)' failed
```

The first warning comes from the installed Ayatana AppIndicator-gtk3
library, **not from our native RGB driver**. Upstream has publicly
discussed its GTK3 library deprecation and transitional support while
the GTK-free GLib implementation matures. A current GNOME/StatusNotifier
integration path must be verified before switching APIs, not guessed.
See [upstream library discussion](https://mail-archive.com/debian-bugs-dist%40lists.debian.org/msg2123677.html)
and [GLib-only implementation](https://github.com/AyatanaIndicators/libayatana-appindicator-glib).

The GTK widget assertion was emitted around **Quit tray** in the
reported terminal transcript, with no reported functional failure or
crash; the exact caller and cause are **unknown**. Do not claim it is
fixed, definitively in upstream GTK, or universally harmless. If
repeatable on clean shutdown, collect one targeted GTK backtrace or
minimal Ayatana reproducer before modifying shutdown handling. Do not
silence GTK criticals globally.

**Remaining acceptance tests:** Try **Refresh readings** and confirm
the timestamp updates; invoke a second `--tray` process while one is
already running to confirm the single-instance guard; recheck
relaunch/quit stability. Optional login autostart remains off and is
not required to merge. Verify the warnings' behavior on a fresh
session. The stable published `v0.4.0-preview.2` remains untouched.

## Second Fedora GNOME test — refresh and single-instance PASS

On November 11, 2026, the owner returned with a second live
GNOME/Ptyxis screenshot, showing **two terminals**:

- Left: original `/usr/bin/python3 -m nitro_control --tray`
  process still running; GNOME top bar retained **one Nitro Control
  status icon**.
- Right: a second invocation of the same command immediately printed
  `Nitro Control tray is already running.` and returned to the
  shell prompt. **No duplicate indicator** was created.

The owner also explicitly reported that **everything in the requested
final test passed**, including the **Refresh readings** action
(updating the timestamp/readings), relaunch and normal **Quit tray**
behavior. The first run had already confirmed that opening the full
GTK4 Nitro Control dashboard works correctly.

**Outcome:** The user-tested core indicator workflows now pass on
the reference Fedora 45 GNOME session: real read-only sensor menu,
open dashboard, refresh, single instance and quit. Autostart remains
**optional/off by default** and was not enabled for this test.

The second screenshot shows the **Ayatana GTK3 deprecation warning**
from the first running process, but no newly shown GTK widget
assertion in that frame. Since the screenshot was taken before
shutdown, it **does not prove** that the intermittent
`gtk_widget_get_scale_factor` assertion is fixed or never recurs.
The dependency warning and previously reported shutdown assertion
remain documented observations, not evidence of lost functionality
or native RGB problems.

## Safety, limitations and review gates

- The tray uses the existing **read-only** `HardwareReader.collect()`.
  It does **not** call `RGBClient.apply`, change power profiles, touch
  PWM/sysfs writes, invoke Polkit, or load a kernel module.
- The reader may invoke normal, **unprivileged** `nvidia-smi` and
  `tuned-adm active` subprocesses to read status, as the full app does.
- The tray holds a per-user lock under the session's private
  `XDG_RUNTIME_DIR` to prevent duplicate instances. Quit tray or
  logout ends monitoring and releases the lock.
- Screenshots can't prove an indicator actually registered over D-Bus.
  The owner must verify the icon/menu in a **real Fedora 45 GNOME
  session** and report it before we can merge.
- Check missing NVIDIA sensors, display logout/login and status-menu
  stability, and that the dashboard still launches independently.
- A login autostart file is **strictly optional and OFF by default**.
- The published `v0.4.0-preview.2` release, existing installed GUI
  and native RGB experimental backend are **unaffected by merging this
  optional source feature**.
