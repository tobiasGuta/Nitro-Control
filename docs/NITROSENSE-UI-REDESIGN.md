# Nitro-Control interface redesign — NitroSense-inspired GTK4 draft

**Status:** Experimental GUI branch `feat/nitrosense-inspired-dashboard`, not
merged into `main`. The supplied NitroSense reference screenshot is a
**visual direction**, not evidence that its buttons or sensor measurements
exist on this Linux laptop. This is an **original Nitro-Control interface**,
not a copied Acer application, and it uses no Acer logos or proprietary
NitroSense images.

## Fedora screenshot review — first draft vs reference

The owner ran the standalone `--demo` preview on the actual Fedora 45
desktop and supplied a **1649 × 928** screenshot. This is a real GTK render
of the branch, not a product mockup. Findings:

1. **Main mismatch:** The left Adwaita power-profile group expanded to more
   than half the bottom row, squeezing the monitoring charts into about a
   third of the width. The reference uses a *narrow left mode column and
   broad right monitoring area*.
2. The initial fan blades looked like a chunky gear; the reference uses
   finer, curved turbine blades.
3. The reference's steel-gray tabs have gradients and chamfered bevels.
   The first draft used flatter gray shapes.
4. The reference's CPU/GPU charts are wide and relatively short. The first
   draft used extra temperature tiles and taller narrow charts.
5. The preview's straight temperature traces were **correct for the constant
   `--demo` snapshots**. We must not fabricate fluctuations merely to
   imitate a graph in someone else's screenshot.

**Refinements implemented after the screenshot:**

- Replace the wide `Adw.PreferencesGroup` power selector with a compact
  native `Gtk.DropDown`, the **same system-advertised GNOME profiles**,
  explicit Apply confirmation and status, and a narrow mode column.
- Expand the monitoring column, make temperature summary labels compact,
  and shorten/widen the two real-sample graphs with finer red grids and
  observed minimum/maximum temperatures.
- Draw 28 narrower swept fan blades with original brushed-metal shading,
  and add gradient/beveled angular section headers.
- Do not add fan PWM, NitroSense Max/Custom/CoolBoost, fabricated fan
  affiliation, simulated non-demo sensor readings, or unattended RGB writes.

**Next review gate:** The owner must open this updated draft on the same
Fedora desktop, provide an updated screenshot, and inspect all three pages
before this design is considered visually complete or safe to merge.
Comparison should prioritize **layout proportions and panel hierarchy**,
then detailed typography and graphite texture. Live sensor and power/RGB
tests are still separately required; `--demo` confirms only the design.

## Layout mapped to the supplied reference screenshot

| Screenshot visual element | GTK4 implementation | Data / permission boundary |
| --- | --- | --- |
| Charcoal/black textured dark window, angular silver-gray panel tabs | Custom GTK CSS and Cairo-drawn steel `Ribbon` panel headers | Presentation only |
| Large upper turbine/fan readouts | Two drawn `FanDial` graphics with passive RPM labels | Existing Acer HWMON `Snapshot.fans`; if unavailable, show **Unavailable**; no assumed fan/CPU/GPU mapping or RPM capacity |
| Auto/Max/Custom fan buttons in NitroSense | **Not implemented** as controls | Nitro-Control cannot safely set fan PWM or CoolBoost; display actual firmware fan mode as read-only |
| Two lower CPU/GPU monitoring graphs | `TelemetryGraph` with grid, real rolling CPU package temperature, NVIDIA GPU temperature and utilization when available | Bounded actual data samples, never synthetic curves, zeros, CPU load, GPU temperature, or CPU clock placeholders |
| Power/Mode column | Existing GNOME power-profile selector and explicit Apply/confirmation | Only profiles advertised by the system service; raw Acer firmware modes stay read-only |
| Hardware details | Dedicated page for NVIDIA, Acer firmware and TuneD profile, fans, Acer and NVMe thermal sensors | Read-only discovery, unrecognized Acer sensors remain unidentified |
| Keyboard RGB | Dedicated page for existing four-zone editor, presets, in-memory preview and explicitly authorized Apply/Restore | Exact existing safety restrictions, per-action Polkit and guarded native WMI brightness **100%** initialization |

