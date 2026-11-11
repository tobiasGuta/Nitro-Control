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

## Second Fedora screenshot review — proportions corrected

The owner supplied a second screenshot (native GTK demo preview at
approximately 1638 × 895). The reference's main **top fan section plus
a narrow lower left control column and broad right monitoring charts**
is now visibly reproduced. The lower mode column is no longer wider
than the chart region; turbine blades are more curved and steel header
gradients are clearer.

The remaining layout issues visible in the second screenshot were
**duplicated RPM numbers** in both the fan dials and the upper panel's
left text column, a mostly **empty lower half of the mode panel**, and
still relatively plain panel background treatments compared with
the dense NitroSense reference. The next refinement:

- Use the left fan column for the **actual read-only firmware-reported
  fan modes** and show numerical RPM only in the two turbine dials.
  Deliberately no Max/Custom buttons or unsupported CPU/GPU-fan mapping.
- Fill the mode panel with the **real currently reported Acer firmware
  profile and TuneD profile** (read-only), keeping the existing
  GNOME power-profile selection and explicit confirmation.
- Preserve the wide real-only CPU/GPU graphs; their straight lines in
  `--demo` are correct for the constant supplied demo samples and
  must not be decorated with fabricated spikes.

**Pending actual screenshot review:** The owner has not yet visually
validated these latest changes, nor the Keyboard RGB and Hardware
details tabs. No changes to fan hardware, Polkit or RGB drivers were made.

## Third Fedora screenshot — dashboard visual review

The owner shared a third real Fedora GTK preview screenshot after the
fan-status/sidebar and graphite scanline changes.

- **Confirmed visually:** The reference-like main proportions now hold:
  a broad top turbine section, a narrow lower-left power panel, and
  wide stacked CPU/GPU monitoring graphs. The fan RPM numbers appear
  once, inside their gauges. The left fan area shows **real firmware
  mode readouts** instead of duplicated RPM; the power panel shows
  **read-only Acer and TuneD profile** values.
- **Remaining cosmetic differences:** This is a GTK-adapted original
  interface, not a pixel-for-pixel recreation of Acer's desktop window.
  Fan-blade shape, type treatments and panel margins differ.
- **Refinement:** Removed mentions of unsupported NitroSense Max/Custom/
  CoolBoost functions from the prominent dashboard labels; the actual
  capability is summarized simply as **no manual fan speed control**.
  In demo mode the power Apply button is deliberately disabled, and
  its CSS now uses a muted disabled state instead of a prominent red
  active-looking button.
- **Pending checks:** Only the dashboard page is visually reviewed so
  far; obtain **Hardware details** and **Keyboard RGB** screenshots,
  then verify the control labels, responsive layout, four-zone RGB
  safety guard and the real-system power-mode confirmation.

The screenshots use `--demo`, so their displayed temperatures,
profiles, fan speeds and flat graph traces are **sample values**, not
measurements of this laptop's live condition. Do not interpret them
as live hardware readings.

## Animated turbines — RPM-proportional, visual-only

The original NitroSense reference suggests moving fan blades; the GTK/Cairo
`FanDial` now animates its rotor blades **independently for each reported
fan RPM**. A higher observed RPM produces proportionally faster on-screen
movement; `0 RPM`, a missing sensor reading, or an invalid RPM value stops
the animation entirely. Actual RPM numbers continue to come from Acer
HWMON, never from the animation.

**The visual rotation is intentionally slowed down**, because a fan at
2,000 RPM physically makes over 33 revolutions per second and would alias
badly on a desktop monitor. The purely presentational mapping is
`rpm / 15000` *visible turns per second* (with a visual-only cap at 7,500
RPM); it is **not** physical rotation speed and places no restriction on
the real fan. Each GUI animation frame is driven by the monotonic GTK
main loop (~30 FPS) and capped for long pauses to avoid a sudden jump on
resume. The timer stops redrawing fan dials on other tabs or when the
window is unmapped, and stops on window close.

