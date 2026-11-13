# Nitro Control v0.4.0-preview.5

This Fedora source preview brings physical **Breathing**, **Neon**, **Wave**,
**Shifting**, and **Zoom** to the four-zone keyboard on the reference Acer
Nitro AN515-58. Get the tagged source and GitHub-generated source archives
from the [GitHub Releases page](https://github.com/tobiasGuta/Nitro-Control/releases/tag/v0.4.0-preview.5).

- **Keyboard RGB → Dynamic → Apply effect to keyboard** sends the selected
  effect and speed to the Acer firmware after confirmation and authentication.
  Breathing, Shifting, and Zoom also use the selected color.
- The Shifting preview keeps that color as the light sweeps across the keys.
- The preview brightness slider controls the drawing. Physical effects use
  100% firmware brightness and can continue after closing the dashboard.
  To switch back, open **Static**, set brightness to **100%**, and apply your
  palette.

The five effects were observed on the physical reference keyboard on Fedora
45, BIOS V2.21, and kernel `7.2.15-300.fc45.x86_64`. The tests verified
firmware readback and restored the original Static colors after the timed
tests. Fan and firmware power-profile controls are separate from RGB.

## Upgrade on Fedora

From the release checkout or extracted source archive, close the dashboard and
run `./scripts/install-fedora.sh`. The installed launcher reports
`Nitro Control 0.4.0-preview.5`.

For physical effects, also build and install the updated native RGB module for
the running kernel and update the optional RGB helper. If you already installed
both during the physical effects testing, the dashboard update is sufficient.
The [Keyboard RGB guide](KEYBOARD-RGB.md#install-physical-effects-on-fedora)
has the commands, verification, and rollback steps.
