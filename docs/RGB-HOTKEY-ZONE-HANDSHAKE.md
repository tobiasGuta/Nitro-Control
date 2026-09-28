# AN515-58 RGB brightness-down: zone-enable handshake experiment

## Physical evidence

On the user's AN515-58, the stock Fn+F9/F10 shortcuts work until custom four-zone colors are applied under Linuwu. The first Fn+F9 press then changes all four *visible* colors while decreasing brightness, whereas Fn+F10 only increases brightness and does not restore colors. Linuwu's sysfs readback stays unchanged. Reapplying the GUI settings did not fix the physical display. The earlier zone-1-fallback experiment passed a one-press test but failed repeated/preset tests. The stock driver is restored by stopping the temporary session; no boot activation has been enabled.

## Source comparison and testable hypothesis

Linuwu's per-zone writer calls set_kb_status(0,0,brightness,0,0,0,0), then performs four WMI method-6 writes. The [May 2026 AN515-58 kernel RFC](https://www.spinics.net/lists/kernel/msg6183499.html) first calls WMI method 5 with input 0 and WMI method 4 with the zone-enable mask (bit 3 plus bits 40–43), before setting four independent LED zones. Its author reported testing suspend/resume, but also noted unresolved keyboard hotkey release and per-zone brightness interactions. This is an RFC, not a verified hotkey fix.

The build script adds only that two-call *zone-enable handshake* after Linuwu's existing general lighting command and before the four individual zone writes. It starts from the original validated module, **not** the zone-1-fallback experiment. No hotkey interception, timer, GUI polling or automatic privileged writes are added. These firmware WMI calls are experimental.

## Build and test

Close Nitro Control, stop the regular driver service, and from this branch run:

    cd /mnt/Development/Tools/Nitro-Control
    sudo systemctl stop nitro-control-rgb-driver.service
    ./scripts/experiments/build-rgb-zone-handshake-fedora.sh

The build script checks the exact model, kernel and original binary SHA-256, existing input-length guards, and absence of an active Linuwu module. It writes a temporary source copy and prints a command of the form:

    ./scripts/test-rgb-gui-session-fedora.sh /absolute/path/to/experimental/linuwu_sense.ko

Run the **exact command printed by the build**. Apply four distinct colors at 80% brightness and press Fn+F9 once, noting whether all four colors stay intact. Press Fn+F10 once, then if stable try several Fn+F9 presses and a different preset. Close the window to trigger the normal four-minute wrapper's rollback. Do not use the regular install-rgb-driver-fedora.sh installer on this experimental module or enable it at boot.

Verify stock acer_wmi and the original platform profile/fan monitoring after the test. A normal software wrapper cannot recover from a kernel hang, hard crash or power loss. Do not promote this patch based on one press.
