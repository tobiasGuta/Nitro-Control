#!/usr/bin/env bash
# AN515-58 one-variable hotkey experiment. Build-only; NEVER install or load.
set -Eeuo pipefail

SOURCE="${1:-/mnt/Development/Tools/Div-Linuwu-Sense-build-test}"
MODEL='Nitro AN515-58'
EXPECTED_KERNEL='7.2.7-200.fc44.x86_64'
EXPECTED_BASELINE_SHA='1ad1491e1639b5324b322660b3ab28ecd318127a0b0f0590d5a96a8e8b798ed5'

[[ $# -le 1 && -d "$SOURCE" && -f "$SOURCE/Makefile" && -f "$SOURCE/src/linuwu_sense.c" && -f "$SOURCE/src/linuwu_sense.ko" ]] || {
    echo 'Supply the existing locally patched Linuwu source directory, including the original built .ko.' >&2
    exit 1
}
[[ "$(cat /sys/class/dmi/id/product_name)" == "$MODEL" ]] || { echo 'Wrong laptop model.' >&2; exit 1; }
[[ "$(uname -r)" == "$EXPECTED_KERNEL" ]] || { echo 'Kernel changed; rebuild and review baseline first.' >&2; exit 1; }
[[ "$(sha256sum "$SOURCE/src/linuwu_sense.ko" | cut -d' ' -f1)" == "$EXPECTED_BASELINE_SHA" ]] || {
    echo 'Baseline binary differs from the physically tested module; refusing experiment.' >&2
    exit 1
}
[[ "$(modinfo -F vermagic "$SOURCE/src/linuwu_sense.ko" | cut -d' ' -f1)" == "$EXPECTED_KERNEL" ]] || {
    echo 'Baseline module vermagic mismatch.' >&2; exit 1;
}
if systemctl is-active --quiet nitro-control-rgb-driver.service || grep -q '^linuwu_sense ' /proc/modules; then
    echo 'Stop the managed driver service and unload Linuwu before preparing the experiment.' >&2
    exit 1
fi
command -v python3 >/dev/null && command -v make >/dev/null && command -v modinfo >/dev/null || {
    echo 'Python 3, make and modinfo are required.' >&2; exit 1;
}

mkdir -p "$HOME/.cache/nitro-control"
WORK="$(mktemp -d "$HOME/.cache/nitro-control/rgb-base-color.XXXXXXXX")"
mkdir -p "$WORK/src"
cp -- "$SOURCE/Makefile" "$WORK/Makefile"
cp -- "$SOURCE/src/linuwu_sense.c" "$WORK/src/linuwu_sense.c"

python3 - "$WORK/src/linuwu_sense.c" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
source = path.read_text()
old = '    status = set_kb_status(0, 0, input->brightness, 0, 0, 0, 0);'
new = '''    /* Diagnostic only: use zone 1 as the static fallback RGB instead of black. */
    status = set_kb_status(0, 0, input->brightness, 0,
                           (input->zone1 >> 16) & 0xff,
                           (input->zone1 >> 8) & 0xff,
                           input->zone1 & 0xff);'''
if source.count(old) != 1:
    raise SystemExit('Expected exact Linuwu call not found exactly once; no patch applied.')
if '#include <linux/string.h>' not in source or 'strncpy(' in source:
    raise SystemExit('The previously validated Linux 7.2 string-handling patch is missing.')
for name in ('input', 'input_buf', 'str_buf'):
    if f'count > sizeof({name}) - 1' not in source:
        raise SystemExit(f'Missing previously validated input-length guard: {name}')
path.write_text(source.replace(old, new, 1))
print('One targeted WMI-argument change applied in the temporary copy.')
PY

make -C "$WORK" -j2 all
MODULE="$WORK/src/linuwu_sense.ko"
[[ -s "$MODULE" && "$(modinfo -F name "$MODULE")" == 'linuwu_sense' ]] || {
    echo 'Experimental module was not built correctly.' >&2; exit 1;
}
[[ "$(modinfo -F vermagic "$MODULE" | cut -d' ' -f1)" == "$EXPECTED_KERNEL" ]] || {
    echo 'Experimental module vermagic mismatch.' >&2; exit 1;
}
[[ "$(sha256sum "$MODULE" | cut -d' ' -f1)" != "$EXPECTED_BASELINE_SHA" ]] || {
    echo 'Experimental module has the original SHA; refusing ambiguous test.' >&2; exit 1;
}

echo
echo '===== BUILT EXPERIMENTAL MODULE; NOTHING INSTALLED OR LOADED ====='
printf 'Original, unchanged: %s\n' "$SOURCE/src/linuwu_sense.ko"
printf 'Experimental module: %s\n' "$MODULE"
sha256sum "$MODULE"
echo 'Run this only while the managed service is stopped:'
printf './scripts/test-rgb-gui-session-fedora.sh %q\n' "$MODULE"
echo 'The four-minute wrapper restores the original lighting and native acer_wmi.'
echo 'Do NOT use install-rgb-driver-fedora.sh or enable the service with this experimental binary.'
