# RGB hotkey experiment (AN515-58)

Reproduced: Fn+F9/F10 work with the stock driver and before a custom per-zone
color is applied. After a physical Apply, the brightness keys visibly alter
colors, but the Linuwu sysfs readback still reports the prior colors/brightness.
Reapplying the same palette does not restore the physical colors.

## Single-variable hypothesis

In the tested Div-Linuwu-Sense source, set_per_zone_color() first calls
set_kb_status(0, 0, brightness, 0, 0, 0, 0), then writes all four colors.
This experiment replaces only the three zero general RGB arguments with
zone 1's RGB values, retaining the mode, brightness and four zone writes.
If colors change to zone 1 after a Fn key, that supports the fallback-state
hypothesis. If they remain random, this hypothesis is weakened. This is
not a fix, and neither result guarantees hardware safety.

## Opt-in, build-only experiment

Run as your desktop user (not sudo) from Nitro Control:

    cd /mnt/Development/Tools/Nitro-Control
    sudo systemctl stop nitro-control-rgb-driver.service
    ./scripts/experiments/build-rgb-base-color-fedora.sh

The script requires the exact already-patched source and baseline module SHA
from the tested 7.2.7 Fedora kernel. It writes only to a new copied build under
~/.cache/nitro-control, runs make all (never make install), and prints an
experimental .ko path. Pass the *printed* path to the existing temporary
four-minute GUI test wrapper, which restores the original lighting and
stock acer_wmi on ordinary exit:

    ./scripts/test-rgb-gui-session-fedora.sh /FULL/PRINTED/PATH/linuwu_sense.ko

Apply four distinct colors, press Fn+F9 once, observe the colors, then Fn+F10
once. Stop if other laptop behavior becomes abnormal. Verify the final module:

    lsmod | grep -E '^(acer_wmi|linuwu_sense)'
    cat /sys/firmware/acpi/platform_profile
    sensors | sed -n '/acer-isa-0000/,/^$/p'

Do not install the experimental binary into the pinned driver path or enable
the service at boot. A kernel crash or hard power loss is not recoverable by
the wrapper's ordinary cleanup trap.
