# Optional Nitro Control GNOME tray indicator (experimental branch)

**Status:** Experimental, opt-in feature branch
`feat/optional-tray-indicator`. **Not part of the published
`v0.4.0-preview.2` tag.** It needs a real Fedora/GNOME tray
compatibility test before merging or shipping.

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
- The previously published `v0.4.0-preview.2` release, installed GUI
  and native RGB experimental backend are **not affected by this branch**.
