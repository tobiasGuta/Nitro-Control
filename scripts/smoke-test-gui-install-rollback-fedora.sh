#!/usr/bin/env bash
# Isolated Fedora per-user GUI install/upgrade/rollback smoke test.
# Operates ONLY in a private temporary HOME; it never installs the live GUI,
# modifies native RGB kernel modules, or issues WMI/fan/power commands.
set -Eeuo pipefail

repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
case "$#:${1-}" in
  0:) ;;
  1:-h|1:--help)
    echo "Usage: smoke-test-gui-install-rollback-fedora.sh [--help|--version]"
    echo "Exercise upgrade and rollback using a temporary home."
    exit 0 ;;
  1:--version) exec bash "$repo_dir/scripts/install-fedora.sh" --version ;;
  *) echo "Unsupported smoke-test argument. Use --help." >&2; exit 2 ;;
esac

if [[ ${EUID} -eq 0 ]]; then
  echo "Run as your regular user (not with sudo)." >&2
  exit 1
fi

old_commit="v0.4.0-preview.4"
new_version="$(bash "$repo_dir/scripts/install-fedora.sh" --version)"
old_version="Nitro Control 0.4.0-preview.4"

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
export XDG_CONFIG_HOME="$temp_home/.config"
export XDG_CACHE_HOME="$temp_home/.cache"
export XDG_DATA_HOME="$temp_home/.local/share"

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
HOME="$temp_home" bash "$repo_dir/scripts/install-fedora.sh" --source "$old_source"
check_version "$old_version"

echo "== 2. Upgrade isolated HOME to release candidate =="
HOME="$temp_home" bash "$repo_dir/scripts/install-fedora.sh"
check_version "$new_version"

echo "== 3. Roll back isolated HOME to previous GUI =="
HOME="$temp_home" bash "$repo_dir/scripts/install-fedora.sh" --source "$old_source"
check_version "$old_version"

echo "== 4. Reinstall release candidate in isolated HOME =="
HOME="$temp_home" bash "$repo_dir/scripts/install-fedora.sh"
check_version "$new_version"

echo "PASS: staged install, upgrade, rollback, re-upgrade, and launcher import isolation."
echo "No live GUI, root-owned helper, driver, RGB state, fan, or power setting changed."
