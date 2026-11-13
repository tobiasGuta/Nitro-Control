# Independent fan control on the Acer Nitro AN515-58

Nitro Control can set the CPU and GPU fans through the stock Linux `acer_wmi`
driver. Fan changes use the driver's hwmon interface. The desktop power-mode
selector continues to use Fedora's power-profile service.

## Install and open the dashboard

Use a source checkout that contains the fan-control feature and install the
[GTK dependencies in the README](../README.md#run-from-a-checkout-on-fedora). From the checkout:

```bash
sudo ./scripts/install-fan-helper-fedora.sh
/usr/bin/python3 -m nitro_control
```

Close an already open Nitro Control dashboard before launching the checkout. To
update the copy opened by GNOME's app launcher, run `./scripts/install-fedora.sh`
from the same checkout and relaunch Nitro Control. The fan helper is a separate
installation and applies only when you authorize a fan change.

## Set the fans

In **Dashboard → Fan monitoring**, choose a mode and click **Apply fan settings**.
Selecting a mode or moving a slider edits the pending settings. Apply opens an
administrator authentication prompt.

| Mode | Behavior |
| --- | --- |
| **Auto** | Returns both fans to firmware control. |
| **Max** | Requests full speed for both fans. A separate confirmation lets you cancel before authentication. |
| **Custom** | Sets the CPU and GPU independently. Each fan can stay in Auto or use a fixed setting from 45% to 100%. |

The sliders show the requested settings. The dials show measured RPM, which can
take a few seconds to settle. Max and Custom can remain active after you close
the window. To end an override, select **Auto**, apply it, and verify that both
fan status cards report **Automatic**.

## Supported hardware and troubleshooting

- The write path requires an **Acer Nitro AN515-58**, the stock `acer_wmi` module,
  and both PWM channels on its hwmon device. The app locates the device on each
  refresh, so changes in the kernel's `hwmonN` numbering need no configuration.
- Demo mode and unsupported devices retain monitoring. If the dashboard asks
  for the optional helper, install it from the same checkout as the app.
- If a mode is unreported, apply **Auto** and check the status again. The
  reference laptop initially returned `ENXIO` when reading the mode, including
  on AC power. After an explicit Auto write, both channels reported Auto.
- The helper checks mode and Custom setting readback. If an apply fails
  partway through, the helper attempts to restore Auto for both fans and
  reports whether it verified the restoration. Recheck both fan cards after a
  failed apply.

## Hardware validation

Tested November 12, 2026 on an Acer Nitro AN515-58 with BIOS V2.21,
Fedora 45, and kernel `7.2.15-300.fc45.x86_64`. The laptop was on AC power for
the live tests. The desktop power profile, Acer firmware platform profile,
and TuneD profile all reported `balanced` throughout.

| Test | CPU fan | GPU fan |
| --- | ---: | ---: |
| Auto baseline | 2,238 RPM | 2,238 RPM |
| Both fans at Max | 7,692 RPM | 7,692 RPM |
| CPU Custom at about 45%, GPU in Auto | 3,529 RPM | ~2,255 RPM |
| GPU Custom at about 45%, CPU in Auto | ~2,238 RPM | 3,614 RPM |
| Auto restored after Custom | 2,222 RPM | 2,307 RPM |

The owner heard each fan ramp separately and later confirmed the patched
dashboard behaved as expected. The live Custom test covered one setting per
fan, with the other fan in Auto. Automated tests cover the supported Custom
range, two simultaneous Custom settings, rejected inputs, and partial-write
recovery using simulated sysfs.

[Linux acer-wmi support commit](https://github.com/torvalds/linux/commit/40901719bd28a129471f80f98edca42548273458) · [Nitro Control issue #12](https://github.com/tobiasGuta/Nitro-Control/issues/12)

## Remove the helper

Apply **Auto** while the helper is still installed and verify the two fan
status cards. Then run:

```bash
sudo ./scripts/uninstall-fan-helper-fedora.sh
```

This removes the helper and its PolicyKit action. The per-user dashboard
continues to show fan telemetry.
