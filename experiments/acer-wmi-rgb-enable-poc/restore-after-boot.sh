#!/usr/bin/env bash
# Manual, fail-closed AN515-58 RGB recovery after stock acer_wmi starts.
# Does not install anything, edit boot arguments, or replace a driver.
set -Eeuo pipefail

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

[[ "$EUID" -eq 0 ]] || die "Run as root: sudo bash ./restore-after-boot.sh"
[[ "$#" -eq 0 ]] || die "No arguments accepted."
[[ "$(cat /sys/class/dmi/id/sys_vendor 2>/dev/null)" == "Acer" ]] ||
    die "Refusing non-Acer hardware."
[[ "$(cat /sys/class/dmi/id/product_name 2>/dev/null)" == "Nitro AN515-58" ]] ||
    die "Refusing non-AN515-58 hardware."

[[ -d /sys/module/acer_wmi ]] || die "Stock acer_wmi is not loaded."
if grep -Eq '(^|[[:space:]])acer_wmi.force_caps=' /proc/cmdline; then
    die "Non-default acer_wmi.force_caps detected; do not alter the proven setup."
fi
if grep -q '^linuwu_sense ' /proc/modules; then
    die "Linuwu driver is loaded; refusing to mix drivers."
fi
if systemctl is-active --quiet nitro-control-rgb-driver.service 2>/dev/null; then
    die "Old Nitro-Control RGB replacement-driver service is active."
fi
if grep -q '^nitro_rgb_enable_poc ' /proc/modules; then
    die "The experimental module is already loaded; unload it before retrying."
fi

have_acer_hwmon=0
for hwmon in /sys/class/hwmon/hwmon*; do
    if [[ "$(cat "$hwmon/name" 2>/dev/null)" == "acer" &&
          -r "$hwmon/fan1_input" && -r "$hwmon/fan2_input" ]]; then
        have_acer_hwmon=1
        break
    fi
done
(( have_acer_hwmon == 1 )) || die "Acer fan telemetry is unavailable."

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
module="$script_dir/nitro_rgb_enable_poc.ko"
[[ -f "$module" && ! -L "$module" ]] ||
    die "No regular local PoC module: $module. Run make for this kernel."
[[ "$(modinfo -F name "$module")" == "nitro_rgb_enable_poc" ]] ||
    die "Unexpected module name."
[[ "$(modinfo -F vermagic "$module" | cut -d' ' -f1)" == "$(uname -r)" ]] ||
    die "Module is not compiled for running kernel $(uname -r); rebuild it."
if command -v mokutil >/dev/null 2>&1 &&
   mokutil --sb-state 2>/dev/null | grep -qi 'SecureBoot enabled'; then
    die "Secure Boot is enabled; refusing unverified development module."
fi

# The successful module restores red static illumination and brightness 100.
# It must not remain loaded after the single firmware transaction.
cleanup() {
    local code=$?
    trap - EXIT
    if grep -q '^nitro_rgb_enable_poc ' /proc/modules; then
        rmmod nitro_rgb_enable_poc || {
            printf 'ERROR: failed to unload experimental module.\n' >&2
            code=1
        }
    fi
    exit "$code"
}
trap cleanup EXIT

printf 'Running one-shot Acer RGB recovery on %s; all four zones will be set red.\n' "$(uname -r)"
insmod "$module" full_test=1
printf 'Firmware test returned; checking fan telemetry...\n'
[[ -r "$hwmon/fan1_input" && -r "$hwmon/fan2_input" ]] ||
    die "Acer fan telemetry disappeared following the RGB operation."
printf 'fan1=%s RPM; fan2=%s RPM\n' "$(cat "$hwmon/fan1_input")" "$(cat "$hwmon/fan2_input")"
[[ -r /sys/firmware/acpi/platform_profile ]] ||
    die "Platform profile sysfs interface is unavailable."
printf 'platform_profile=%s\n' "$(cat /sys/firmware/acpi/platform_profile)"
printf 'Check the physical keyboard and Fn+F9/Fn+F10 yourself.\n'
printf 'This helper does NOT arrange automatic startup or resume restoration.\n'
