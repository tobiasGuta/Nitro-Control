# Keyboard RGB preview

Open **Keyboard RGB** in Nitro Control. The keyboard drawing shows how a draft
palette or animation will look across the four zones. It updates as you edit
and can be used on its own in Demo mode.

## Static colors

1. Choose **Static**. Click a zone on the keyboard drawing or select its color
   swatch below the drawing to open the color picker.
2. Use the switch below each zone to preview that zone off. Turning it back on
   restores its selected draft color. The brightness slider dims the preview.
3. Use **Load preset** to start from a built-in palette or **Reset** to restore
   the default Midnight palette. Switching between Static and Dynamic preserves
   your Static draft.
4. On a supported RGB backend, choose **Apply to keyboard** to send the Static
   colors to the physical keyboard. Confirm the request and authenticate when
   prompted. Check the physical keyboard afterward.

With the AN515-58 native WMI helper, **Apply to keyboard** and **Restore lighting**
require the editor brightness to be **100%**. After applying, use **Fn+F9** and
**Fn+F10** to adjust the physical brightness. **Restore lighting** reinitializes
the native controller with the selected Static colors. See the
[native RGB guide](NATIVE-RGB.md) for compatibility, installation, and recovery.

## Dynamic effects

Choose **Dynamic** and select **Breathing**, **Wave**, **Zoom**, **Shifting**,
or **Neon**. The speed and brightness sliders change the simulation. Breathing,
Zoom, and Shifting also accept an effect color. Turn off **Animate preview** to
pause on the current frame.

Wave moves a continuous spectrum across the keyboard. Zoom spreads a glow from
the center toward both edges and contracts it again. Shifting moves the
selected color across the keys while keeping its hue.

### Apply a Dynamic effect to the physical keyboard

On supported AN515-58 hardware, select **Breathing**, **Neon**, **Wave**,
**Shifting**, or **Zoom** in Dynamic, then choose **Apply effect to keyboard**
and authenticate. Speed applies to the hardware. Breathing, Shifting, and
Zoom also send the selected color.
The brightness slider affects the drawing. Physical effects use 100% firmware
brightness and can continue after closing the app. To stop the effect, open
**Static**, set brightness to **100%**, then apply your palette.

Physical effects require the updated native RGB module and helper. Build the
module for the running kernel and follow the installation steps in the
[native RGB guide](NATIVE-RGB.md). The reference Fedora AN515-58 on BIOS V2.21
visually confirmed all five effects. The timed tests also verified firmware mode
and speed readback and restoration of all four Static colors.

## Try it on Fedora

From a checkout of the release, launch Demo mode:

```bash
/usr/bin/python3 -m nitro_control --demo
```

For a per-user upgrade, close the running dashboard and run
`./scripts/install-fedora.sh` from the release directory. Reopen Nitro Control
from the GNOME app grid and select **Keyboard RGB**. The preview works with the
regular GUI dependencies, including PyCairo. Existing optional fan and RGB
helpers can be reused.

## Install physical effects on Fedora

Use the release source on the four-zone **Acer Nitro AN515-58** with the stock
`acer_wmi` driver loaded. Build the module with `make`, a compiler, and the
`kernel-devel` package matching `uname -r`. The installer rejects an unsigned
module when Secure Boot is enabled. See the [native RGB guide](NATIVE-RGB.md)
for the driver prerequisites and recovery steps.

From the release checkout, close the dashboard and run:

```bash
make -C experiments/acer-wmi-rgb-enable-poc
sudo ./scripts/install-native-rgb-fedora.sh \
  "$PWD/experiments/acer-wmi-rgb-enable-poc/nitro_rgb_enable_poc.ko"
sudo ./scripts/install-rgb-helper-fedora.sh
./scripts/install-fedora.sh
```

Reopen Nitro Control and select **Keyboard RGB → Dynamic**. If **Apply effect
to keyboard** is disabled in the normal dashboard, check
`~/.local/bin/nitro-control --rgb-probe` for an available `native-wmi` backend.
After a kernel update, rebuild and stage the module for the new kernel.

If firmware refuses an effect, open **Static**, set brightness to **100%**,
and choose **Apply to keyboard**. If the keyboard is dark, use **Restore
lighting** with the same Static palette. For a GUI rollback, reinstall the app
from the [v0.4.0-preview.4 source release](https://github.com/tobiasGuta/Nitro-Control/releases/tag/v0.4.0-preview.4).
The older GUI can still send Static requests to the updated helper.
