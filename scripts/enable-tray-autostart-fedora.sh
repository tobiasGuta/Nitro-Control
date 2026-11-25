#!/usr/bin/env bash
set -euo pipefail
exec /usr/bin/python3 -IB "$(dirname -- "${BASH_SOURCE[0]}")/gui_deploy.py" enable-tray "${0##*/}" "$@"
