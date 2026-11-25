#!/usr/bin/bash
# Managed by Nitro Control; experimental AN515-58 RGB driver controller.
# Root-owned runtime controller for a pinned, explicitly installed AN515-58 module.
# No network, build, blacklist, profile write, or automatic kernel migration.
set -Eeuo pipefail

MODEL='Nitro AN515-58'
GUID='7A4DDFE7-5B5D-40B4-8595-4408E0CC7F56'
KERNEL="$(uname -r)"
MODULE="/usr/local/lib/nitro-control/modules/$KERNEL/linuwu_sense.ko"
DIGEST="$MODULE.sha256"
RGB='/sys/devices/platform/acer-wmi/four_zoned_kb/per_zone_mode'
STATE_DIR='/run/nitro-control-rgb-driver'
STATE="$STATE_DIR/original-rgb"

SELF_PATH="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/$(basename -- "${BASH_SOURCE[0]}")"
if [[ "$SELF_PATH" == '/usr/local/libexec/nitro-control-rgb-driver' ]]; then
    LIB_ROOT='/usr/local/libexec/nitro-control-rgb-driver-lib'
else
    LIB_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
fi

loaded() { [[ -r /proc/modules ]] && grep -q "^$1 " /proc/modules; }
valid_rgb() { [[ "$1" =~ ^[[:xdigit:]]{6}(,[[:xdigit:]]{6}){3},(100|[1-9]?[0-9])$ ]]; }

run_staging_cli() {
    /usr/bin/python3 -I -c '
import sys
sys.path.insert(0, sys.argv[1])
from nitro_control.module_staging import _cli_main
raise SystemExit(_cli_main(sys.argv[2:]))
' "$LIB_ROOT" "$@"
}

preflight() {
    [[ "$(cat /sys/class/dmi/id/product_name 2>/dev/null || true)" == "$MODEL" ]] || { echo 'Wrong model; stock driver left untouched.' >&2; return 1; }
    local device found=0
    for device in /sys/bus/wmi/devices/"$GUID"-*; do
        if [[ -e "$device" ]]; then found=1; break; fi
    done
    (( found == 1 )) || { echo 'Expected Acer RGB WMI interface is missing.' >&2; return 1; }
    run_staging_cli verify-runtime --component rgb-driver --kernel "$KERNEL" || return 1
    # verify-runtime already validates digest/sha256sum, modinfo -F vermagic, and manifest.
    if command -v mokutil >/dev/null 2>&1 && mokutil --sb-state 2>/dev/null | grep -qi 'SecureBoot enabled'; then
        echo 'Secure Boot enabled; unsigned development module is not supported.' >&2
        return 1
    fi
    ! loaded linuwu_sense || { echo 'Linuwu already loaded outside this service; refusing takeover.' >&2; return 1; }
    ! loaded nitro_rgb_enable_poc && [[ ! -d /sys/module/nitro_rgb_enable_poc ]] || {
        echo 'Native RGB test module is active; refusing takeover.' >&2; return 1;
    }
}

restore() {
    local rc=0 actual saved
    echo 'Restoring saved keyboard lighting and stock acer_wmi...'
    if loaded linuwu_sense; then
        if [[ -f "$STATE" && ! -L "$STATE" && -r "$RGB" && -w "$RGB" ]]; then
            saved="$(cat "$STATE")"
            if valid_rgb "$saved"; then
                if ! printf '%s\n' "$saved" > "$RGB"; then
                    echo 'WARNING: Lighting restoration write failed.' >&2; rc=1
                else
                    actual="$(cat "$RGB" 2>/dev/null || true)"
                    [[ "$actual" == "$saved" ]] || { echo 'WARNING: Lighting readback differs.' >&2; rc=1; }
                fi
            else
                echo 'WARNING: Saved RGB state invalid.' >&2; rc=1
            fi
        else
            echo 'WARNING: Original RGB unavailable; lighting restoration unverified.' >&2; rc=1
        fi
        if ! rmmod linuwu_sense; then
            echo 'WARNING: Could not unload Linuwu; do not force-load acer_wmi.' >&2
            return 1
        fi
    fi
    if ! loaded acer_wmi; then
        modprobe acer_wmi || { echo 'WARNING: Unable to restore acer_wmi. Reboot using stock configuration.' >&2; return 1; }
    fi
    [[ -d "$STATE_DIR" ]] && rm -f -- "$STATE"
    loaded acer_wmi || { echo 'WARNING: Stock Acer module not loaded.' >&2; return 1; }
    echo 'Stock acer_wmi loaded.'
    return "$rc"
}

locked_start() {
    preflight
    if ! loaded acer_wmi; then modprobe acer_wmi; fi
    [[ ! -e "$STATE" ]] || { echo 'Stale RGB state exists; investigate before starting.' >&2; exit 1; }
    changed=0
    recover_failure() {
        rc=$?
        trap - EXIT HUP INT TERM
        if [[ "$changed" == 1 ]]; then restore || true; fi
        exit "$rc"
    }
    trap recover_failure EXIT
    trap 'exit 129' HUP
    trap 'exit 130' INT
    trap 'exit 143' TERM
    rmmod acer_wmi
    changed=1
    insmod "$MODULE"
    [[ -r "$RGB" && -w "$RGB" ]] || { echo 'Driver did not expose expected RGB endpoint.' >&2; exit 1; }
    saved="$(cat "$RGB")"
    valid_rgb "$saved" || { echo 'Driver returned unexpected RGB data.' >&2; exit 1; }
    [[ -r /sys/firmware/acpi/platform_profile ]] || { echo 'Firmware profile interface missing.' >&2; exit 1; }
    install -d -o root -g root -m 700 "$STATE_DIR"
    printf '%s\n' "$saved" > "$STATE"
    chmod 600 "$STATE"
    echo "Linuwu RGB active on $KERNEL; original lighting recorded."
    changed=0
    trap - EXIT HUP INT TERM
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    ACTION="${1:-}"
    [[ $EUID -eq 0 ]] || { echo 'Controller must be started by systemd as root.' >&2; exit 1; }
    [[ $# -eq 1 ]] || { echo 'Usage: controller {check|start|stop}' >&2; exit 2; }
    case "$ACTION" in
        check) preflight ;;
        start)
            exec /usr/bin/python3 -I -c '
import sys
sys.path.insert(0, sys.argv[1])
from nitro_control.module_staging import _cli_main
raise SystemExit(_cli_main(sys.argv[2:]))
' "$LIB_ROOT" lock-and-exec --lock rgb-driver.lock --lock native-rgb.lock \
              --busy-message 'Another RGB driver or native RGB operation is active.' \
              -- /usr/bin/bash -c 'source "$1" && locked_start' nitro-control-rgb-driver "$SELF_PATH"
            ;;
        stop)
            exec /usr/bin/python3 -I -c '
import sys
sys.path.insert(0, sys.argv[1])
from nitro_control.module_staging import _cli_main
raise SystemExit(_cli_main(sys.argv[2:]))
' "$LIB_ROOT" lock-and-exec --lock rgb-driver.lock --lock native-rgb.lock \
              --busy-message 'Another RGB driver or native RGB operation is active.' \
              -- /usr/bin/bash -c 'source "$1" && restore' nitro-control-rgb-driver "$SELF_PATH"
            ;;
        *) echo 'Usage: controller {check|start|stop}' >&2; exit 2 ;;
    esac
fi
