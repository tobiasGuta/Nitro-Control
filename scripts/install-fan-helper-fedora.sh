#!/usr/bin/env bash
# Optional root-owned helper for the stock Acer hwmon interface.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
if [[ ${EUID} -ne 0 || $# -ne 0 ]]; then
  echo 'Usage: sudo ./scripts/install-fan-helper-fedora.sh' >&2
  exit 1
fi

install -d -o root -g root -m 755 /usr/local/libexec/nitro-control-fan/nitro_control
install -o root -g root -m 644 nitro_control/{__init__,fan_control}.py /usr/local/libexec/nitro-control-fan/nitro_control/
install -o root -g root -m 755 /dev/stdin /usr/local/libexec/nitro-control-fan-helper <<'PYTHON'
#!/usr/bin/python3 -I
import sys
sys.path.insert(0, "/usr/local/libexec/nitro-control-fan")
from nitro_control.fan_control import main
raise SystemExit(main())
PYTHON
install -o root -g root -m 644 /dev/stdin /usr/share/polkit-1/actions/io.github.tobiasguta.NitroControl.fan.policy <<'XML'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE policyconfig PUBLIC "-//freedesktop//DTD PolicyKit Policy Configuration 1.0//EN" "http://www.freedesktop.org/standards/PolicyKit/1.0/policyconfig.dtd">
<policyconfig>
  <action id="io.github.tobiasguta.NitroControl.fan">
    <description>Apply Acer Nitro fan settings</description>
    <message>Authentication is required to change your laptop fan settings.</message>
    <defaults>
      <allow_any>no</allow_any>
      <allow_inactive>no</allow_inactive>
      <allow_active>auth_admin</allow_active>
    </defaults>
    <annotate key="org.freedesktop.policykit.exec.path">/usr/local/libexec/nitro-control-fan-helper</annotate>
  </action>
</policyconfig>
XML
echo 'Installed the optional fan helper. Fan settings are applied from the Dashboard.'
