# Nitro Control v0.4.0-preview.4

This Fedora source preview adds a full keyboard drawing to **Keyboard RGB**.
Get the tagged source and GitHub-generated source archives from the
[GitHub Releases page](https://github.com/tobiasGuta/Nitro-Control/releases/tag/v0.4.0-preview.4).

- **Static** shows four independently editable zones, per-zone toggles, and a
  live brightness preview. Click the keyboard drawing to open a zone's color
  picker.
- **Dynamic** simulates Breathing, Wave, Zoom, Shifting, and Neon. Use the speed
  and brightness controls to explore each effect. Wave sweeps smoothly across
  the keys, and Zoom expands from the center and contracts again.
- Physical keyboard Apply uses the existing Static RGB backend and its
  confirmation and authorization. Dynamic effects run in the preview.

See the [Keyboard RGB guide](KEYBOARD-RGB.md) for usage and native WMI limits.

## Upgrade on Fedora

Close the dashboard. From the extracted source archive or release checkout,
run:

```bash
./scripts/install-fedora.sh
```

Reopen **Nitro Control** from the app grid. To check the installed version, run
`~/.local/bin/nitro-control --version`. Existing optional fan and RGB helpers
can be reused. To roll back, run the same installer from the
[v0.4.0-preview.3 source](https://github.com/tobiasGuta/Nitro-Control/releases/tag/v0.4.0-preview.3).
