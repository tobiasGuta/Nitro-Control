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
the center toward both edges and contracts it again. Dynamic effects are
on-screen simulations. Physical animation support has yet to be validated on
the reference Fedora AN515-58.

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
