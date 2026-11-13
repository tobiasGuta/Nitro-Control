# Nitro Control v0.4.0-preview.3

This Fedora source preview adds independent fan control on the Acer Nitro
AN515-58. Get the tagged source and GitHub-generated source archives on the
[GitHub Releases page](https://github.com/tobiasGuta/Nitro-Control/releases/tag/v0.4.0-preview.3).

## Fan control

Open **Dashboard → Fan monitoring** and choose **Auto**, **Max**, or **Custom**.
Custom has independent CPU and GPU sliders with a per-fan Auto toggle. Max
asks for confirmation before requesting full speed. Applying any mode requires
the optional administrator-authorized fan helper. The app uses the stock
`acer_wmi` hwmon interface for fan changes.

On the reference AN515-58 running Fedora 45 with kernel
`7.2.15-300.fc45.x86_64`, both fans reached 7,692 RPM in Max. At about 45% in
Custom, the CPU and GPU fans reached 3,529 and 3,614 RPM, respectively, when
tested one at a time. Both returned to Auto. The desktop, firmware, and
TuneD profiles stayed on Balanced throughout. See the
[fan-control guide](FAN-CONTROL.md) for the test scope, troubleshooting, and
helper removal.

This release also includes the optional [GNOME tray indicator](TRAY-INDICATOR.md)
and **Keep Nitro-Control in the system tray** setting added to `main` after
preview.2.

## Install or upgrade on Fedora

Extract the source archive or check out the release tag. With the
[GTK dependencies installed](../README.md#run-from-a-checkout-on-fedora),
close the dashboard and run these commands from the release directory:

```bash
./scripts/install-fedora.sh
sudo ./scripts/install-fan-helper-fedora.sh
```

The first command updates your per-user app and launcher. Run the second
command if you want fan controls and have not already installed the fan
helper from the fan-control branch. Open **Nitro Control** from the app grid
and check that the footer shows `0.4.0-preview.3`.

Demo mode and unsupported models retain fan monitoring. For rollback, apply
**Auto** to both fans before removing the helper or reinstalling the prior
dashboard. The [fan-control guide](FAN-CONTROL.md#remove-the-helper) lists the
removal command.
