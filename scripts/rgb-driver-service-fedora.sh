#!/usr/bin/bash
# Root-owned runtime controller for a pinned, explicitly installed AN515-58 module.
# No network, build, blacklist, fan write, profile write, or automatic kernel migration.
set -Eeuo pipefail

ACTION="${1:-}"
MODEL='Nitro AN515-58'
GUID='7A4DDFE7-5B5D-40B4-8595-4408E0CC7F56'
KERNEL="$(uname -r)"
MODULE="/usr/lib/modules/$KERNEL/extra/nitro-control/linuwu_sense.ko"
DIGEST="$MODULE.sha256"
RGB='/sys/devices/platform/acer-wmi/four_zoned_kb/per_zone_mode'
STATE_DIR='/run/nitro-control-rgb-driver'
STATE="$STATE_DIR/original-rgb"

loaded() { grep -q "^$1 " /proc/modules; }
valid_rgb() { [[ "$1" =~ ^[[:xdigit:]]{6}(,[[:xdigit:]]{6}){3},(100|[1-9]?[0-9])$ ]]; }

preflight() {
    [[ "$(cat /sys/class/dmi/id/product_name 2>/dev/null)" == "$MODEL" ]] || { echo 'Wrong model; stock driver left untouched.' >&2; return 1; }
    local device found=0
    for device in /sys/bus/wmi/devices/"$GUID"-*; do
        if [[ -e "$device" ]]; then found=1; break; fi
    done
    (( found == 1 )) || { echo 'Expected Acer RGB WMI interface is missing.' >&2; return 1; }
    [[ -f "$MODULE" && ! -L "$MODULE" && -f "$DIGEST" && ! -L "$DIGEST" ]] || { echo "No pinned RGB module for $KERNEL; stock driver left untouched." >&2; return 1; }
    [[ "$(stat -c '%u:%a' "$MODULE")" == '0:644' && "$(stat -c '%u:%a' "$DIGEST")" == '0:600' ]] || { echo 'Module ownership/mode mismatch.' >&2; return 1; }
    [[ "$(modinfo -F name "$MODULE")" == 'linuwu_sense' ]] || { echo 'Unexpected module identity.' >&2; return 1; }
    [[ "$(modinfo -F vermagic "$MODULE" | cut -d' ' -f1)" == "$KERNEL" ]] || { echo 'Module does not match running kernel.' >&2; return 1; }
    [[ "$(cat "$DIGEST")" == "$(sha256sum "$MODULE" | cut -d' ' -f1)" ]] || { echo 'Module digest mismatch.' >&2; return 1; }
    if command -v mokutil >/dev/null 2>&1 && mokutil --sb-state 2>/dev/null | grep -qi 'SecureBoot enabled'; then
        echo 'Secure Boot enabled; unsigned development module is not supported.' >&2
        return 1
    fi
    ! loaded linuwu_sense || { echo 'Linuwu already loaded outside this service; refusing takeover.' >&2; return 1; }
}

restore() {
    local rc=0 actual saved
    echo 'Restoring saved keyboard lighting and stock acer_wmi...'
    if loaded linuwu_sense; then
        if [[ -f "$STATE" && -r "$RGB" && -w "$RGB" ]]; then
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
    [[ -d "$STATE_DIR" ]] && rm -f "$STATE"
    loaded acer_wmi || { echo 'WARNING: Stock Acer module not loaded.' >&2; return 1; }
    echo 'Stock acer_wmi loaded.'
    return "$rc"
}

[[ $EUID -eq 0 ]] || { echo 'Controller must be started by systemd as root.' >&2; exit 1; }
[[ $# -eq 1 ]] || { echo 'Usage: controller {check|start|stop}' >&2; exit 2; }
case "$ACTION" in
    check) preflight ;;
    start)
        preflight
        # If udev has not loaded the stock module yet, load it first.
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
        ;;
    stop)
        restore
        ;;
    *) echo 'Usage: controller {check|start|stop}' >&2; exit 2 ;;
esac