- Hardware samples remain on the **unchanged four-second worker-thread
  polling cadence**. No extra HWMON calls, WMI operations, PWM writes,
  driver loads or platform-profile changes are made by the animation.
- Demo mode uses the **existing clearly labeled sample RPMs**, so the
  preview blades move as demonstration only, not live hardware testimony.
- Added pure, GTK-independent tests for stationary/missing data,
  proportional per-fan speeds, display-speed slowing, and safe
  suspend/resume timing.

This is an additional **visual acceptance gate**: review a running preview
on the Fedora laptop for smooth motion (a still screenshot cannot verify
animation), check that faster actual fan RPM gives faster-looking blades,
and confirm an absent/stopped sensor is visually stationary.

## Live Fedora test: maximized-window layout

The owner provided **two live-mode Fedora screenshots** (not `--demo`),
with reported CPU/GPU temperatures and fan speeds being read from the
laptop. The smaller window has the desired top fan dials and lower split
dashboard, but **maximizing previously left wide unused black gutters**:
`page()` placed every tab inside an `Adw.Clamp(maximum_size=1500,
tightening_threshold=1050)`, which constrained the dashboard even when
the window had room to grow.

**Current branch adjustment:** Let the **Dashboard** page use its
`Gtk.ScrolledWindow` content directly, with full-width horizontal
expansion and a modest 20px inset. Ensure the live-monitoring panel is
`hexpand` so extra width goes to the graphs while the left power column
stays compact and the two fan dials redistribute across the upper area.
Continue clamping **Hardware details** and **Keyboard RGB** to readable
form widths rather than stretching color pickers or settings rows across
an ultra-wide desktop.

**Pending visual verification:** Reopen the updated branch and compare
both the ordinary and maximized dashboard on Fedora. This is a layout-only
fix, not a new sensor or power feature; it changes neither fan/WMI access
nor the independent RPM-based turbine animation. Horizontal behavior
on especially narrow windows should also be checked separately.

## Maximized-height refinement — fourth live Fedora screenshot

After the maximized-width fix, the owner supplied another live-sensor
screenshot showing both fan gauges and temperature graphs use the full
horizontal viewport. One smaller issue remained: **a strip of unused
background below the dashboard panels and footer** when maximized.

The dashboard page now allows its vertical content to expand, and the
**lower Power Mode and Live Monitoring panels grow to occupy the
remaining viewport height**. The monitoring section distributes the
extra room to the two actual CPU/GPU history graphs. The top fan
section and RPM dials remain at their compact reference proportions;
the footer remains below the panels. Shorter windows keep the existing
GTK vertical scroll behavior rather than forcing larger minimum heights.

`Hardware details` and `Keyboard RGB` retain their separate readable
form-width clamping and existing control/safety paths. No new hardware
measurements, privileged controls, automatic RGB restore or direct fan
control are introduced.

**Pending verification:** This change is committed and covered by
synthetic layout contract tests, but the owner should review both
maximized and smaller-window GTK renders to confirm that the empty
bottom strip shrinks without stretching charts awkwardly or clipping
the footer.

## Fifth Fedora screenshot — maximized height accepted

The owner provided a live-sensor screenshot after the vertical expansion
change and said **"looks way better now"**. On the maximized Fedora desktop:

- Both **wide side gutters and the prominent empty bottom strip** seen in
  earlier screenshots are now resolved to ordinary window padding.
- The turbine RPM gauges retain their proportions while the compact power
  column and the stacked, real-only CPU/GPU history graphs fill the
  available area.
- The application continues to visibly identify **fan control mode as
  Unknown** when no reliable control-mode data is available; it does not
  invent fan modes or a CPU/GPU fan mapping.

**Dashboard layout feedback: accepted for this screen size.** This is visual
validation of a GTK rendering, *not* a new test of physical RGB Apply,
real power-profile changes, or operation on all display sizes. The
RPM-based animation is implemented but a still screenshot cannot confirm
motion quality. Keep the PR as draft until Hardware details and Keyboard
RGB page review and live workflow safety checks are complete.

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
