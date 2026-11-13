#!/usr/bin/env bash
# Isolated Fedora per-user GUI install/upgrade/rollback smoke test.
# Operates ONLY in a private temporary HOME; it never installs the live GUI,
# modifies native RGB kernel modules, or issues WMI/fan/power commands.
set -Eeuo pipefail

if [[ ${EUID} -eq 0 ]]; then
  echo "Run as your regular user (not with sudo)." >&2
  exit 1
fi

repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
old_commit="v0.4.0-preview.3"
new_version="Nitro Control 0.4.0-preview.4"
old_version="Nitro Control 0.4.0-preview.3"

if ! command -v git >/dev/null || ! command -v tar >/dev/null; then
  echo "This smoke test requires git and tar." >&2
  exit 1
fi
if ! git -C "$repo_dir" cat-file -e "$old_commit^{commit}" 2>/dev/null; then
  echo "Missing rollback commit $old_commit in this checkout." >&2
  echo "Fetch repository history before running this test." >&2
  exit 1
fi

temp_home_parent="$(mktemp -d "${TMPDIR:-/tmp}/nitro-control-gui-smoke.XXXXXXXX")"
# mktemp yields a new private 0700 directory; it is removed on exit.
trap 'rm -rf -- "$temp_home_parent"' EXIT
temp_home="$temp_home_parent/home"
old_source="$temp_home_parent/old-source"
mkdir -p -- "$temp_home" "$old_source"

echo "== Preparing prior GUI source from reviewed rollback commit =="
git -C "$repo_dir" archive "$old_commit" | tar -xf - -C "$old_source"

launcher="$temp_home/.local/bin/nitro-control"

check_version() {
  local expected="$1"
  local actual
  # Launch from the old source directory to catch stale-cwd import shadowing.
  actual="$(cd "$old_source" && HOME="$temp_home" "$launcher" --version)"
  if [[ "$actual" != "$expected" ]]; then
    echo "Unexpected staged GUI version: $actual (expected $expected)" >&2
    exit 1
  fi
  echo "PASS: $actual"
}

echo "== 1. Install previous GUI into isolated HOME =="
HOME="$temp_home" bash "$old_source/scripts/install-fedora.sh"
check_version "$old_version"

echo "== 2. Upgrade isolated HOME to release candidate =="
HOME="$temp_home" bash "$repo_dir/scripts/install-fedora.sh"
check_version "$new_version"

echo "== 3. Roll back isolated HOME to previous GUI =="
HOME="$temp_home" bash "$old_source/scripts/install-fedora.sh"
check_version "$old_version"

echo "== 4. Reinstall release candidate in isolated HOME =="
HOME="$temp_home" bash "$repo_dir/scripts/install-fedora.sh"
check_version "$new_version"

echo "PASS: staged install, upgrade, rollback, re-upgrade, and launcher import isolation."
echo "No live GUI, root-owned helper, driver, RGB state, fan, or power setting changed."
