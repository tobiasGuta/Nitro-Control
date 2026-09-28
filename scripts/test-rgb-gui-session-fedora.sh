#!/usr/bin/env bash
# Temporary AN515-58 GUI integration test; never installs or blacklists a kernel module.
# Runs as the desktop user. Elevated operations are restricted to module swap/restore.
set -Eeuo pipefail

MODEL='Nitro AN515-58'
MODULE="${1:-/mnt/Development/Tools/Div-Linuwu-Sense-build-test/src/linuwu_sense.ko}"
APP="$HOME/.local/bin/nitro-control"
RGB='/sys/devices/platform/acer-wmi/four_zoned_kb/per_zone_mode'
HELPER='/usr/local/libexec/nitro-control-rgb-helper'
SESSION_SECONDS=240
original=''

cleanup() {
    local rc=$?
    trap - EXIT HUP INT TERM
    set +e
    echo
    echo '===== RESTORE ORIGINAL KEYBOARD LIGHTING ====='
    if [[ -n "$original" ]]; then
        if [[ -e "$RGB" ]]; then
            if printf '%s\n' "$original" | sudo tee "$RGB" >/dev/null; then
                local actual
                actual="$(cat "$RGB" 2>/dev/null)"
                if [[ "$actual" == "$original" ]]; then
                    echo "RGB values restored: $actual"
                else
                    echo "WARNING: RGB readback does not match the saved value: $actual" >&2
                    rc=1
                fi
            else
                echo 'WARNING: Could not restore original lighting.' >&2
                rc=1
            fi
        else
            echo 'WARNING: RGB endpoint is missing; lighting restoration unverified.' >&2
            rc=1
        fi
    fi

    echo '===== RESTORE NATIVE ACER DRIVER ====='
    if grep -q '^linuwu_sense ' /proc/modules; then
        if ! sudo rmmod linuwu_sense; then
            echo 'WARNING: Could not unload experimental driver.' >&2
            rc=1
        fi
    fi
    if ! grep -q '^linuwu_sense ' /proc/modules; then
        if ! sudo modprobe acer_wmi; then
            echo 'WARNING: Could not reload native acer_wmi.' >&2
            rc=1
        fi
    else
        echo 'WARNING: Experimental driver is still loaded.' >&2
        rc=1
    fi
    grep -E '^(acer_wmi|linuwu_sense) ' /proc/modules || true
    if ! grep -q '^acer_wmi ' /proc/modules; then
        echo 'WARNING: Native acer_wmi is not loaded. Restore manually or reboot.' >&2
        rc=1
    fi
    echo '===== FINAL FIRMWARE PROFILE ====='
    cat /sys/firmware/acpi/platform_profile 2>/dev/null || true
    echo '===== FINAL FAN READINGS ====='
    if command -v sensors >/dev/null 2>&1; then
        sensors | sed -n '/acer-isa-0000/,/^$/p'
    fi
    exit "$rc"
}
echo '===== PREFLIGHT ====='
[[ "$(cat /sys/class/dmi/id/product_name)" == "$MODEL" ]] || { echo 'Unsupported laptop model.' >&2; exit 1; }
[[ -f "$MODULE" && -x "$APP" && -x "$HELPER" ]] || { echo 'Missing compiled driver, installed Nitro Control or RGB helper.' >&2; exit 1; }
[[ "$(modinfo -F name "$MODULE")" == 'linuwu_sense' ]] || { echo 'Unexpected module name.' >&2; exit 1; }
[[ "$(modinfo -F vermagic "$MODULE" | cut -d' ' -f1)" == "$(uname -r)" ]] || { echo 'Driver does not match the running kernel.' >&2; exit 1; }
grep -q '^acer_wmi ' /proc/modules || { echo 'Native acer_wmi must be loaded to start.' >&2; exit 1; }
if grep -q '^linuwu_sense ' /proc/modules; then
    echo 'Experimental driver already loaded; refusing an overlapping session.' >&2
    exit 1
fi
command -v pkexec >/dev/null || { echo 'pkexec is not installed.' >&2; exit 1; }
command -v timeout >/dev/null || { echo 'GNU timeout is required.' >&2; exit 1; }
command -v flock >/dev/null || { echo 'flock is required.' >&2; exit 1; }
mkdir -p "$HOME/.cache"
exec 9>"$HOME/.cache/nitro-control-rgb-session.lock"
flock -n 9 || { echo 'Another RGB test session is running.' >&2; exit 1; }
echo "Driver: $MODULE"
echo "Kernel: $(uname -r)"
echo "Session maximum: $SESSION_SECONDS seconds"
echo 'Close any other Nitro Control windows before continuing.'
echo 'The app must stay open in this terminal; closing it ends the test.'
sudo -v
# Arm rollback only after preflight, before the first driver-changing command.
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

echo '===== LOAD TEMPORARY DRIVER ====='
sudo rmmod acer_wmi
sudo insmod "$MODULE"
[[ -r "$RGB" ]] || { echo 'RGB endpoint is not readable.' >&2; exit 1; }
original="$(cat "$RGB")"
[[ "$original" =~ ^[[:xdigit:]]{6}(,[[:xdigit:]]{6}){3},(100|[1-9]?[0-9])$ ]] || { echo 'Unexpected saved RGB format.' >&2; exit 1; }
echo "Saved original RGB: $original"

probe="$($APP --rgb-probe)"
printf '%s\n' "$probe"
printf '%s\n' "$probe" | /usr/bin/python3 -c 'import json,sys; x=json.load(sys.stdin); sys.exit(0 if x.get("available") is True and x.get("backend")=="linuwu-sense" else 1)' || {
    echo 'Nitro Control did not identify the expected Linuwu backend.' >&2
    exit 1
}

echo '===== LAUNCH NITRO CONTROL AS YOUR NORMAL USER ====='
echo 'Choose colors at 25% brightness, click Apply to keyboard, and authorize.'
echo "Close the window when done; it also closes automatically after $SESSION_SECONDS seconds."
if timeout --foreground --signal=TERM --kill-after=10s "${SESSION_SECONDS}s" "$APP"; then
    echo 'Nitro Control window closed.'
else
    result=$?
    if [[ "$result" == 124 ]]; then
        echo 'Time limit reached; restoring the native driver.'
    else
        echo "Nitro Control exited with status $result; restoring the native driver." >&2
    fi
    exit "$result"
fi
