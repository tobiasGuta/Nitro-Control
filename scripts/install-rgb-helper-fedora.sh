#!/usr/bin/env bash
# Optional: installs root-owned one-shot RGB helper and a specific Polkit action.
# NEVER installs, replaces, loads, blacklists or removes any kernel module.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
if [[ ${EUID} -ne 0 ]]; then
  echo 'Run explicitly as: sudo ./scripts/install-rgb-helper-fedora.sh' >&2
  exit 1
fi
install -d -m 755 /usr/local/libexec/nitro-control-rgb/nitro_control
for file in __init__.py rgb.py rgb_hardware.py rgb_privileged.py; do
  install -o root -g root -m 644 "nitro_control/$file" "/usr/local/libexec/nitro-control-rgb/nitro_control/$file"
done
cat > /usr/local/libexec/nitro-control-rgb-helper <<'PY'
#!/usr/bin/python3 -I
import sys
sys.path.insert(0, "/usr/local/libexec/nitro-control-rgb")
from nitro_control.rgb_privileged import main
raise SystemExit(main())
PY
chown root:root /usr/local/libexec/nitro-control-rgb-helper
chmod 755 /usr/local/libexec/nitro-control-rgb-helper
install -d -m 755 /usr/share/polkit-1/actions
cat > /usr/share/polkit-1/actions/io.github.tobiasguta.NitroControl.rgb.policy <<'XML'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE policyconfig PUBLIC "-//freedesktop//DTD PolicyKit Policy Configuration 1.0//EN" "http://www.freedesktop.org/standards/PolicyKit/1.0/policyconfig.dtd">
<policyconfig>
  <action id="io.github.tobiasguta.NitroControl.rgb">
    <description>Apply Acer Nitro keyboard RGB lighting</description>
    <message>Authentication is required to change your keyboard RGB lighting.</message>
    <defaults>
      <allow_any>no</allow_any>
      <allow_inactive>no</allow_inactive>
      <allow_active>auth_admin</allow_active>
    </defaults>
    <annotate key="org.freedesktop.policykit.exec.path">/usr/local/libexec/nitro-control-rgb-helper</annotate>
  </action>
</policyconfig>
XML
chown root:root /usr/share/polkit-1/actions/io.github.tobiasguta.NitroControl.rgb.policy
chmod 644 /usr/share/polkit-1/actions/io.github.tobiasguta.NitroControl.rgb.policy
echo 'Installed optional root-owned RGB helper. No kernel driver changes made.'
