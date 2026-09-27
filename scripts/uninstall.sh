#!/usr/bin/env bash
set -euo pipefail
rm -rf -- "$HOME/.local/share/nitro-control"
rm -f -- "$HOME/.local/bin/nitro-control" "$HOME/.local/share/applications/io.github.tobiasguta.NitroControl.desktop"
echo 'Nitro Control user installation removed; source checkout untouched.'
