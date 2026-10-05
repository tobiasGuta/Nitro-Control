# AN515-58 RGB-enable proof of concept

This experiment is intentionally isolated from Nitro Control's normal runtime.

It tests one narrow hypothesis discovered on the reference Acer Nitro AN515-58:

- stock `acer_wmi` with HWMON enabled exposes Acer fan telemetry but the keyboard
  backlight turns off during Linux startup;
- `acer_wmi.force_caps=1024` keeps the keyboard working but removes Acer HWMON;
- the May 2026 upstream RFC for AN515-58 RGB support explicitly polls the Acer
  gaming WMI interface and then enables all four keyboard zones after HWMON init.

The module in this directory reproduces only that two-call enable sequence.
It does **not** replace `acer_wmi`, expose RGB sysfs devices, install itself,
autoload, modify GRUB, or touch fan controls.

## Safety properties

- exact DMI gate: `Acer` + `Nitro AN515-58`;
- exact Acer gaming WMI GUID gate;
- default insertion is a dry run and performs no WMI method calls;
- no MODULE_DEVICE_TABLE / modalias, service, install target, or boot persistence;
- unloading does not send another firmware command.

A firmware/WMI call can still behave unexpectedly. Test only on the reference
AN515-58 and keep the normal stock `acer_wmi` configuration.

## Build

```bash
cd experiments/acer-wmi-rgb-enable-poc
make
modinfo ./nitro_rgb_enable_poc.ko
```

## Preconditions

Before the live test, verify stock `acer_wmi` and Acer HWMON are active:

```bash
uname -r
lsmod | grep '^acer_wmi'
cat /proc/cmdline
for d in /sys/class/hwmon/hwmon*; do
    [ "$(cat "$d/name" 2>/dev/null)" = "acer" ] && echo "$d : acer"
done
```

Do not run this while the Linuwu replacement driver/service is active.

## Dry-run insertion

```bash
sudo insmod ./nitro_rgb_enable_poc.ko
sudo journalctl -k -b --no-pager | tail -n 30
sudo rmmod nitro_rgb_enable_poc
```

Expected log:

```text
nitro_rgb_enable_poc: dry run only; model and WMI GUID validated
```

## Live one-shot test

Only after the dry run succeeds:

```bash
sudo insmod ./nitro_rgb_enable_poc.ko enable=1
sudo journalctl -k -b --no-pager | tail -n 40
```

Immediately inspect the physical keyboard. Then confirm Acer fan telemetry and
the platform profile still exist:

```bash
echo "=== ACER HWMON ==="
for d in /sys/class/hwmon/hwmon*; do
    name="$(cat "$d/name" 2>/dev/null)"
    if [ "$name" = "acer" ]; then
        echo "$d : $name"
        find "$d" -maxdepth 1 -type f \
          \( -name 'fan*_input' -o -name 'temp*_input' -o -name 'pwm*' \) \
          -print -exec cat {} \; 2>/dev/null
    fi
done

echo
echo "=== PLATFORM PROFILE ==="
cat /sys/firmware/acpi/platform_profile 2>&1
```

Finally unload the proof-of-concept:

```bash
sudo rmmod nitro_rgb_enable_poc
```

The firmware state may remain until another firmware/driver action or reboot.
That is expected; the module deliberately does not issue a restore command.

## What success means

If the keyboard turns back on while Acer HWMON and platform profiles remain
available, the project has a strong basis for replacing the current
`acer_wmi`/Linuwu handoff with a native AN515-58 RGB path.

This experiment does **not** yet solve Fn+F9/Fn+F10 brightness handling or
register the four multicolor LED zones.