## App architecture and visual choices

- **GTK4 + Libadwaita**, consistent with the original project; custom Cairo
  rendering is limited to **decorative dials and live graphs**.
- A native `Gtk.StackSwitcher` navigates **Dashboard**, **Hardware details**,
  and **Keyboard RGB**, so no hardware details or existing RGB features are
  removed to make room for the screenshot-inspired presentation.
- Dark near-black panel interiors, neutral steel-gray angled header tabs,
  red CPU/temperature emphasis, amber secondary GPU utilization trace,
  and geometric turbine blades. The screenshot's layout proportions are
  the target; fonts, window decorations and rendering follow GTK on Fedora.
- Fan gauges report an unscaled absolute **RPM** number. They do *not*
  imply a safe fan maximum or represent controllable speed.
- History uses a bounded series of real sensor samples, with gaps when
  telemetry is absent. The first graph contains only readings since the
  window was opened; the curves grow naturally over time.
- Demo mode, when explicitly enabled with `--demo`, remains labeled as
  sample data and cannot perform real RGB or power changes.
- The dashboard never changes fans, EC state, WMI state, BIOS settings,
  Linux modules, or service boot configuration merely to render.

## Development installation (opt-in, keep stable version as needed)

Use a **separate checkout/worktree**, not the existing dirty experimental
branch. For example:

```bash
cd /mnt/Development/Tools/Nitro-Control
git fetch origin feat/nitrosense-inspired-dashboard
git worktree add --detach \
  "$HOME/Nitro-Control-ui-preview" origin/feat/nitrosense-inspired-dashboard
```

For a development trial, first check that Fedora has the GUI dependencies:

```bash
sudo dnf install python3-gobject python3-cairo gtk4 libadwaita
cd "$HOME/Nitro-Control-ui-preview"
python3 -m unittest discover -s tests -v
/usr/bin/python3 -m nitro_control --demo
```

**Do not run the per-user install script yet if you wish to keep the existing
stable application launcher unchanged.** The command above runs the
checkout as a process; `--demo` shows clearly labeled sample data while
testing the visual design and disables physical changes. To test with
live sensors later, omit `--demo` while keeping the privilege-confirmation
steps unchanged.

To return to stable behavior, close the preview process and launch
`~/.local/bin/nitro-control` (the separately installed version).
Installing from this branch with `bash scripts/install-fedora.sh` would
replace that per-user UI copy, but **would not install drivers**.

## Acceptance checks before merging

- Screenshot inspection on **Fedora 45 + GTK4/Libadwaita** at a laptop-sized
  window, including dark background, turbine gauges, section bars, live
  traces and navigation; refine spacing/colors based on real screenshots.
- Fan names remain **Fan 1/Fan 2** unless explicitly labeled by firmware;
  no CPU/GPU fan association is invented.
- CPU temp, GPU temp/utilization, fans and additional sensors render
  unavailable instead of fabricated values.
- Existing GNOME power-profile choices and **confirmation** work unchanged.
- Four-zone RGB **preview** stays in-memory. Native physical Apply/Restore
  still require 100% editor brightness, explicit confirmation and authorized
  root-owned helper. Fn+F9/Fn+F10 and fan telemetry remain functional.
- Synthetic tests and Python compilation pass; **GUI visual and physical
  hardware regression tests must be performed on the reference laptop**.
  Offline CI alone is insufficient to claim a pixel-perfect replica.

## Why this remains a draft

The Python and Cairo widget structure is implemented on this branch, but
the developer environment does not have Fedora's GTK4 GUI stack or the Acer
hardware to capture a real application screenshot. **Pixel-level fidelity,
responsive breakpoints, actual GTK CSS rendering and live UI interactions
need human review on the reference Fedora laptop before merging.**

Related: [native RGB safety guide](NATIVE-RGB.md),
[incident investigation](RGB-INCIDENT-2026-11.md),
[issue #3](https://github.com/tobiasGuta/Nitro-Control/issues/3).
