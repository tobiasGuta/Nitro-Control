"""Shared root-owned kernel module staging, provenance, locking, and migration."""
from __future__ import annotations

import argparse
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import hashlib
import importlib.util
import json
import marshal
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys


class ModuleStagingError(RuntimeError):
    """Raised when staging validation, locking, provenance, or migration fails."""


@dataclass(frozen=True, slots=True)
class ComponentSpec:
    component: str
    module_name: str
    module_file: str
    lock_name: str


COMPONENT_SPECS: dict[str, ComponentSpec] = {
    "native-rgb": ComponentSpec(
        component="native-rgb",
        module_name="nitro_rgb_enable_poc",
        module_file="nitro_rgb_enable_poc.ko",
        lock_name="native-rgb.lock",
    ),
    "rgb-driver": ComponentSpec(
        component="rgb-driver",
        module_name="linuwu_sense",
        module_file="linuwu_sense.ko",
        lock_name="rgb-driver.lock",
    ),
}

MANAGED_BY = "nitro-control"
SCHEMA_VERSION = 1
PRIVATE_MODULES_REL = ("usr", "local", "lib", "nitro-control", "modules")
LEGACY_SUBDIR_REL = ("extra", "nitro-control")
LOCK_SUBDIR = "nitro-control"
CONTROLLER_MARKER = "# Managed by Nitro Control;"
SERVICE_MARKER = "# Installed explicitly by Nitro Control;"
DRIVER_LIB_MANIFEST = "driver-lib.manifest.json"
RGB_HELPER_MANIFEST = "manifest.json"
RGB_HELPER_TX_FILE = ".tx.rgb-helper.json"
RGB_HELPER_LAUNCHER_MARKER = "# Managed RGB helper by Nitro Control;"
RGB_HELPER_LEGACY_SIGNATURES = (
    "/usr/local/libexec/nitro-control-rgb",
    "from nitro_control.rgb_privileged import main",
)
RGB_HELPER_POLICY_MARKER = '<action id="io.github.tobiasguta.NitroControl.rgb">'
RGB_HELPER_EXEC_MARKER = "/usr/local/libexec/nitro-control-rgb-helper"
RGB_HELPER_FILES = (
    "__init__.py",
    "rgb.py",
    "rgb_hardware.py",
    "module_staging.py",
    "native_rgb.py",
    "rgb_privileged.py",
)
_RGB_HELPER_PY_STEMS = frozenset(fname.rsplit(".", 1)[0] for fname in RGB_HELPER_FILES)
_RGB_HELPER_EXPECTED_KEYS = frozenset({
    "nitro-control-rgb-helper",
    "io.github.tobiasguta.NitroControl.rgb.policy",
    *(f"nitro_control/{fname}" for fname in RGB_HELPER_FILES),
})
_RGB_HELPER_PY_MARKERS: dict[str, tuple[bytes, ...]] = {
    "__init__.py": (b"Nitro Control",),
    "rgb.py": (b"LightingPlan",),
    "rgb_hardware.py": (b"RGBHardware",),
    "module_staging.py": (b"ModuleStagingError", b"module_staging"),
    "native_rgb.py": (b"NativeRGB",),
    "rgb_privileged.py": (b"RGBHardware",),
}
RGB_HELPER_LOCK = "rgb-helper.lock"
RGB_HELPER_PROTOCOL = 2
RGB_HELPER_CAPABILITIES = ("static", "effect")
METADATA_INSPECT_TIMEOUT = 2
WMI_ALIAS = "wmi:676AA15E-6A47-4D9F-A2CC-1E6D18D14026"


@dataclass(frozen=True, slots=True)
class NativeParamInspection:
    status: str  # "compatible", "incompatible", "missing_tool", "failed", "timeout"
    exposed: frozenset[str]
    forbidden: tuple[str, ...]
    detail: str
_DISABLED_STATES = frozenset({"disabled", "masked", "masked-runtime"})
_MIGRATION_STATES = frozenset({"complete", "unverified_clearance", "pending_clearance"})
_INDEX_STATUSES = frozenset({"verified_clean", "stale_or_failed"})
_INITRAMFS_STATUSES = frozenset({"not_present", "verified_clean", "exposed", "unverified"})
_MIGRATION_KERNEL_STATUSES = frozenset({"complete", "pending"})
DISALLOWED_NATIVE_EXPERIMENTAL_PARAMS = frozenset({"backlight_test", "red_test", "enable", "effect_test"})
_HEX_SHA = re.compile(r"[a-f0-9]{64}\Z")
_COMMIT_STAGES = (
    "after_tx_prepare",
    "after_manifest_unlink",
    "after_ko_replace",
    "after_sha_replace",
    "after_manifest_commit",
)

Runner = Callable[..., subprocess.CompletedProcess[str]]
StageHook = Callable[[str], None]


def validate_kernel_name(kernel: str) -> str:
    if (not kernel or "/" in kernel or "\\" in kernel or "\x00" in kernel
            or kernel in {".", ".."} or kernel.startswith("-")):
        raise ValueError(f"Unsafe kernel release string: {kernel!r}")
    return kernel


def allowed_root_uids(root: Path) -> set[int]:
    if root == Path("/"):
        return {0}
    try:
        root_uid = root.stat().st_uid
    except OSError:
        return {0}
    return {0} if root_uid == 0 else {0, root_uid}


def manifest_name(module_file: str) -> str:
    return f"{module_file}.manifest.json"


def tx_json_name(module_file: str) -> str:
    return f".tx.{module_file}.json"


def tx_dir_name(module_file: str) -> str:
    return f".tx.{module_file}.dir"


def clearance_sentinel_name(module_file: str) -> str:
    return f".{module_file}.clearance-pending"


def migration_state_name(component: str) -> str:
    return f"{component}.migration.json"


def _default_runner(argv: list[str], *, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)


def _fsync_dir(dir_fd: int) -> None:
    try:
        os.fsync(dir_fd)
    except OSError:
        pass


def verify_dir_chain(
    root: Path,
    parts: tuple[str, ...],
    *,
    create_from_index: int | None = None,
    new_dir_mode: int = 0o755,
    leaf_mode: int | None = None,
    allowed_uids: set[int] | None = None,
) -> int:
    """Walk `parts` from `root` via dir_fd + O_NOFOLLOW and verify ownership/modes."""
    uids = allowed_uids if allowed_uids is not None else allowed_root_uids(root)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    try:
        root_fd = os.open(root, flags)
    except OSError as exc:
        raise ModuleStagingError(f"Root directory {root} unavailable: {exc}") from exc
    cur_fd = root_fd
    try:
        info = os.fstat(cur_fd)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid not in uids or (info.st_mode & 0o022) != 0:
            raise ModuleStagingError(f"Unsafe ownership or permissions on {root}")
        for idx, part in enumerate(parts):
            create_here = create_from_index is not None and idx >= create_from_index
            mode_here = leaf_mode if (idx == len(parts) - 1 and leaf_mode is not None) else new_dir_mode
            if create_here:
                try:
                    os.mkdir(part, mode_here, dir_fd=cur_fd)
                except FileExistsError:
                    pass
                except OSError as exc:
                    raise ModuleStagingError(f"Cannot create directory {'/'.join(parts[:idx + 1])}: {exc}") from exc
            try:
                next_fd = os.open(part, flags, dir_fd=cur_fd)
            except OSError as exc:
                raise ModuleStagingError(
                    f"Unsafe or missing directory {'/'.join(parts[:idx + 1])}: {exc}"
                ) from exc
            os.close(cur_fd)
            cur_fd = next_fd
            info = os.fstat(cur_fd)
            if not stat.S_ISDIR(info.st_mode) or info.st_uid not in uids or (info.st_mode & 0o022) != 0:
                raise ModuleStagingError(
                    f"Unsafe ownership or writable permissions on {'/'.join(parts[:idx + 1])}"
                )
            if idx == len(parts) - 1 and leaf_mode is not None and stat.S_IMODE(info.st_mode) != leaf_mode:
                raise ModuleStagingError(
                    f"Expected mode {oct(leaf_mode)} on {'/'.join(parts)}"
                )
        result_fd = cur_fd
        cur_fd = -1
        return result_fd
    finally:
        if cur_fd != -1:
            os.close(cur_fd)


def open_regular_ro(
    path_or_name: Path | str,
    *,
    dir_fd: int | None = None,
    allowed_uids: set[int],
    exact_mode: int | None = None,
    label: str,
) -> tuple[int, bytes]:
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
    try:
        fd = os.open(path_or_name, flags, dir_fd=dir_fd) if dir_fd is not None else os.open(path_or_name, flags)
    except OSError as exc:
        raise ModuleStagingError(f"Cannot safely open {label}: {exc}") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise ModuleStagingError(f"{label} is not a regular file")
        if info.st_nlink != 1:
            raise ModuleStagingError(f"{label} has unexpected hardlink count ({info.st_nlink})")
        if info.st_uid not in allowed_uids:
            raise ModuleStagingError(f"{label} has untrusted owner uid {info.st_uid}")
        if (info.st_mode & 0o022) != 0:
            raise ModuleStagingError(f"{label} has unsafe group/world write permissions")
        if exact_mode is not None and stat.S_IMODE(info.st_mode) != exact_mode:
            raise ModuleStagingError(f"{label} expected mode {oct(exact_mode)}, got {oct(stat.S_IMODE(info.st_mode))}")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            chunks.append(chunk)
        return info.st_uid, b"".join(chunks)
    finally:
        os.close(fd)


@contextmanager
def acquire_private_locks(
    root: Path,
    lock_names: tuple[str, ...],
    busy_message: str,
    *,
    allowed_uids: set[int] | None = None,
    inheritable: bool = False,
) -> Iterator[list[int]]:
    """Acquire one or more locks in fixed order without ever replacing an existing inode."""
    uids = allowed_uids if allowed_uids is not None else allowed_root_uids(root)
    if root != Path("/"):
        create_idx = 0
    else:
        create_idx = 1
    dir_fd = verify_dir_chain(
        root,
        ("run", LOCK_SUBDIR),
        create_from_index=create_idx,
        leaf_mode=0o700,
        allowed_uids=uids,
    )
    lock_fds: list[int] = []
    try:
        for lock_name in lock_names:
            try:
                fd = os.open(
                    lock_name,
                    os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
                    0o600,
                    dir_fd=dir_fd,
                )
            except OSError as exc:
                raise ModuleStagingError(f"Lock file {lock_name} unavailable: {exc}") from exc
            lock_fds.append(fd)
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or info.st_uid not in uids or stat.S_IMODE(info.st_mode) != 0o600):
                raise ModuleStagingError(f"Lock file {lock_name} has unsafe ownership, type, or permissions")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ModuleStagingError(busy_message) from exc
            if inheritable:
                os.set_inheritable(fd, True)
        yield lock_fds
    finally:
        for fd in reversed(lock_fds):
            os.close(fd)
        os.close(dir_fd)


def parse_manifest_bytes(
    raw: bytes,
    *,
    expected_component: str,
    expected_kernel: str,
    expected_module_name: str,
    expected_sha256: str | None = None,
) -> dict[str, object]:
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise ModuleStagingError("Malformed module provenance manifest") from exc
    if not isinstance(data, dict):
        raise ModuleStagingError("Invalid module provenance manifest structure")
    if (data.get("managed_by") != MANAGED_BY
            or data.get("schema_version") != SCHEMA_VERSION
            or data.get("component") != expected_component
            or data.get("module_name") != expected_module_name
            or data.get("kernel") != expected_kernel
            or data.get("state") != "committed"):
        raise ModuleStagingError("Module provenance manifest fields do not match Nitro Control target")
    digest = data.get("sha256")
    if not isinstance(digest, str) or not _HEX_SHA.fullmatch(digest):
        raise ModuleStagingError("Invalid digest in module provenance manifest")
    if expected_sha256 is not None and digest != expected_sha256:
        raise ModuleStagingError("Manifest digest does not match staged module")
    return data


def _parse_tx_bytes(raw: bytes, *, spec: ComponentSpec, kernel: str) -> dict[str, object]:
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise ModuleStagingError("Malformed module transaction marker") from exc
    if (not isinstance(data, dict)
            or data.get("managed_by") != MANAGED_BY
            or data.get("schema_version") != SCHEMA_VERSION
            or data.get("component") != spec.component
            or data.get("module_name") != spec.module_name
            or data.get("kernel") != kernel
            or data.get("state") not in {"preparing", "committing"}):
        raise ModuleStagingError("Invalid or foreign module transaction marker")
    target = data.get("target_sha256")
    prev = data.get("previous_sha256")
    if not isinstance(target, str) or not _HEX_SHA.fullmatch(target):
        raise ModuleStagingError("Invalid target digest in transaction marker")
    if prev is not None and (not isinstance(prev, str) or not _HEX_SHA.fullmatch(prev)):
        raise ModuleStagingError("Invalid previous digest in transaction marker")
    return data


def _read_modinfo(ko_path: Path, runner: Runner) -> tuple[str, str]:
    name_proc = runner(["modinfo", "-F", "name", str(ko_path)])
    ver_proc = runner(["modinfo", "-F", "vermagic", str(ko_path)])
    if name_proc.returncode != 0 or ver_proc.returncode != 0:
        raise ModuleStagingError(f"modinfo failed for {ko_path}")
    mod_name = name_proc.stdout.strip()
    vermagic = ver_proc.stdout.split()[:1]
    return mod_name, (vermagic[0] if vermagic else "")


def _parse_modinfo_params(modinfo_stdout: str) -> frozenset[str]:
    return frozenset(
        line.split(":", 1)[0].strip()
        for line in modinfo_stdout.splitlines()
        if ":" in line and line.split(":", 1)[0].strip()
    )


def _find_disallowed_native_params(modinfo_stdout: str) -> list[str]:
    return sorted(_parse_modinfo_params(modinfo_stdout) & DISALLOWED_NATIVE_EXPERIMENTAL_PARAMS)


def _inspect_native_param_metadata(
    ko_path: Path,
    runner: Runner | None = None,
    *,
    timeout: int = METADATA_INSPECT_TIMEOUT,
) -> NativeParamInspection:
    """Inspect `modinfo -p` with bounded timeout and explicit outcome classification."""
    try:
        if runner is not None:
            try:
                proc = runner(["modinfo", "-p", str(ko_path)], timeout=timeout)
            except TypeError:
                proc = runner(["modinfo", "-p", str(ko_path)])
        else:
            modinfo_bin = shutil.which("modinfo") or (
                "/usr/sbin/modinfo" if Path("/usr/sbin/modinfo").is_file() else None
            )
            if modinfo_bin is None:
                return NativeParamInspection(
                    "missing_tool",
                    frozenset(),
                    (),
                    "modinfo tool is unavailable to inspect native RGB module metadata",
                )
            proc = subprocess.run(
                [modinfo_bin, "-p", str(ko_path)],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
    except FileNotFoundError:
        return NativeParamInspection(
            "missing_tool",
            frozenset(),
            (),
            "modinfo tool is unavailable to inspect native RGB module metadata",
        )
    except subprocess.TimeoutExpired:
        return NativeParamInspection(
            "timeout",
            frozenset(),
            (),
            f"modinfo -p timed out after {timeout}s inspecting {ko_path.name}",
        )
    except OSError as exc:
        return NativeParamInspection(
            "failed",
            frozenset(),
            (),
            f"modinfo -p failed for {ko_path.name}: {exc}",
        )

    if proc.returncode != 0:
        err = (getattr(proc, "stderr", "") or "").strip()
        msg = f"modinfo -p exited {proc.returncode} for {ko_path.name}"
        if err:
            msg = f"{msg}: {err[:120]}"
        return NativeParamInspection("failed", frozenset(), (), msg)

    exposed = _parse_modinfo_params(proc.stdout)
    forbidden = tuple(sorted(exposed & DISALLOWED_NATIVE_EXPERIMENTAL_PARAMS))
    if forbidden:
        return NativeParamInspection(
            "incompatible",
            exposed,
            forbidden,
            f"Refusing experimental diagnostic parameters ({', '.join(forbidden)}) in staged {ko_path.name}",
        )
    return NativeParamInspection("compatible", exposed, (), "")


def _check_native_module_params(ko_path: Path, runner: Runner) -> None:
    insp = _inspect_native_param_metadata(ko_path, runner)
    if insp.status == "compatible":
        return
    if insp.status == "incompatible":
        raise ModuleStagingError(insp.detail)
    raise ModuleStagingError(f"modinfo -p failed for {ko_path}: {insp.detail}")


def _check_digest_text(raw: bytes, label: str) -> str:
    try:
        text = raw.decode("ascii").strip()
    except UnicodeError as exc:
        raise ModuleStagingError(f"Non-ASCII digest in {label}") from exc
    if not _HEX_SHA.fullmatch(text):
        raise ModuleStagingError(f"Invalid SHA-256 format in {label}")
    return text


def _write_private_file(dir_fd: int, name: str, data: bytes, mode: int) -> None:
    try:
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                     mode, dir_fd=dir_fd)
    except OSError as exc:
        raise ModuleStagingError(f"Cannot create staged file {name}: {exc}") from exc
    try:
        view = memoryview(data)
        total = 0
        while total < len(view):
            total += os.write(fd, view[total:])
        os.fchmod(fd, mode)
        try:
            os.fsync(fd)
        except OSError:
            pass
    finally:
        os.close(fd)


def _clear_tx_artifacts(root: Path, dir_fd: int, dir_path: Path, spec: ComponentSpec) -> None:
    uids = allowed_root_uids(root)
    ko_name = spec.module_file
    sha_name = f"{ko_name}.sha256"
    man_name = manifest_name(ko_name)
    expected_tx_entries = {ko_name: 0o644, sha_name: 0o600, man_name: 0o600}
    tx_dir = dir_path / tx_dir_name(ko_name)
    if tx_dir.is_symlink():
        raise ModuleStagingError(f"Refusing symlinked transaction directory {tx_dir}")
    if tx_dir.exists():
        tx_fd = verify_dir_chain(dir_path, (tx_dir_name(ko_name),), leaf_mode=0o700,
                                 allowed_uids=uids)
        try:
            items = os.listdir(tx_fd)
            unexpected = [item for item in items if item not in expected_tx_entries]
            if unexpected:
                raise ModuleStagingError(
                    f"Unexpected entry inside transaction directory {tx_dir}: {', '.join(sorted(unexpected))}"
                )
            for item in items:
                open_regular_ro(
                    item, dir_fd=tx_fd, allowed_uids=uids,
                    exact_mode=expected_tx_entries[item], label=str(tx_dir / item),
                )
            for item in items:
                os.unlink(item, dir_fd=tx_fd)
            _fsync_dir(tx_fd)
        finally:
            os.close(tx_fd)
        os.rmdir(tx_dir_name(ko_name), dir_fd=dir_fd)
    tx_json = dir_path / tx_json_name(ko_name)
    if tx_json.is_symlink():
        raise ModuleStagingError(f"Refusing symlinked transaction file {tx_json}")
    if tx_json.exists():
        open_regular_ro(tx_json_name(ko_name), dir_fd=dir_fd, allowed_uids=uids, exact_mode=0o600, label=str(tx_json))
        os.unlink(tx_json_name(ko_name), dir_fd=dir_fd)
    _fsync_dir(dir_fd)


def _validate_tx_dir_contents(
    root: Path,
    dir_path: Path,
    spec: ComponentSpec,
    kernel: str,
    target_sha: str,
) -> None:
    uids = allowed_root_uids(root)
    ko_name = spec.module_file
    sha_name = f"{ko_name}.sha256"
    man_name = manifest_name(ko_name)
    expected_tx_entries = {ko_name: 0o644, sha_name: 0o600, man_name: 0o600}
    tx_dir = dir_path / tx_dir_name(ko_name)
    if tx_dir.is_symlink():
        raise ModuleStagingError(f"Refusing symlinked transaction directory {tx_dir}")
    if not tx_dir.exists():
        return
    tx_fd = verify_dir_chain(dir_path, (tx_dir_name(ko_name),), leaf_mode=0o700, allowed_uids=uids)
    try:
        items = os.listdir(tx_fd)
        unexpected = [item for item in items if item not in expected_tx_entries]
        if unexpected:
            raise ModuleStagingError(
                f"Unexpected entry inside transaction directory {tx_dir}: {', '.join(sorted(unexpected))}"
            )
        for item in items:
            _, raw = open_regular_ro(
                item, dir_fd=tx_fd, allowed_uids=uids,
                exact_mode=expected_tx_entries[item], label=str(tx_dir / item),
            )
            if item == ko_name and hashlib.sha256(raw).hexdigest() != target_sha:
                raise ModuleStagingError(f"Staged module inside {tx_dir} does not match transaction digest")
            if item == sha_name and _check_digest_text(raw, str(tx_dir / item)) != target_sha:
                raise ModuleStagingError(f"Staged checksum inside {tx_dir} does not match transaction digest")
            if item == man_name:
                parse_manifest_bytes(
                    raw,
                    expected_component=spec.component,
                    expected_kernel=kernel,
                    expected_module_name=spec.module_name,
                    expected_sha256=target_sha,
                )
    finally:
        os.close(tx_fd)


def _inspect_location(
    root: Path,
    dir_fd: int,
    dir_path: Path,
    spec: ComponentSpec,
    kernel: str,
    *,
    runner: Runner,
    allow_adopt: bool,
) -> tuple[str, str | None]:
    """Inspect a module directory under root-verified dir_fd without mutating anything."""
    strict_uids = {0} if root == Path("/") else allowed_root_uids(root)
    ko_name = spec.module_file
    sha_name = f"{ko_name}.sha256"
    man_name = manifest_name(ko_name)
    tx_j_name = tx_json_name(ko_name)
    tx_d_name = tx_dir_name(ko_name)

    entries = set(os.listdir(dir_fd))
    stray_tmps = [e for e in entries if e.startswith((f".{ko_name}.tmp.", f".{ko_name}.sha256.tmp."))]
    if stray_tmps and tx_j_name not in entries:
        raise ModuleStagingError(
            f"Ambiguous temporary files without Nitro transaction marker in {dir_path}: {', '.join(sorted(stray_tmps))}"
        )

    if tx_j_name in entries or tx_d_name in entries:
        if tx_j_name not in entries:
            raise ModuleStagingError(
                f"Ambiguous staging directory {dir_path / tx_d_name} without Nitro transaction marker"
            )
        _, tx_raw = open_regular_ro(
            tx_j_name, dir_fd=dir_fd, allowed_uids=strict_uids, exact_mode=0o600, label=str(dir_path / tx_j_name)
        )
        tx = _parse_tx_bytes(tx_raw, spec=spec, kernel=kernel)
        allowed_hashes = {tx["target_sha256"]}
        if tx["previous_sha256"] is not None:
            allowed_hashes.add(tx["previous_sha256"])

        _validate_tx_dir_contents(root, dir_path, spec, kernel, str(tx["target_sha256"]))

        cur_sha: str | None = None
        if ko_name in entries or (dir_path / ko_name).is_symlink():
            _, ko_bytes = open_regular_ro(
                ko_name, dir_fd=dir_fd, allowed_uids=strict_uids, label=str(dir_path / ko_name)
            )
            cur_sha = hashlib.sha256(ko_bytes).hexdigest()
            if cur_sha not in allowed_hashes:
                raise ModuleStagingError(
                    f"Existing {dir_path / ko_name} digest does not match interrupted Nitro transaction; preserving file"
                )
        if sha_name in entries or (dir_path / sha_name).is_symlink():
            _, sha_raw = open_regular_ro(
                sha_name, dir_fd=dir_fd, allowed_uids=strict_uids, exact_mode=0o600, label=str(dir_path / sha_name)
            )
            sha_text = _check_digest_text(sha_raw, str(dir_path / sha_name))
            if sha_text not in allowed_hashes:
                raise ModuleStagingError(
                    f"Existing {dir_path / sha_name} does not match interrupted Nitro transaction; preserving file"
                )
        if man_name in entries or (dir_path / man_name).is_symlink():
            _, man_raw = open_regular_ro(
                man_name, dir_fd=dir_fd, allowed_uids=strict_uids, exact_mode=0o600, label=str(dir_path / man_name)
            )
            man_data = parse_manifest_bytes(
                man_raw,
                expected_component=spec.component,
                expected_kernel=kernel,
                expected_module_name=spec.module_name,
            )
            if man_data["sha256"] not in allowed_hashes:
                raise ModuleStagingError(
                    f"Existing {dir_path / man_name} digest does not match interrupted Nitro transaction; preserving file"
                )
        return "interrupted", cur_sha

    has_ko = ko_name in entries
    has_sha = sha_name in entries
    has_man = man_name in entries
    if not has_ko and not has_sha and not has_man:
        return "absent", None
    if not has_ko:
        if has_sha and not has_man:
            raise ModuleStagingError(f"Incomplete artifact pair in {dir_path}: digest exists without {ko_name}")
        raise ModuleStagingError(f"Incomplete staged artifact in {dir_path}: manifest exists without {ko_name}")
    if not has_sha:
        raise ModuleStagingError(f"Unverified artifact in {dir_path}: {ko_name} lacks {sha_name}")

    _, ko_bytes = open_regular_ro(ko_name, dir_fd=dir_fd, allowed_uids=strict_uids, label=str(dir_path / ko_name))
    _, sha_raw = open_regular_ro(
        sha_name, dir_fd=dir_fd, allowed_uids=strict_uids, exact_mode=0o600, label=str(dir_path / sha_name)
    )
    expected_sha = _check_digest_text(sha_raw, str(dir_path / sha_name))
    actual_sha = hashlib.sha256(ko_bytes).hexdigest()
    if actual_sha != expected_sha:
        raise ModuleStagingError(f"Digest mismatch for {dir_path / ko_name}")
    mod_name, vermagic = _read_modinfo(dir_path / ko_name, runner)
    if mod_name != spec.module_name or vermagic != kernel:
        raise ModuleStagingError(
            f"Module metadata mismatch for {dir_path / ko_name}: name={mod_name!r}, vermagic={vermagic!r}, expected={kernel!r}"
        )

    if has_man:
        _, man_raw = open_regular_ro(
            man_name, dir_fd=dir_fd, allowed_uids=strict_uids, exact_mode=0o600, label=str(dir_path / man_name)
        )
        parse_manifest_bytes(
            man_raw,
            expected_component=spec.component,
            expected_kernel=kernel,
            expected_module_name=spec.module_name,
            expected_sha256=actual_sha,
        )
        return "managed", actual_sha

    return ("adoptable" if allow_adopt else "unadopted"), actual_sha


def _commit_module_transaction(
    root: Path,
    dir_fd: int,
    dir_path: Path,
    spec: ComponentSpec,
    kernel: str,
    payload_bytes: bytes,
    target_sha: str,
    previous_sha: str | None,
    *,
    runner: Runner,
    on_stage: StageHook | None = None,
) -> None:
    uids = allowed_root_uids(root)
    ko_name = spec.module_file
    sha_name = f"{ko_name}.sha256"
    man_name = manifest_name(ko_name)
    tx_j_name = tx_json_name(ko_name)
    tx_d_name = tx_dir_name(ko_name)

    _clear_tx_artifacts(root, dir_fd, dir_path, spec)
    tx_fd = verify_dir_chain(
        dir_path, (tx_d_name,), create_from_index=0, new_dir_mode=0o700, leaf_mode=0o700, allowed_uids=uids
    )
    committed_tx_marker = False
    try:
        manifest_obj = {
            "managed_by": MANAGED_BY,
            "schema_version": SCHEMA_VERSION,
            "component": spec.component,
            "module_name": spec.module_name,
            "kernel": kernel,
            "sha256": target_sha,
            "state": "committed",
        }
        _write_private_file(tx_fd, ko_name, payload_bytes, 0o644)
        _write_private_file(tx_fd, sha_name, (target_sha + "\n").encode("ascii"), 0o600)
        _write_private_file(tx_fd, man_name, (json.dumps(manifest_obj, sort_keys=True) + "\n").encode("utf-8"), 0o600)

        staged_ko = dir_path / tx_d_name / ko_name
        mod_name, vermagic = _read_modinfo(staged_ko, runner)
        if mod_name != spec.module_name or vermagic != kernel:
            raise ModuleStagingError(
                f"Staged bytes failed verification: name={mod_name!r}, vermagic={vermagic!r}, expected={kernel!r}"
            )
        if spec.component == "native-rgb":
            _check_native_module_params(staged_ko, runner)
        if shutil.which("restorecon"):
            runner(["restorecon", "-F", str(staged_ko),
                    str(dir_path / tx_d_name / sha_name),
                    str(dir_path / tx_d_name / man_name)])

        tx_obj = {
            "managed_by": MANAGED_BY,
            "schema_version": SCHEMA_VERSION,
            "component": spec.component,
            "module_name": spec.module_name,
            "kernel": kernel,
            "previous_sha256": previous_sha,
            "target_sha256": target_sha,
            "state": "committing",
        }
        _write_private_file(dir_fd, tx_j_name, (json.dumps(tx_obj, sort_keys=True) + "\n").encode("utf-8"), 0o600)
        _fsync_dir(dir_fd)
        committed_tx_marker = True
        if on_stage is not None:
            on_stage("after_tx_prepare")

        if (dir_path / man_name).exists():
            os.unlink(man_name, dir_fd=dir_fd)
            _fsync_dir(dir_fd)
        if on_stage is not None:
            on_stage("after_manifest_unlink")

        os.replace(staged_ko, dir_path / ko_name)
        _fsync_dir(dir_fd)
        if on_stage is not None:
            on_stage("after_ko_replace")

        os.replace(dir_path / tx_d_name / sha_name, dir_path / sha_name)
        _fsync_dir(dir_fd)
        if on_stage is not None:
            on_stage("after_sha_replace")

        os.replace(dir_path / tx_d_name / man_name, dir_path / man_name)
        _fsync_dir(dir_fd)
        if on_stage is not None:
            on_stage("after_manifest_commit")

        _clear_tx_artifacts(root, dir_fd, dir_path, spec)
    finally:
        os.close(tx_fd)
        if not committed_tx_marker:
            # Failed pre-commit validation (e.g., staged modinfo check); remove unused staging dir.
            _clear_tx_artifacts(root, dir_fd, dir_path, spec)


def _ensure_private_kernel_dir(root: Path, kernel: str) -> tuple[int, Path]:
    uids = allowed_root_uids(root)
    rel = (*PRIVATE_MODULES_REL, kernel)
    # Only create starting at `usr/local/lib/nitro-control` (index 3 under usr/local/lib).
    fd = verify_dir_chain(root, rel, create_from_index=3, new_dir_mode=0o755, allowed_uids=uids)
    return fd, root.joinpath(*rel)


def _read_source_bytes(source: Path, *, reject_world_write: bool = False) -> bytes:
    if not source.is_absolute():
        raise ModuleStagingError(f"Source file path must be absolute: {source}")
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
    try:
        fd = os.open(source, flags)
    except OSError as exc:
        raise ModuleStagingError(f"Cannot safely open source file {source}: {exc}") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise ModuleStagingError(f"Source path is not a regular file: {source}")
        if info.st_nlink != 1:
            raise ModuleStagingError(f"Source file has unexpected hardlink count ({info.st_nlink}): {source}")
        if reject_world_write and (info.st_mode & 0o002) != 0:
            raise ModuleStagingError(f"Source file must not be world-writable: {source}")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(fd)


def _modules_dep_stale(root: Path, kernel: str, module_file: str, module_name: str, component: str) -> bool:
    kdir = root / "usr/lib/modules" / kernel
    dep = kdir / "modules.dep"
    alias = kdir / "modules.alias"
    target_rel = f"extra/nitro-control/{module_file}"
    try:
        if dep.exists():
            if dep.is_symlink():
                return True
            if target_rel in dep.read_text(encoding="utf-8", errors="ignore"):
                return True
        if component == "rgb-driver" and alias.exists():
            if alias.is_symlink():
                return True
            legacy_ko = kdir / "extra/nitro-control" / module_file
            if not legacy_ko.exists():
                for line in alias.read_text(encoding="utf-8", errors="ignore").splitlines():
                    parts = line.split()
                    if len(parts) >= 3 and parts[2] == module_name:
                        return True
    except OSError:
        return True
    return False


def _kernels_with_clearance_sentinels(root: Path, spec: ComponentSpec) -> set[str]:
    priv_root = root.joinpath(*PRIVATE_MODULES_REL)
    if not priv_root.exists():
        return set()
    if priv_root.is_symlink() or not priv_root.is_dir():
        raise ModuleStagingError(f"Unsafe staging directory {priv_root}")
    sentinel_name = clearance_sentinel_name(spec.module_file)
    found: set[str] = set()
    for kdir in sorted(priv_root.iterdir(), key=lambda p: p.name):
        if kdir.is_symlink() or not kdir.is_dir():
            continue
        kver = validate_kernel_name(kdir.name)
        spath = kdir / sentinel_name
        if spath.exists() or spath.is_symlink():
            found.add(kver)
    return found


def _derive_migration_summary(
    kernel_records: dict[str, dict[str, str]],
    errors_by_kernel: dict[str, str],
) -> tuple[list[str], list[str], list[str], str]:
    pending_set: set[str] = set(errors_by_kernel)
    unverified_set: set[str] = set()
    exposed_set: set[str] = set()
    for kver, rec in kernel_records.items():
        if rec["initramfs_status"] == "exposed":
            exposed_set.add(kver)
            pending_set.add(kver)
        elif rec["initramfs_status"] == "unverified":
            unverified_set.add(kver)
        if rec["migration_status"] == "pending" or rec["index_status"] != "verified_clean":
            pending_set.add(kver)
    state_val = (
        "pending_clearance" if pending_set
        else ("unverified_clearance" if unverified_set else "complete")
    )
    return sorted(pending_set), sorted(unverified_set), sorted(exposed_set), state_val


def _load_migration_state(root: Path, spec: ComponentSpec) -> dict[str, object]:
    uids = allowed_root_uids(root)
    base_path = root / "usr/local/lib/nitro-control"
    if not base_path.exists() and not base_path.is_symlink():
        return {
            "pending_kernels": [],
            "unverified_kernels": [],
            "exposed_initramfs_kernels": [],
            "kernels": {},
            "errors": {},
            "state": "complete",
        }
    base_fd = verify_dir_chain(root, ("usr", "local", "lib", "nitro-control"), allowed_uids=uids)
    try:
        mname = migration_state_name(spec.component)
        mpath = base_path / mname
        if not mpath.exists() and not mpath.is_symlink():
            return {
                "pending_kernels": [],
                "unverified_kernels": [],
                "exposed_initramfs_kernels": [],
                "kernels": {},
                "errors": {},
                "state": "complete",
            }
        _, raw = open_regular_ro(mname, dir_fd=base_fd, allowed_uids=uids, exact_mode=0o600, label=str(mpath))
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeError, ValueError) as exc:
            raise ModuleStagingError(f"Malformed migration state file {mpath}") from exc
        if (not isinstance(data, dict)
                or data.get("managed_by") != MANAGED_BY
                or data.get("schema_version") != SCHEMA_VERSION
                or data.get("component") != spec.component
                or data.get("state") not in _MIGRATION_STATES):
            raise ModuleStagingError(f"Invalid or foreign migration state file {mpath}")
        pending = data.get("pending_kernels")
        unverified = data.get("unverified_kernels")
        exposed = data.get("exposed_initramfs_kernels")
        kernels_raw = data.get("kernels")
        errors = data.get("errors")
        if (not isinstance(pending, list) or not all(isinstance(k, str) for k in pending)
                or not isinstance(unverified, list) or not all(isinstance(k, str) for k in unverified)
                or not isinstance(exposed, list) or not all(isinstance(k, str) for k in exposed)
                or not isinstance(kernels_raw, dict)
                or not isinstance(errors, dict)
                or not all(isinstance(k, str) and isinstance(v, str) for k, v in errors.items())):
            raise ModuleStagingError(f"Corrupt migration kernel state in {mpath}")
        kernels: dict[str, dict[str, str]] = {}
        for kver, rec in kernels_raw.items():
            vk = validate_kernel_name(kver)
            if (not isinstance(rec, dict)
                    or rec.get("index_status") not in _INDEX_STATUSES
                    or rec.get("initramfs_status") not in _INITRAMFS_STATUSES
                    or rec.get("migration_status") not in _MIGRATION_KERNEL_STATUSES):
                raise ModuleStagingError(f"Corrupt migration record for {kver} in {mpath}")
            kernels[vk] = {
                "index_status": str(rec["index_status"]),
                "initramfs_status": str(rec["initramfs_status"]),
                "migration_status": str(rec["migration_status"]),
            }
        validated_pending = sorted({
            *(validate_kernel_name(k) for k in pending),
            *(validate_kernel_name(k) for k in exposed),
            *(validate_kernel_name(k) for k in errors),
            *(
                k for k, rec in kernels.items()
                if rec["migration_status"] == "pending"
                or rec["index_status"] != "verified_clean"
                or rec["initramfs_status"] == "exposed"
            ),
        })
        validated_unverified = sorted({
            *(validate_kernel_name(k) for k in unverified),
            *(k for k, rec in kernels.items() if rec["initramfs_status"] == "unverified"),
        })
        validated_exposed = sorted({
            *(validate_kernel_name(k) for k in exposed),
            *(k for k, rec in kernels.items() if rec["initramfs_status"] == "exposed"),
        })
        return {
            "pending_kernels": validated_pending,
            "unverified_kernels": validated_unverified,
            "exposed_initramfs_kernels": validated_exposed,
            "kernels": kernels,
            "errors": dict(errors),
            "state": data["state"],
        }
    finally:
        os.close(base_fd)


def _save_migration_state(
    root: Path,
    spec: ComponentSpec,
    *,
    kernel_records: dict[str, dict[str, str]],
    errors_by_kernel: dict[str, str],
) -> str:
    uids = allowed_root_uids(root)
    base_fd = verify_dir_chain(
        root, ("usr", "local", "lib", "nitro-control"),
        create_from_index=3, new_dir_mode=0o755, allowed_uids=uids,
    )
    try:
        mname = migration_state_name(spec.component)
        mpath = root / "usr/local/lib/nitro-control" / mname
        if mpath.is_symlink():
            raise ModuleStagingError(f"Refusing symlinked migration state {mpath}")
        pending_list, unverified_list, exposed_list, state_val = _derive_migration_summary(
            kernel_records, errors_by_kernel
        )
        payload = {
            "managed_by": MANAGED_BY,
            "schema_version": SCHEMA_VERSION,
            "component": spec.component,
            "pending_kernels": pending_list,
            "unverified_kernels": unverified_list,
            "exposed_initramfs_kernels": exposed_list,
            "kernels": {k: dict(kernel_records[k]) for k in sorted(kernel_records)},
            "errors": dict(sorted(errors_by_kernel.items())),
            "state": state_val,
        }
        tmp_name = f".{mname}.tmp.{os.getpid()}"
        tmp_path = root / "usr/local/lib/nitro-control" / tmp_name
        if tmp_path.exists() or tmp_path.is_symlink():
            os.unlink(tmp_name, dir_fd=base_fd)
        _write_private_file(base_fd, tmp_name, (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8"), 0o600)
        os.replace(tmp_path, mpath)
        _fsync_dir(base_fd)
        return state_val
    finally:
        os.close(base_fd)


def _sync_clearance_sentinels(root: Path, spec: ComponentSpec, pending_kernels: set[str]) -> None:
    uids = allowed_root_uids(root)
    priv_root = root.joinpath(*PRIVATE_MODULES_REL)
    if not priv_root.exists():
        return
    if priv_root.is_symlink() or not priv_root.is_dir():
        raise ModuleStagingError(f"Unsafe staging directory {priv_root}")
    sentinel_name = clearance_sentinel_name(spec.module_file)
    for kdir in sorted(priv_root.iterdir(), key=lambda p: p.name):
        if kdir.is_symlink() or not kdir.is_dir():
            continue
        kver = validate_kernel_name(kdir.name)
        fd = verify_dir_chain(root, (*PRIVATE_MODULES_REL, kver), allowed_uids=uids)
        try:
            spath = kdir / sentinel_name
            if kver in pending_kernels:
                if spath.is_symlink():
                    raise ModuleStagingError(f"Refusing symlinked clearance sentinel {spath}")
                if spath.exists():
                    open_regular_ro(sentinel_name, dir_fd=fd, allowed_uids=uids, exact_mode=0o600, label=str(spath))
                else:
                    _write_private_file(fd, sentinel_name, b"", 0o600)
            elif spath.exists() or spath.is_symlink():
                open_regular_ro(sentinel_name, dir_fd=fd, allowed_uids=uids, exact_mode=0o600, label=str(spath))
                os.unlink(sentinel_name, dir_fd=fd)
                _fsync_dir(fd)
        finally:
            os.close(fd)


def _persist_pending_migration_step(
    root: Path,
    spec: ComponentSpec,
    *,
    pending_kernels: set[str],
    prior_kernels: dict[str, dict[str, str]],
    errors_by_kernel: dict[str, str],
    on_stage: StageHook | None = None,
) -> None:
    if not pending_kernels:
        return
    # Preserve all pre-existing unresolved kernel obligations (including "unverified"
    # or "exposed" entries on unrelated kernels) when recording a new transition.
    snapshot_records: dict[str, dict[str, str]] = {
        kver: dict(rec) for kver, rec in prior_kernels.items()
    }
    for kver in sorted(pending_kernels):
        prev = snapshot_records.get(kver, {})
        prev_initramfs = prev.get("initramfs_status", "not_present")
        snapshot_records[kver] = {
            "index_status": "stale_or_failed",
            "initramfs_status": prev_initramfs if prev_initramfs in _INITRAMFS_STATUSES else "not_present",
            "migration_status": "pending",
        }
    _save_migration_state(
        root, spec, kernel_records=snapshot_records, errors_by_kernel=errors_by_kernel
    )
    if on_stage is not None:
        on_stage("after_migration_journal_save")
    _sync_clearance_sentinels(root, spec, pending_kernels)
    if on_stage is not None:
        on_stage("after_clearance_sentinel_publish")


def _unresolved_blocking_kernels(prior_state: dict[str, object], root: Path, spec: ComponentSpec) -> set[str]:
    blocking = (
        set(prior_state["pending_kernels"])
        | set(prior_state["exposed_initramfs_kernels"])
        | _kernels_with_clearance_sentinels(root, spec)
    )
    for kver, rec in dict(prior_state["kernels"]).items():
        if (rec["initramfs_status"] == "exposed"
                or rec["migration_status"] == "pending"
                or rec["index_status"] != "verified_clean"):
            blocking.add(kver)
    return blocking


def _unresolved_obligation_kernels(prior_state: dict[str, object], root: Path, spec: ComponentSpec) -> set[str]:
    unresolved = _unresolved_blocking_kernels(prior_state, root, spec) | set(prior_state["unverified_kernels"])
    for kver, rec in dict(prior_state["kernels"]).items():
        if rec["initramfs_status"] == "unverified":
            unresolved.add(kver)
    return unresolved


def _collect_verification_targets(
    root: Path,
    spec: ComponentSpec,
    running_kernel: str,
    *,
    prior_state: dict[str, object],
    include_private_staged: bool = False,
) -> tuple[set[str], set[str]]:
    """Enumerate all kernels subject to verification and establish blocking pre-mutation targets."""
    affected = _unresolved_obligation_kernels(prior_state, root, spec)
    pre_pending = _unresolved_blocking_kernels(prior_state, root, spec)

    modules_root = root / "usr/lib/modules"
    if modules_root.exists() and modules_root.is_dir() and not modules_root.is_symlink():
        for kdir in sorted(modules_root.iterdir(), key=lambda p: p.name):
            if kdir.is_symlink() or not kdir.is_dir():
                continue
            try:
                kver = validate_kernel_name(kdir.name)
            except ValueError:
                affected.add(kdir.name)
                pre_pending.add(kdir.name)
                continue
            leg_dir = kdir / "extra/nitro-control"
            if leg_dir.exists() or leg_dir.is_symlink() or _modules_dep_stale(
                root, kver, spec.module_file, spec.module_name, spec.component
            ):
                affected.add(kver)
                pre_pending.add(kver)

    if (root / "boot" / f"initramfs-{running_kernel}.img").exists():
        pre_pending.add(running_kernel)

    if include_private_staged:
        priv_root = root.joinpath(*PRIVATE_MODULES_REL)
        if priv_root.exists() and priv_root.is_dir() and not priv_root.is_symlink():
            for kdir in sorted(priv_root.iterdir(), key=lambda p: p.name):
                if kdir.is_symlink() or not kdir.is_dir():
                    continue
                try:
                    kver = validate_kernel_name(kdir.name)
                except ValueError:
                    continue
                if any((kdir / name).exists() or (kdir / name).is_symlink() for name in (
                    spec.module_file, f"{spec.module_file}.sha256", manifest_name(spec.module_file),
                    tx_json_name(spec.module_file), tx_dir_name(spec.module_file),
                )):
                    affected.add(kver)
                    pre_pending.add(kver)

    return affected, pre_pending


def _finalize_migration_state(
    root: Path,
    spec: ComponentSpec,
    *,
    kernel_records: dict[str, dict[str, str]],
    errors_by_kernel: dict[str, str],
    on_stage: StageHook | None = None,
) -> str:
    pending_list, unverified_list, _, state_val = _derive_migration_summary(
        kernel_records, errors_by_kernel
    )
    if pending_list or unverified_list:
        _save_migration_state(
            root, spec, kernel_records=kernel_records, errors_by_kernel=errors_by_kernel
        )
        _sync_clearance_sentinels(root, spec, set(pending_list))
        return state_val

    mname = migration_state_name(spec.component)
    mpath = root / "usr/local/lib/nitro-control" / mname
    if mpath.is_symlink():
        raise ModuleStagingError(f"Refusing symlinked migration state {mpath}")
    if mpath.exists():
        uids = allowed_root_uids(root)
        base_fd = verify_dir_chain(root, ("usr", "local", "lib", "nitro-control"), allowed_uids=uids)
        try:
            open_regular_ro(mname, dir_fd=base_fd, allowed_uids=uids, exact_mode=0o600, label=str(mpath))
            os.unlink(mname, dir_fd=base_fd)
            _fsync_dir(base_fd)
        finally:
            os.close(base_fd)
    if on_stage is not None:
        on_stage("after_finalize_unlink_journal")
    _sync_clearance_sentinels(root, spec, set())
    return "complete"


def check_installation_qualification(root: Path, component: str) -> tuple[bool, dict[str, object]]:
    """Report installation-wide migration and boot-safety qualification across all kernels."""
    spec = COMPONENT_SPECS[component]
    root = Path(root)
    state = _load_migration_state(root, spec)
    pending = set(state["pending_kernels"])
    unverified = set(state["unverified_kernels"])
    exposed = set(state["exposed_initramfs_kernels"])
    errors = dict(state["errors"])

    for kver in _kernels_with_clearance_sentinels(root, spec):
        pending.add(kver)
        errors.setdefault(kver, f"Unresolved migration or clearance marker on {kver}")

    legacy_root = root / "usr/lib/modules"
    if legacy_root.exists() and not legacy_root.is_symlink():
        for kdir in sorted(legacy_root.iterdir(), key=lambda p: p.name):
            if kdir.is_symlink() or not kdir.is_dir():
                continue
            kver = kdir.name
            leg_dir = kdir / "extra/nitro-control"
            if ((leg_dir / spec.module_file).exists()
                    or (leg_dir / f"{spec.module_file}.sha256").exists()
                    or (leg_dir / manifest_name(spec.module_file)).exists()
                    or (leg_dir / spec.module_file).is_symlink()):
                pending.add(kver)
                errors.setdefault(kver, f"Unresolved legacy indexed artifact in {leg_dir}")
            elif _modules_dep_stale(root, kver, spec.module_file, spec.module_name, spec.component):
                pending.add(kver)
                errors.setdefault(kver, f"Stale kernel module index in /usr/lib/modules/{kver}")

    if pending or errors or exposed or state["state"] == "pending_clearance":
        qual_state = "pending_clearance"
    elif unverified or state["state"] == "unverified_clearance":
        qual_state = "unverified_clearance"
    else:
        qual_state = "complete"
    qualified = qual_state == "complete"
    return qualified, {
        "state": qual_state,
        "pending_kernels": sorted(pending),
        "unverified_kernels": sorted(unverified),
        "exposed_initramfs_kernels": sorted(exposed),
        "kernels": dict(state["kernels"]),
        "errors": errors,
    }


def _refresh_and_verify_indexes(
    root: Path,
    spec: ComponentSpec,
    affected_kernels: set[str],
    running_kernel: str,
    *,
    prior_kernels: dict[str, dict[str, str]] | None = None,
    prior_blocking_kernels: set[str] | None = None,
    check_running_initramfs: bool = False,
    runner: Runner,
) -> tuple[dict[str, dict[str, str]], dict[str, list[str]], list[str]]:
    kernel_records: dict[str, dict[str, str]] = {}
    errors_by_kernel: dict[str, list[str]] = {}
    warnings: list[str] = []
    prev_map = prior_kernels or {}
    prior_blocked = prior_blocking_kernels or set()
    modules_root = root / "usr/lib/modules"
    if modules_root.exists():
        for kdir in sorted(modules_root.iterdir(), key=lambda p: p.name):
            if kdir.is_symlink() or not kdir.is_dir():
                continue
            kver = kdir.name
            if _modules_dep_stale(root, kver, spec.module_file, spec.module_name, spec.component):
                affected_kernels.add(kver)

    check_kernels = set(affected_kernels)
    if check_running_initramfs and (root / "boot" / f"initramfs-{running_kernel}.img").exists():
        check_kernels.add(running_kernel)

    for kver in sorted(check_kernels):
        k_errs: list[str] = []
        index_ok = True
        if kver in affected_kernels:
            depmod_bin = shutil.which("depmod") or "depmod"
            res = runner([depmod_bin, "-a", kver])
            if res.returncode != 0:
                index_ok = False
                k_errs.append(f"depmod -a {kver} failed (exit {res.returncode})")
            elif _modules_dep_stale(root, kver, spec.module_file, spec.module_name, spec.component):
                index_ok = False
                k_errs.append(
                    f"Stale {spec.module_file} or {spec.module_name} entry remained in /usr/lib/modules/{kver} index after depmod"
                )

            if spec.component == "rgb-driver" and kver == running_kernel:
                modprobe_bin = shutil.which("modprobe")
                if modprobe_bin is None:
                    index_ok = False
                    k_errs.append("modprobe unavailable to verify WMI alias clearance")
                else:
                    probe = runner([modprobe_bin, "-R", WMI_ALIAS])
                    if probe.returncode != 0:
                        index_ok = False
                        k_errs.append(f"modprobe -R verification failed for {WMI_ALIAS}")
                    elif any(spec.module_name in line.split() for line in probe.stdout.splitlines()):
                        index_ok = False
                        k_errs.append(f"WMI alias {WMI_ALIAS} still resolves to {spec.module_name}")

        prev_rec = prev_map.get(kver, {})
        prev_initramfs = prev_rec.get("initramfs_status", "not_present")
        had_blocking_obligation = (
            prev_initramfs == "exposed"
            or kver in prior_blocked
            or prev_rec.get("migration_status") == "pending"
            or (bool(prev_rec) and prev_rec.get("index_status") != "verified_clean")
        )
        initramfs_status = "not_present"
        img = root / "boot" / f"initramfs-{kver}.img"
        if img.exists():
            lsinitrd_bin = shutil.which("lsinitrd")
            if lsinitrd_bin is None or (listing := runner([lsinitrd_bin, str(img)])).returncode != 0:
                reason = "lsinitrd unavailable" if lsinitrd_bin is None else "lsinitrd failed"
                if prev_initramfs == "exposed":
                    initramfs_status = "exposed"
                    k_errs.append(f"Cannot clear prior residual initramfs exposure on {kver}: {reason}")
                elif had_blocking_obligation:
                    initramfs_status = "unverified"
                    k_errs.append(
                        f"Cannot verify clearance of interrupted blocking obligation on {kver}: {reason}"
                    )
                else:
                    initramfs_status = "unverified"
                    warnings.append(f"UNVERIFIED initramfs clearance for {img}: {reason}")
            else:
                needle = f"extra/nitro-control/{spec.module_file}"
                if needle in listing.stdout or (spec.component == "rgb-driver" and f"/{spec.module_file}" in listing.stdout):
                    initramfs_status = "exposed"
                    k_errs.append(
                        f"RESIDUAL_INITRAMFS_EXPOSURE: {spec.module_file} present in {img}; rebuild initramfs (dracut --force --kver {kver})"
                    )
                else:
                    initramfs_status = "verified_clean"

        kernel_records[kver] = {
            "index_status": "verified_clean" if index_ok else "stale_or_failed",
            "initramfs_status": initramfs_status,
            "migration_status": "pending" if k_errs else "complete",
        }
        if k_errs:
            errors_by_kernel[kver] = k_errs
    return kernel_records, errors_by_kernel, warnings


def _ensure_disabled_unit(systemctl_bin: str, runner: Runner) -> None:
    dis = runner([systemctl_bin, "disable", "nitro-control-rgb-driver.service"])
    if dis.returncode != 0:
        detail = (dis.stderr or dis.stdout).strip()
        raise ModuleStagingError(f"systemctl disable failed (exit {dis.returncode}): {detail}")
    post = runner([systemctl_bin, "is-enabled", "nitro-control-rgb-driver.service"])
    state = post.stdout.strip()
    if state in {"enabled", "enabled-runtime"} or post.returncode == 0:
        raise ModuleStagingError(
            f"Service remained enabled after systemctl disable (state={state!r}, rc={post.returncode})"
        )
    if state not in _DISABLED_STATES:
        raise ModuleStagingError(
            f"Unable to positively verify disabled service state from systemctl is-enabled (rc={post.returncode}, out={state!r})"
        )


def _driver_lib_manifest_valid(lib_dir_fd: int, lib_dir: Path, uids: set[int]) -> bool:
    if DRIVER_LIB_MANIFEST not in os.listdir(lib_dir_fd):
        return False
    _, raw = open_regular_ro(DRIVER_LIB_MANIFEST, dir_fd=lib_dir_fd, allowed_uids=uids, exact_mode=0o600,
                             label=str(lib_dir / DRIVER_LIB_MANIFEST))
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise ModuleStagingError(f"Malformed controller library manifest {lib_dir / DRIVER_LIB_MANIFEST}") from exc
    if (not isinstance(data, dict)
            or data.get("managed_by") != MANAGED_BY
            or data.get("schema_version") != SCHEMA_VERSION
            or data.get("component") != "rgb-driver-lib"
            or not isinstance(data.get("files"), dict)):
        raise ModuleStagingError(f"Invalid controller library manifest {lib_dir / DRIVER_LIB_MANIFEST}")
    files = data["files"]
    if set(files) != {"__init__.py", "module_staging.py"}:
        raise ModuleStagingError(f"Unexpected file set in controller library manifest {lib_dir / DRIVER_LIB_MANIFEST}")
    for fname, expected_sha in files.items():
        if not isinstance(expected_sha, str) or not _HEX_SHA.fullmatch(expected_sha):
            raise ModuleStagingError(f"Invalid digest entry for {fname} in controller library manifest")
        _, fbytes = open_regular_ro(fname, dir_fd=lib_dir_fd, allowed_uids=uids, exact_mode=0o644,
                                    label=str(lib_dir / fname))
        if hashlib.sha256(fbytes).hexdigest() != expected_sha:
            raise ModuleStagingError(f"Controller library digest mismatch for {lib_dir / fname}")
    return True


def _install_controller_and_unit(
    root: Path,
    controller_src: Path,
    unit_src: Path,
    *,
    adopt_legacy: bool,
    runner: Runner,
) -> tuple[bool, list[str]]:
    uids = allowed_root_uids(root)
    notes: list[str] = []
    libexec_fd = verify_dir_chain(root, ("usr", "local", "libexec"), create_from_index=1, new_dir_mode=0o755, allowed_uids=uids)
    unit_dir_fd = verify_dir_chain(root, ("etc", "systemd", "system"), create_from_index=1, new_dir_mode=0o755, allowed_uids=uids)
    try:
        ctrl_path = root / "usr/local/libexec/nitro-control-rgb-driver"
        if "nitro-control-rgb-driver" in os.listdir(libexec_fd) or ctrl_path.is_symlink():
            _, existing_ctrl = open_regular_ro("nitro-control-rgb-driver", dir_fd=libexec_fd, allowed_uids=uids, label=str(ctrl_path))
            text = existing_ctrl.decode("utf-8", errors="replace")
            if CONTROLLER_MARKER not in text:
                if not (adopt_legacy and "MODEL='Nitro AN515-58'" in text and "/run/nitro-control-rgb-driver" in text):
                    raise ModuleStagingError(f"Controller path {ctrl_path} lacks Nitro Control ownership marker; pass --adopt-legacy after inspection")

        svc_path = root / "etc/systemd/system/nitro-control-rgb-driver.service"
        if "nitro-control-rgb-driver.service" in os.listdir(unit_dir_fd) or svc_path.is_symlink():
            _, existing_svc = open_regular_ro("nitro-control-rgb-driver.service", dir_fd=unit_dir_fd, allowed_uids=uids, label=str(svc_path))
            if SERVICE_MARKER not in existing_svc.decode("utf-8", errors="replace"):
                raise ModuleStagingError(f"Service path {svc_path} is occupied by an unrelated unit; refusing overwrite")

        ctrl_bytes = _read_source_bytes(controller_src.resolve(), reject_world_write=True)
        unit_bytes = _read_source_bytes(unit_src.resolve(), reject_world_write=True)

        lib_fd = verify_dir_chain(
            root, ("usr", "local", "libexec", "nitro-control-rgb-driver-lib", "nitro_control"),
            create_from_index=3, new_dir_mode=0o755, allowed_uids=uids,
        )
        try:
            lib_dir = root / "usr/local/libexec/nitro-control-rgb-driver-lib/nitro_control"
            existing_lib_entries = set(os.listdir(lib_fd))
            if existing_lib_entries & {"__init__.py", "module_staging.py", DRIVER_LIB_MANIFEST}:
                if not _driver_lib_manifest_valid(lib_fd, lib_dir, uids):
                    if not adopt_legacy:
                        raise ModuleStagingError(
                            f"Existing driver library files in {lib_dir} lack Nitro provenance manifest; pass --adopt-legacy"
                        )
                    for fname in ("__init__.py", "module_staging.py"):
                        if fname in existing_lib_entries or (lib_dir / fname).is_symlink():
                            open_regular_ro(fname, dir_fd=lib_fd, allowed_uids=uids, label=str(lib_dir / fname))

            pkg_root = Path(__file__).resolve().parent
            lib_hashes: dict[str, str] = {}
            for mod_name in ("__init__.py", "module_staging.py"):
                src_bytes = _read_source_bytes((pkg_root / mod_name).resolve(), reject_world_write=True)
                lib_hashes[mod_name] = hashlib.sha256(src_bytes).hexdigest()
                tmp_py = f".{mod_name}.tmp.{os.getpid()}"
                if (lib_dir / tmp_py).exists() or (lib_dir / tmp_py).is_symlink():
                    os.unlink(tmp_py, dir_fd=lib_fd)
                _write_private_file(lib_fd, tmp_py, src_bytes, 0o644)
                os.replace(lib_dir / tmp_py, lib_dir / mod_name)

            lib_manifest = {
                "managed_by": MANAGED_BY,
                "schema_version": SCHEMA_VERSION,
                "component": "rgb-driver-lib",
                "files": lib_hashes,
            }
            tmp_man = f".{DRIVER_LIB_MANIFEST}.tmp.{os.getpid()}"
            if (lib_dir / tmp_man).exists() or (lib_dir / tmp_man).is_symlink():
                os.unlink(tmp_man, dir_fd=lib_fd)
            _write_private_file(lib_fd, tmp_man, (json.dumps(lib_manifest, sort_keys=True) + "\n").encode("utf-8"), 0o600)
            os.replace(lib_dir / tmp_man, lib_dir / DRIVER_LIB_MANIFEST)
            _fsync_dir(lib_fd)
        finally:
            os.close(lib_fd)

        tmp_ctrl = f".nitro-control-rgb-driver.tmp.{os.getpid()}"
        _write_private_file(libexec_fd, tmp_ctrl, ctrl_bytes, 0o755)
        tmp_unit = f".nitro-control-rgb-driver.service.tmp.{os.getpid()}"
        _write_private_file(unit_dir_fd, tmp_unit, unit_bytes, 0o644)
        if shutil.which("restorecon"):
            runner(["restorecon", "-F", str(root / "usr/local/libexec" / tmp_ctrl), str(root / "etc/systemd/system" / tmp_unit)])
        os.replace(root / "usr/local/libexec" / tmp_ctrl, ctrl_path)
        os.replace(root / "etc/systemd/system" / tmp_unit, svc_path)
    finally:
        os.close(libexec_fd)
        os.close(unit_dir_fd)

    systemctl_bin = shutil.which("systemctl")
    if systemctl_bin is None:
        raise ModuleStagingError("systemctl is unavailable to verify service disablement")
    was_enabled = runner([systemctl_bin, "is-enabled", "--quiet", "nitro-control-rgb-driver.service"]).returncode == 0
    if runner([systemctl_bin, "daemon-reload"]).returncode != 0:
        raise ModuleStagingError("systemctl daemon-reload failed")
    _ensure_disabled_unit(systemctl_bin, runner)
    if was_enabled:
        notes.append("NOTICE: nitro-control-rgb-driver.service was previously enabled; boot startup has been reset to DISABLED.")
    notes.append("Service status verified: disabled (boot autoload OFF).")
    return was_enabled, notes


def _persist_verified_pending(
    root: Path,
    spec: ComponentSpec,
    *,
    kernel_records: dict[str, dict[str, str]],
    errors_by_kernel: dict[str, str],
) -> None:
    pending_now, _, _, _ = _derive_migration_summary(kernel_records, errors_by_kernel)
    if pending_now:
        _save_migration_state(
            root, spec, kernel_records=kernel_records, errors_by_kernel=errors_by_kernel
        )
        _sync_clearance_sentinels(root, spec, set(pending_now))


def _merge_migration_results(
    kernel_records: dict[str, dict[str, str]],
    error_map: dict[str, str],
    idx_errors_by_kver: dict[str, list[str]],
    errors_list: list[str],
) -> None:
    for kver, msgs in idx_errors_by_kver.items():
        joined = "; ".join(msgs)
        errors_list.extend(msgs)
        error_map[kver] = f"{error_map[kver]}; {joined}" if kver in error_map else joined
    for kver in error_map:
        rec = kernel_records.setdefault(
            kver,
            {"index_status": "stale_or_failed", "initramfs_status": "not_present", "migration_status": "pending"},
        )
        rec["migration_status"] = "pending"


def stage_install(
    root: Path,
    component: str,
    kernel: str,
    source_ko: Path,
    *,
    adopt_legacy: bool = False,
    runner: Runner = _default_runner,
    controller_src: Path | None = None,
    unit_src: Path | None = None,
    on_commit_stage: StageHook | None = None,
    on_migration_stage: StageHook | None = None,
) -> dict[str, object]:
    spec = COMPONENT_SPECS[component]
    kernel = validate_kernel_name(kernel)
    root = Path(root)
    busy_msg = ("Another native RGB operation is in progress; retry after it finishes."
                if component == "native-rgb"
                else "Another RGB driver operation is active; retry after it finishes.")

    with acquire_private_locks(root, (spec.lock_name,), busy_msg):
        if _module_active(root, spec.module_name):
            raise ModuleStagingError(f"Module {spec.module_name} is currently loaded; stop it before installing")
        other_name = COMPONENT_SPECS["rgb-driver" if component == "native-rgb" else "native-rgb"].module_name
        if _module_active(root, other_name):
            raise ModuleStagingError(f"Conflicting module {other_name} is active; stop it first")

        payload = _read_source_bytes(source_ko)
        target_sha = hashlib.sha256(payload).hexdigest()
        if component == "native-rgb":
            _check_native_module_params(source_ko, runner)

        baseline_state = _load_migration_state(root, spec)
        was_previously_blocked = _unresolved_blocking_kernels(baseline_state, root, spec)
        prior_kernels = dict(baseline_state["kernels"])
        prior_errors = dict(baseline_state["errors"])
        affected, pre_pending = _collect_verification_targets(
            root, spec, kernel, prior_state=baseline_state, include_private_staged=False
        )
        pending_journal: set[str] = set(pre_pending)

        cur_fd, cur_dir = _ensure_private_kernel_dir(root, kernel)
        try:
            state, cur_sha = _inspect_location(root, cur_fd, cur_dir, spec, kernel, runner=runner, allow_adopt=adopt_legacy)
            if state == "unadopted":
                raise ModuleStagingError(
                    f"Existing unmanifested artifact at {cur_dir / spec.module_file}; pass --adopt-legacy after inspection"
                )
            # Persist all discovered verification obligations BEFORE mutating private staging or indexes.
            if pre_pending:
                _persist_pending_migration_step(
                    root,
                    spec,
                    pending_kernels=pending_journal,
                    prior_kernels=prior_kernels,
                    errors_by_kernel=prior_errors,
                    on_stage=on_migration_stage,
                )
            if state == "managed" and cur_sha == target_sha:
                if component == "native-rgb":
                    _check_native_module_params(cur_dir / spec.module_file, runner)
            elif state in {"absent", "managed", "adoptable", "interrupted"}:
                _commit_module_transaction(
                    root, cur_fd, cur_dir, spec, kernel, payload, target_sha, cur_sha,
                    runner=runner, on_stage=on_commit_stage,
                )
        finally:
            os.close(cur_fd)

        notes: list[str] = []
        migration_errors: list[str] = []
        error_map: dict[str, str] = {}
        legacy_root = root / "usr/lib/modules"
        if legacy_root.exists():
            for kdir in sorted(legacy_root.iterdir(), key=lambda p: p.name):
                if kdir.is_symlink() or not kdir.is_dir():
                    continue
                kver = kdir.name
                if not (kdir / "extra/nitro-control").exists() and not (kdir / "extra/nitro-control").is_symlink():
                    continue
                try:
                    kver = validate_kernel_name(kver)
                    leg_fd = verify_dir_chain(
                        root, ("usr", "lib", "modules", kver, "extra", "nitro-control"), allowed_uids=allowed_root_uids(root)
                    )
                except (ValueError, ModuleStagingError) as exc:
                    migration_errors.append(str(exc))
                    error_map[kver] = str(exc)
                    affected.add(kver)
                    pending_journal.add(kver)
                    continue

                leg_dir = kdir / "extra/nitro-control"
                try:
                    leg_state, leg_sha = _inspect_location(
                        root, leg_fd, leg_dir, spec, kver, runner=runner, allow_adopt=adopt_legacy
                    )
                except ModuleStagingError as exc:
                    os.close(leg_fd)
                    migration_errors.append(str(exc))
                    error_map[kver] = str(exc)
                    affected.add(kver)
                    pending_journal.add(kver)
                    continue

                try:
                    if leg_state == "absent":
                        _rmdir_if_empty(root, kdir)
                        continue
                    if leg_state == "unadopted":
                        msg = f"Preserved legacy unmanifested artifact at {leg_dir / spec.module_file}; pass --adopt-legacy to adopt/migrate"
                        migration_errors.append(msg)
                        error_map[kver] = msg
                        affected.add(kver)
                        pending_journal.add(kver)
                        continue
                    if kver != kernel:
                        ok = _migrate_other_kernel(
                            root, spec, kver, leg_fd, leg_dir, leg_sha, runner=runner, adopt_legacy=adopt_legacy
                        )
                        if not ok[0]:
                            migration_errors.append(ok[1])
                            error_map[kver] = ok[1]
                            affected.add(kver)
                            pending_journal.add(kver)
                            continue
                    # Persist migration journal AND clearance sentinel BEFORE unlinking legacy files.
                    affected.add(kver)
                    pending_journal.add(kver)
                    _persist_pending_migration_step(
                        root,
                        spec,
                        pending_kernels=pending_journal,
                        prior_kernels=prior_kernels,
                        errors_by_kernel={**prior_errors, **error_map},
                        on_stage=on_migration_stage,
                    )
                    for fname in (spec.module_file, f"{spec.module_file}.sha256", manifest_name(spec.module_file)):
                        if (leg_dir / fname).exists():
                            os.unlink(fname, dir_fd=leg_fd)
                    _fsync_dir(leg_fd)
                    if on_migration_stage is not None:
                        on_migration_stage("after_legacy_unlink")
                finally:
                    os.close(leg_fd)
                _rmdir_if_empty(root, kdir)

        kernel_records, idx_errors_by_kver, idx_warnings = _refresh_and_verify_indexes(
            root,
            spec,
            affected,
            kernel,
            prior_kernels=prior_kernels,
            prior_blocking_kernels=was_previously_blocked,
            check_running_initramfs=True,
            runner=runner,
        )
        _merge_migration_results(kernel_records, error_map, idx_errors_by_kver, migration_errors)
        _persist_verified_pending(
            root, spec, kernel_records=kernel_records, errors_by_kernel=error_map
        )
        if on_migration_stage is not None:
            on_migration_stage("after_index_refresh")

        qual_state = _finalize_migration_state(
            root,
            spec,
            kernel_records=kernel_records,
            errors_by_kernel=error_map,
            on_stage=on_migration_stage,
        )

        if migration_errors:
            raise ModuleStagingError("; ".join(migration_errors))

        other_incompatible: list[str] = []
        if component == "native-rgb":
            compat = check_artifact_compatibility(root, component, runner=runner)
            other_incompatible = [k for k in compat["incompatible_kernels"] if k != kernel]
            for other_kver in other_incompatible:
                notes.append(
                    f"NOTICE: Staged native RGB module for other kernel {other_kver} exposes "
                    "experimental parameters and will be refused at runtime until rebuilt without "
                    "EXPERIMENTAL=1 or uninstalled."
                )

        if component == "rgb-driver":
            if controller_src is None or unit_src is None:
                raise ModuleStagingError("controller_src and unit_src are required for rgb-driver")
            _, svc_notes = _install_controller_and_unit(
                root, controller_src, unit_src, adopt_legacy=adopt_legacy, runner=runner
            )
            notes.extend(svc_notes)
        return {
            "staged": str(cur_dir / spec.module_file),
            "sha256": target_sha,
            "affected_kernels": sorted(affected),
            "warnings": idx_warnings,
            "notes": notes,
            "qualification_state": qual_state,
            "incompatible_kernels": other_incompatible,
        }


def _migrate_other_kernel(
    root: Path,
    spec: ComponentSpec,
    kver: str,
    leg_fd: int,
    leg_dir: Path,
    leg_sha: str | None,
    *,
    runner: Runner,
    adopt_legacy: bool,
) -> tuple[bool, str]:
    assert leg_sha is not None
    priv_fd, priv_dir = _ensure_private_kernel_dir(root, kver)
    try:
        priv_state, priv_sha = _inspect_location(
            root, priv_fd, priv_dir, spec, kver, runner=runner, allow_adopt=adopt_legacy
        )
        if priv_state == "unadopted":
            return False, f"Unmanifested private module exists for {kver}; refusing overwrite during legacy migration"
        if priv_state in {"managed", "adoptable"} and priv_sha != leg_sha:
            return False, f"Conflicting valid modules for {kver}: private {priv_sha} differs from legacy {leg_sha}; preserving both"
        if priv_state == "managed" and priv_sha == leg_sha:
            if spec.component == "native-rgb":
                _check_native_module_params(priv_dir / spec.module_file, runner)
            return True, ""
        if spec.component == "native-rgb":
            _check_native_module_params(leg_dir / spec.module_file, runner)
        _, leg_bytes = open_regular_ro(
            spec.module_file, dir_fd=leg_fd, allowed_uids=allowed_root_uids(root), label=str(leg_dir / spec.module_file)
        )
        _commit_module_transaction(root, priv_fd, priv_dir, spec, kver, leg_bytes, leg_sha, priv_sha, runner=runner)
        return True, ""
    except ModuleStagingError as exc:
        return False, str(exc)
    finally:
        os.close(priv_fd)


def _rmdir_if_empty(root: Path, kdir: Path) -> None:
    for d in (kdir / "extra/nitro-control", kdir / "extra"):
        try:
            if d.exists() and not d.is_symlink():
                d.rmdir()
        except OSError:
            pass


def _module_active(root: Path, module_name: str) -> bool:
    if (root / "sys/module" / module_name).exists():
        return True
    proc_mods = root / "proc/modules"
    if proc_mods.is_file() and not proc_mods.is_symlink():
        try:
            for line in proc_mods.read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.startswith(f"{module_name} "):
                    return True
        except OSError:
            pass
    return False


def uninstall_modules(
    root: Path,
    component: str,
    kernel: str,
    *,
    adopt_legacy: bool = False,
    runner: Runner = _default_runner,
    on_migration_stage: StageHook | None = None,
) -> dict[str, object]:
    spec = COMPONENT_SPECS[component]
    kernel = validate_kernel_name(kernel)
    root = Path(root)
    busy_msg = ("Another native RGB operation is active; retry after it finishes."
                if component == "native-rgb"
                else "Another RGB driver operation is active; retry after it finishes.")

    if component == "rgb-driver":
        uids = allowed_root_uids(root)
        svc_path = root / "etc/systemd/system/nitro-control-rgb-driver.service"
        if svc_path.is_symlink():
            raise ModuleStagingError(f"Refusing symlinked service path {svc_path}")
        if svc_path.exists():
            unit_dir_fd = verify_dir_chain(root, ("etc", "systemd", "system"), allowed_uids=uids)
            try:
                _, existing_svc = open_regular_ro(
                    "nitro-control-rgb-driver.service", dir_fd=unit_dir_fd,
                    allowed_uids=uids, label=str(svc_path),
                )
            finally:
                os.close(unit_dir_fd)
            if SERVICE_MARKER not in existing_svc.decode("utf-8", errors="replace"):
                raise ModuleStagingError(f"Unknown service file at {svc_path}; refusing removal")

        ctrl_path = root / "usr/local/libexec/nitro-control-rgb-driver"
        if ctrl_path.is_symlink():
            raise ModuleStagingError(f"Refusing symlinked controller path {ctrl_path}")
        if ctrl_path.exists():
            ctrl_dir_fd = verify_dir_chain(root, ("usr", "local", "libexec"), allowed_uids=uids)
            try:
                _, existing_ctrl = open_regular_ro(
                    "nitro-control-rgb-driver", dir_fd=ctrl_dir_fd,
                    allowed_uids=uids, label=str(ctrl_path),
                )
            finally:
                os.close(ctrl_dir_fd)
            text = existing_ctrl.decode("utf-8", errors="replace")
            if CONTROLLER_MARKER not in text:
                if not (adopt_legacy and "MODEL='Nitro AN515-58'" in text and "/run/nitro-control-rgb-driver" in text):
                    raise ModuleStagingError(f"Controller at {ctrl_path} lacks Nitro ownership marker; pass --adopt-legacy")

        systemctl_bin = shutil.which("systemctl")
        if svc_path.exists():
            if systemctl_bin is None:
                raise ModuleStagingError("systemctl is unavailable to stop or verify disablement of nitro-control-rgb-driver.service")
            if runner([systemctl_bin, "is-active", "--quiet", "nitro-control-rgb-driver.service"]).returncode == 0:
                stop_res = runner([systemctl_bin, "stop", "nitro-control-rgb-driver.service"])
                if stop_res.returncode != 0:
                    raise ModuleStagingError("Failed to stop active nitro-control-rgb-driver.service")
            _ensure_disabled_unit(systemctl_bin, runner)
        elif systemctl_bin and runner([systemctl_bin, "is-active", "--quiet", "nitro-control-rgb-driver.service"]).returncode == 0:
            stop_res = runner([systemctl_bin, "stop", "nitro-control-rgb-driver.service"])
            if stop_res.returncode != 0:
                raise ModuleStagingError("Failed to stop active nitro-control-rgb-driver.service")

    with acquire_private_locks(root, (spec.lock_name,), busy_msg):
        if component == "rgb-driver":
            systemctl_bin = shutil.which("systemctl")
            if systemctl_bin and runner([systemctl_bin, "is-active", "--quiet", "nitro-control-rgb-driver.service"]).returncode == 0:
                raise ModuleStagingError("Legacy RGB driver service is still active; refusing removal")
        if _module_active(root, spec.module_name):
            raise ModuleStagingError(f"{spec.module_name} is currently loaded; stop/unload it before uninstalling")

        baseline_state = _load_migration_state(root, spec)
        was_previously_blocked = _unresolved_blocking_kernels(baseline_state, root, spec)
        prior_kernels = dict(baseline_state["kernels"])
        prior_errors = dict(baseline_state["errors"])

        errors: list[str] = []
        error_map: dict[str, str] = {}
        removed = False
        affected, pre_pending = _collect_verification_targets(
            root, spec, kernel, prior_state=baseline_state, include_private_staged=True
        )
        pending_journal: set[str] = set(pre_pending)

        if component == "rgb-driver":
            modprobe_bin = shutil.which("modprobe")
            if modprobe_bin is None or runner([modprobe_bin, "acer_wmi"]).returncode != 0:
                raise ModuleStagingError("Unable to verify or load stock acer_wmi")
            errors.extend(_remove_unit_and_controller(root, adopt_legacy=adopt_legacy, runner=runner))

        priv_root = root.joinpath(*PRIVATE_MODULES_REL)
        if pre_pending:
            _persist_pending_migration_step(
                root,
                spec,
                pending_kernels=pending_journal,
                prior_kernels=prior_kernels,
                errors_by_kernel=prior_errors,
                on_stage=on_migration_stage,
            )

        if priv_root.exists():
            if priv_root.is_symlink():
                errors.append(f"Refusing symlinked directory {priv_root}")
            else:
                for kdir in sorted(priv_root.iterdir(), key=lambda p: p.name):
                    try:
                        kver = validate_kernel_name(kdir.name)
                        fd = verify_dir_chain(root, (*PRIVATE_MODULES_REL, kver), allowed_uids=allowed_root_uids(root))
                    except (ValueError, ModuleStagingError) as exc:
                        errors.append(str(exc))
                        continue
                    try:
                        if _cleanup_single_dir(root, fd, kdir, spec, kver, runner=runner, adopt_legacy=adopt_legacy):
                            removed = True
                            affected.add(kver)
                            pending_journal.add(kver)
                    except ModuleStagingError as exc:
                        errors.append(str(exc))
                        error_map[kver] = str(exc)
                        pending_journal.add(kver)
                    finally:
                        os.close(fd)

        leg_root = root / "usr/lib/modules"
        if leg_root.exists():
            for kdir in sorted(leg_root.iterdir(), key=lambda p: p.name):
                if kdir.is_symlink() or not kdir.is_dir():
                    continue
                if not (kdir / "extra/nitro-control").exists() and not (kdir / "extra/nitro-control").is_symlink():
                    continue
                kver = kdir.name
                try:
                    kver = validate_kernel_name(kver)
                    fd = verify_dir_chain(root, ("usr", "lib", "modules", kver, "extra", "nitro-control"), allowed_uids=allowed_root_uids(root))
                except (ValueError, ModuleStagingError) as exc:
                    errors.append(str(exc))
                    error_map[kver] = str(exc)
                    affected.add(kver)
                    pending_journal.add(kver)
                    continue
                try:
                    affected.add(kver)
                    pending_journal.add(kver)
                    _persist_pending_migration_step(
                        root,
                        spec,
                        pending_kernels=pending_journal,
                        prior_kernels=prior_kernels,
                        errors_by_kernel={**prior_errors, **error_map},
                        on_stage=on_migration_stage,
                    )
                    if _cleanup_single_dir(
                        root, fd, kdir / "extra/nitro-control", spec, kver, runner=runner, adopt_legacy=adopt_legacy
                    ):
                        removed = True
                    if on_migration_stage is not None:
                        on_migration_stage("after_legacy_unlink")
                except ModuleStagingError as exc:
                    errors.append(str(exc))
                    error_map[kver] = str(exc)
                finally:
                    os.close(fd)
                _rmdir_if_empty(root, kdir)

        kernel_records, idx_errors_by_kver, idx_warnings = _refresh_and_verify_indexes(
            root,
            spec,
            affected,
            kernel,
            prior_kernels=prior_kernels,
            prior_blocking_kernels=was_previously_blocked,
            check_running_initramfs=True,
            runner=runner,
        )
        _merge_migration_results(kernel_records, error_map, idx_errors_by_kver, errors)
        _persist_verified_pending(
            root, spec, kernel_records=kernel_records, errors_by_kernel=error_map
        )
        if on_migration_stage is not None:
            on_migration_stage("after_index_refresh")

        qual_state = _finalize_migration_state(
            root,
            spec,
            kernel_records=kernel_records,
            errors_by_kernel=error_map,
            on_stage=on_migration_stage,
        )

        if priv_root.exists() and not priv_root.is_symlink():
            for kdir in sorted(priv_root.iterdir(), key=lambda p: p.name):
                try:
                    if kdir.is_dir() and not kdir.is_symlink():
                        kdir.rmdir()
                except OSError:
                    pass
            for d in (priv_root, root / "usr/local/lib/nitro-control"):
                try:
                    if d.exists() and not d.is_symlink():
                        d.rmdir()
                except OSError:
                    pass

        if errors:
            raise ModuleStagingError("; ".join(errors))
        return {
            "removed": removed,
            "affected_kernels": sorted(affected),
            "warnings": idx_warnings,
            "qualification_state": qual_state,
        }


def _cleanup_single_dir(
    root: Path,
    dir_fd: int,
    dir_path: Path,
    spec: ComponentSpec,
    kernel: str,
    *,
    runner: Runner,
    adopt_legacy: bool,
) -> bool:
    state, _ = _inspect_location(root, dir_fd, dir_path, spec, kernel, runner=runner, allow_adopt=adopt_legacy)
    if state == "absent":
        return False
    if state == "unadopted":
        raise ModuleStagingError(
            f"Refusing removal of unmanifested artifact in {dir_path}; pass --adopt-legacy after verification"
        )
    if state == "interrupted":
        _clear_tx_artifacts(root, dir_fd, dir_path, spec)
    removed = False
    for fname in (spec.module_file, f"{spec.module_file}.sha256", manifest_name(spec.module_file)):
        if (dir_path / fname).exists():
            os.unlink(fname, dir_fd=dir_fd)
            removed = True
    _fsync_dir(dir_fd)
    return removed


def _remove_unit_and_controller(root: Path, *, adopt_legacy: bool, runner: Runner) -> list[str]:
    uids = allowed_root_uids(root)
    errors: list[str] = []
    svc = root / "etc/systemd/system/nitro-control-rgb-driver.service"
    unit_removed = False
    if svc.exists() or svc.is_symlink():
        try:
            ufd = verify_dir_chain(root, ("etc", "systemd", "system"), allowed_uids=uids)
            try:
                _, raw = open_regular_ro("nitro-control-rgb-driver.service", dir_fd=ufd, allowed_uids=uids, label=str(svc))
                if SERVICE_MARKER not in raw.decode("utf-8", errors="replace"):
                    raise ModuleStagingError(f"Unknown service file at {svc}; refusing removal")
                os.unlink("nitro-control-rgb-driver.service", dir_fd=ufd)
                unit_removed = True
            finally:
                os.close(ufd)
        except ModuleStagingError as exc:
            errors.append(str(exc))

    ctrl = root / "usr/local/libexec/nitro-control-rgb-driver"
    if ctrl.exists() or ctrl.is_symlink():
        try:
            cfd = verify_dir_chain(root, ("usr", "local", "libexec"), allowed_uids=uids)
            try:
                _, raw = open_regular_ro("nitro-control-rgb-driver", dir_fd=cfd, allowed_uids=uids, label=str(ctrl))
                text = raw.decode("utf-8", errors="replace")
                if CONTROLLER_MARKER not in text:
                    if not (adopt_legacy and "MODEL='Nitro AN515-58'" in text and "/run/nitro-control-rgb-driver" in text):
                        raise ModuleStagingError(f"Controller at {ctrl} lacks Nitro ownership marker; pass --adopt-legacy")
                os.unlink("nitro-control-rgb-driver", dir_fd=cfd)
            finally:
                os.close(cfd)
        except ModuleStagingError as exc:
            errors.append(str(exc))

    lib_root = root / "usr/local/libexec/nitro-control-rgb-driver-lib"
    if lib_root.exists() or lib_root.is_symlink():
        try:
            lfd = verify_dir_chain(root, ("usr", "local", "libexec", "nitro-control-rgb-driver-lib", "nitro_control"), allowed_uids=uids)
            try:
                lib_pkg_dir = lib_root / "nitro_control"
                entries = set(os.listdir(lfd))
                if entries & {"__init__.py", "module_staging.py", DRIVER_LIB_MANIFEST}:
                    if not _driver_lib_manifest_valid(lfd, lib_pkg_dir, uids):
                        if not adopt_legacy:
                            raise ModuleStagingError(
                                f"Driver library in {lib_pkg_dir} lacks Nitro provenance manifest; pass --adopt-legacy"
                            )
                        for fname in ("__init__.py", "module_staging.py"):
                            if fname in entries or (lib_pkg_dir / fname).is_symlink():
                                _, fbytes = open_regular_ro(fname, dir_fd=lfd, allowed_uids=uids, exact_mode=0o644,
                                                            label=str(lib_pkg_dir / fname))
                                marker = b"Nitro Control" if fname == "__init__.py" else b"nitro_control.module_staging"
                                if marker not in fbytes:
                                    raise ModuleStagingError(f"Unrecognized file {lib_pkg_dir / fname}; preserving")
                    for fname in ("__init__.py", "module_staging.py", DRIVER_LIB_MANIFEST):
                        if (lib_pkg_dir / fname).exists():
                            os.unlink(fname, dir_fd=lfd)
                    _fsync_dir(lfd)
            finally:
                os.close(lfd)
            for d in (lib_root / "nitro_control", lib_root):
                try:
                    if d.exists() and not d.is_symlink():
                        d.rmdir()
                except OSError:
                    pass
        except ModuleStagingError as exc:
            errors.append(str(exc))

    systemctl_bin = shutil.which("systemctl")
    if systemctl_bin and (unit_removed or (root / "etc/systemd/system").exists()):
        if runner([systemctl_bin, "daemon-reload"]).returncode != 0:
            errors.append("systemctl daemon-reload failed")
    return errors


def check_artifact_compatibility(
    root: Path,
    component: str,
    *,
    runner: Runner = _default_runner,
) -> dict[str, object]:
    """Report per-kernel artifact parameter compatibility in private staging without blocking boot-safety qualification."""
    spec = COMPONENT_SPECS[component]
    root = Path(root)
    compatible: list[str] = []
    incompatible: dict[str, str] = {}
    unverified: dict[str, str] = {}
    statuses: dict[str, str] = {}
    priv_root = root.joinpath(*PRIVATE_MODULES_REL)
    if priv_root.exists() and not priv_root.is_symlink() and priv_root.is_dir():
        for kdir in sorted(priv_root.iterdir(), key=lambda p: p.name):
            if kdir.is_symlink() or not kdir.is_dir():
                continue
            mod = kdir / spec.module_file
            if not (mod.exists() or mod.is_symlink()):
                continue
            if mod.is_symlink() or not mod.is_file():
                unverified[kdir.name] = f"Unsafe artifact entry {mod}"
                statuses[kdir.name] = "failed"
                continue
            if component != "native-rgb":
                compatible.append(kdir.name)
                statuses[kdir.name] = "compatible"
                continue
            insp = _inspect_native_param_metadata(mod, runner)
            statuses[kdir.name] = insp.status
            if insp.status == "compatible":
                compatible.append(kdir.name)
            elif insp.status == "incompatible":
                incompatible[kdir.name] = insp.detail
            else:
                unverified[kdir.name] = insp.detail
    return {
        "compatible_kernels": compatible,
        "incompatible_kernels": sorted(incompatible),
        "unverified_kernels": sorted(unverified),
        "statuses": statuses,
        "errors": {**incompatible, **unverified},
    }


def _check_unprivileged_staging_files(root: Path, component: str, kernel: str) -> tuple[bool, str | None, Path]:
    spec = COMPONENT_SPECS[component]
    kernel = validate_kernel_name(kernel)
    priv_dir = root.joinpath(*PRIVATE_MODULES_REL, kernel)
    ancestors = [
        root / "usr",
        root / "usr/local",
        root / "usr/local/lib",
        root / "usr/local/lib/nitro-control",
        root / "usr/local/lib/nitro-control/modules",
        priv_dir,
    ]
    for d in ancestors:
        if d.is_symlink() or (d.exists() and not d.is_dir()):
            reason = ("Native RGB staging directory is unsafe" if component == "native-rgb"
                      else f"{component} staging directory is unsafe")
            return False, reason, priv_dir / spec.module_file

    mod = priv_dir / spec.module_file
    digest = priv_dir / f"{spec.module_file}.sha256"
    man = priv_dir / manifest_name(spec.module_file)
    tx_j = priv_dir / tx_json_name(spec.module_file)
    tx_d = priv_dir / tx_dir_name(spec.module_file)
    sentinel = priv_dir / clearance_sentinel_name(spec.module_file)
    leg_dir = root / "usr/lib/modules" / kernel / "extra/nitro-control"
    leg_mod = leg_dir / spec.module_file
    leg_sha = leg_dir / f"{spec.module_file}.sha256"

    if tx_j.exists() or tx_j.is_symlink() or tx_d.exists() or tx_d.is_symlink():
        msg = ("Interrupted native RGB installation detected; re-run sudo ./scripts/install-native-rgb-fedora.sh to recover"
               if component == "native-rgb"
               else f"Interrupted {component} installation detected; re-run installer to recover")
        return False, msg, mod
    if sentinel.exists() or sentinel.is_symlink():
        msg = (f"Unresolved migration or clearance state for {kernel}; re-run sudo ./scripts/install-native-rgb-fedora.sh after fixing indexes/initramfs"
               if component == "native-rgb"
               else f"Unresolved migration or clearance state for {kernel}; re-run installer")
        return False, msg, mod
    if leg_mod.exists() or leg_mod.is_symlink() or leg_sha.exists() or leg_sha.is_symlink():
        msg = ("Unresolved legacy indexed native RGB module detected; re-run sudo ./scripts/install-native-rgb-fedora.sh to migrate to private staging"
               if component == "native-rgb"
               else f"Unresolved legacy indexed {component} module detected; re-run installer to migrate")
        return False, msg, mod
    if _modules_dep_stale(root, kernel, spec.module_file, spec.module_name, spec.component):
        msg = ("Stale kernel module index references legacy native RGB path; refresh depmod via installer"
               if component == "native-rgb"
               else f"Stale kernel module index references legacy {component} path; refresh depmod via installer")
        return False, msg, mod
    if not mod.is_file() or mod.is_symlink() or not digest.is_file() or digest.is_symlink():
        msg = ("Install the opt-in native RGB module for this kernel"
               if component == "native-rgb"
               else f"Install the opt-in {component} module for this kernel")
        return False, msg, mod
    if not man.is_file() or man.is_symlink():
        msg = ("Native RGB staging manifest missing or unsafe; re-run sudo ./scripts/install-native-rgb-fedora.sh"
               if component == "native-rgb"
               else f"{component} staging manifest missing or unsafe; re-run installer")
        return False, msg, mod
    return True, None, mod


def inspect_native_unprivileged_readiness(
    root: Path,
    kernel: str,
    *,
    runner: Runner | None = None,
    timeout: int = METADATA_INSPECT_TIMEOUT,
) -> tuple[bool, str | None, NativeParamInspection | None]:
    """Check 0755/0644 unprivileged native RGB readiness plus bounded metadata inspection."""
    ok_files, reason, mod = _check_unprivileged_staging_files(root, "native-rgb", kernel)
    if not ok_files:
        return False, reason, None

    insp = _inspect_native_param_metadata(mod, runner, timeout=timeout)
    if insp.status == "compatible":
        return True, None, insp
    if insp.status == "incompatible":
        return False, (
            "Staged native RGB module exposes experimental diagnostic parameters; "
            "rebuild without EXPERIMENTAL=1 and re-run sudo ./scripts/install-native-rgb-fedora.sh"
        ), insp
    if insp.status == "missing_tool":
        return False, (
            "Native RGB module metadata inspection unavailable: modinfo tool is missing"
        ), insp
    if insp.status == "timeout":
        return False, (
            "Native RGB module metadata inspection timed out; compatibility is unverified"
        ), insp
    return False, (
        f"Native RGB module metadata inspection failed ({insp.detail}); "
        "verify or re-run sudo ./scripts/install-native-rgb-fedora.sh"
    ), insp


def check_unprivileged_readiness(
    root: Path,
    component: str,
    kernel: str,
    *,
    runner: Runner | None = None,
    timeout: int = METADATA_INSPECT_TIMEOUT,
) -> tuple[bool, str | None]:
    """Unprivileged readiness checks using only 0755 metadata and 0644 kernel index files."""
    if component == "native-rgb":
        ready, reason, _ = inspect_native_unprivileged_readiness(root, kernel, runner=runner, timeout=timeout)
        return ready, reason
    ok_files, reason, _ = _check_unprivileged_staging_files(root, component, kernel)
    return ok_files, reason


def _atomic_write_in_dir(dir_fd: int, name: str, data: bytes, mode: int, *, uids: set[int], label: str) -> None:
    """Atomically create or replace a regular root-owned file inside verified dir_fd."""
    try:
        info = os.lstat(name, dir_fd=dir_fd)
    except FileNotFoundError:
        info = None
    if info is not None:
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise ModuleStagingError(f"Refusing non-regular or symlinked target {label}")
        if info.st_uid not in uids:
            raise ModuleStagingError(f"Refusing file owned by uid {info.st_uid} at {label}")

    fd = -1
    tmp_name = ""
    for attempt in range(32):
        candidate = f".nitro-tmp.{os.getpid()}.{attempt}.{name}"[:240]
        try:
            fd = os.open(
                candidate,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                mode,
                dir_fd=dir_fd,
            )
            tmp_name = candidate
            break
        except FileExistsError:
            continue
    if fd < 0:
        raise ModuleStagingError(f"Unable to allocate unique staging file for {label}")
    try:
        view = memoryview(data)
        total = 0
        while total < len(view):
            total += os.write(fd, view[total:])
        os.fchmod(fd, mode)
        try:
            os.fsync(fd)
        except OSError:
            pass
    except Exception:
        os.close(fd)
        try:
            os.unlink(tmp_name, dir_fd=dir_fd)
        except OSError:
            pass
        raise
    os.close(fd)
    os.replace(tmp_name, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
    _fsync_dir(dir_fd)


_PYC_NAME_RE = re.compile(r"^([A-Za-z0-9_]+)\.cpython-[0-9]+(?:\.opt-([12]))?\.pyc\Z")


def _pyc_matches_nitro_ownership(
    stem: str,
    raw: bytes,
    mode: int,
    *,
    known_sources: dict[str, tuple[bytes, ...]] | None,
    opt_level: int = 0,
    candidate_filenames: tuple[str, ...] = (),
) -> bool:
    """Verify PEP 552 header and exact bytecode/hash identity before removing a cached .pyc file."""
    if (mode & 0o022) != 0:
        return False
    if len(raw) < 16 or raw[:4] != importlib.util.MAGIC_NUMBER:
        return False
    flags = int.from_bytes(raw[4:8], "little")
    if flags not in {0, 1, 3}:
        return False
    if not known_sources or stem not in known_sources:
        return False
    candidates = known_sources[stem]
    if flags in {1, 3}:
        recorded_hash = raw[8:16]
        matching_sources = [src for src in candidates if importlib.util.source_hash(src) == recorded_hash]
    else:
        recorded_size = int.from_bytes(raw[12:16], "little")
        matching_sources = [src for src in candidates if (len(src) & 0xffffffff) == recorded_size]
    if not matching_sources:
        return False
    fnames = candidate_filenames or (f"/usr/local/libexec/nitro-control-rgb/nitro_control/{stem}.py",)
    try:
        loaded = marshal.loads(raw[16:])
    except (ValueError, EOFError, TypeError, MemoryError):
        return False
    for src in matching_sources:
        try:
            text = src.decode("utf-8")
        except UnicodeError:
            continue
        for fname in fnames:
            try:
                code = compile(text, fname, "exec", dont_inherit=True, optimize=opt_level)
            except SyntaxError:
                continue
            if isinstance(loaded, type(code)) and loaded.co_filename == fname and loaded == code:
                return True
    return False


def _clean_pycache_in_dir(
    parent_fd: int,
    parent_path: Path,
    uids: set[int],
    *,
    owned_stems: frozenset[str] = _RGB_HELPER_PY_STEMS,
    known_sources: dict[str, tuple[bytes, ...]] | None = None,
) -> None:
    """Remove only verified Nitro-owned `.pyc` entries inside `__pycache__`, preserving ambiguous/unrelated files."""
    cache_path = parent_path / "__pycache__"
    if cache_path.is_symlink():
        raise ModuleStagingError(f"Refusing symlinked cache directory {cache_path}")
    if not cache_path.exists():
        return
    cfd = verify_dir_chain(parent_path, ("__pycache__",), allowed_uids=uids)
    try:
        for entry in list(os.listdir(cfd)):
            entry_path = cache_path / entry
            match = _PYC_NAME_RE.fullmatch(entry)
            if not match:
                continue
            stem = match.group(1)
            if stem not in owned_stems:
                continue
            opt_level = int(match.group(2)) if match.group(2) else 0
            if entry_path.is_symlink():
                raise ModuleStagingError(f"Refusing symlinked bytecode cache entry {entry_path}")
            _, raw = open_regular_ro(entry, dir_fd=cfd, allowed_uids=uids, label=str(entry_path))
            mode = os.stat(entry, dir_fd=cfd, follow_symlinks=False).st_mode
            candidate_filenames = (
                str(parent_path / f"{stem}.py"),
                f"/usr/local/libexec/nitro-control-rgb/nitro_control/{stem}.py",
            )
            if not _pyc_matches_nitro_ownership(
                stem,
                raw,
                mode,
                known_sources=known_sources,
                opt_level=opt_level,
                candidate_filenames=candidate_filenames,
            ):
                continue
            os.unlink(entry, dir_fd=cfd)
        _fsync_dir(cfd)
    finally:
        os.close(cfd)
    try:
        os.rmdir("__pycache__", dir_fd=parent_fd)
    except OSError:
        pass


def _render_rgb_helper_launcher() -> bytes:
    expected_files = tuple(sorted(_RGB_HELPER_EXPECTED_KEYS))
    return (
        "#!/usr/bin/python3 -IB\n"
        f"{RGB_HELPER_LAUNCHER_MARKER}\n"
        "import fcntl, hashlib, json, os, stat, sys\n"
        "sys.dont_write_bytecode = True\n"
        "sys.implementation.cache_tag = None\n"
        'sys.path.insert(0, "/usr/local/libexec/nitro-control-rgb")\n'
        'if sys.argv[1:] == ["--capabilities"]:\n'
        "    try:\n"
        "        from nitro_control.rgb_privileged import main\n"
        "        raise SystemExit(main(sys.argv[1:]))\n"
        "    except Exception as exc:\n"
        '        print(json.dumps({"ok": False, "error": f"RGB helper installation incomplete or corrupted: {exc}"}))\n'
        "        raise SystemExit(1)\n"
        "if os.geteuid() != 0:\n"
        '    print(json.dumps({"ok": False, "error": "RGB helper requires administrator authorization"}))\n'
        "    raise SystemExit(1)\n"
        "def _open_dir_chain(parts):\n"
        "    dflags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC\n"
        '    cur = os.open("/", dflags)\n'
        "    try:\n"
        "        info = os.fstat(cur)\n"
        "        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or (info.st_mode & 0o022):\n"
        '            raise OSError("Unsafe root directory permissions")\n'
        "        for part in parts:\n"
        "            nxt = os.open(part, dflags, dir_fd=cur)\n"
        "            os.close(cur)\n"
        "            cur = nxt\n"
        "            info = os.fstat(cur)\n"
        "            if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or (info.st_mode & 0o022):\n"
        '                raise OSError(f"Unsafe directory permissions at /{\'/\' .join(parts)}")\n'
        "        res = cur\n"
        "        cur = -1\n"
        "        return res\n"
        "    finally:\n"
        "        if cur != -1:\n"
        "            os.close(cur)\n"
        "def _read_reg(dir_fd, name, label, exact_mode=None):\n"
        "    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, dir_fd=dir_fd)\n"
        "    try:\n"
        "        info = os.fstat(fd)\n"
        "        if not stat.S_ISREG(info.st_mode):\n"
        '            raise RuntimeError(f"{label} is not a regular file")\n'
        "        if info.st_nlink != 1 or info.st_uid != 0 or (info.st_mode & 0o022):\n"
        '            raise RuntimeError(f"Unsafe ownership or permissions on {label}")\n'
        "        if exact_mode is not None and stat.S_IMODE(info.st_mode) != exact_mode:\n"
        '            raise RuntimeError(f"Unexpected mode on {label}")\n'
        "        chunks = []\n"
        "        while True:\n"
        "            chunk = os.read(fd, 65536)\n"
        "            if not chunk:\n"
        "                break\n"
        "            chunks.append(chunk)\n"
        '        return b"".join(chunks)\n'
        "    finally:\n"
        "        os.close(fd)\n"
        "def _verify_helper_coherence():\n"
        f"    expected = {expected_files!r}\n"
        '    libexec_fd = _open_dir_chain(("usr", "local", "libexec"))\n'
        '    polkit_fd = _open_dir_chain(("usr", "share", "polkit-1", "actions"))\n'
        '    base_fd = _open_dir_chain(("usr", "local", "libexec", "nitro-control-rgb"))\n'
        '    pkg_fd = _open_dir_chain(("usr", "local", "libexec", "nitro-control-rgb", "nitro_control"))\n'
        "    try:\n"
        '        if os.path.islink("/usr/local/libexec/nitro-control-rgb/.tx.rgb-helper.json") or ".tx.rgb-helper.json" in os.listdir(base_fd):\n'
        '            raise RuntimeError("Interrupted RGB helper installation detected; re-run sudo ./scripts/install-rgb-helper-fedora.sh before use")\n'
        '        if os.path.islink("/usr/local/libexec/nitro-control-rgb/manifest.json") or "manifest.json" not in os.listdir(base_fd):\n'
        '            raise RuntimeError("RGB helper provenance manifest is missing or unsafe; re-run sudo ./scripts/install-rgb-helper-fedora.sh")\n'
        '        mraw = _read_reg(base_fd, "manifest.json", "/usr/local/libexec/nitro-control-rgb/manifest.json", exact_mode=0o644)\n'
        '        manifest = json.loads(mraw.decode("utf-8"))\n'
        "        if (\n"
        "            not isinstance(manifest, dict)\n"
        '            or manifest.get("managed_by") != "nitro-control"\n'
        '            or manifest.get("component") != "rgb-helper"\n'
        f'            or manifest.get("schema_version") != {SCHEMA_VERSION!r}\n'
        '            or not isinstance(manifest.get("files"), dict)\n'
        "        ):\n"
        '            raise RuntimeError("Invalid RGB helper provenance manifest")\n'
        '        files_map = manifest["files"]\n'
        "        locs = {\n"
        '            "nitro-control-rgb-helper": (libexec_fd, "nitro-control-rgb-helper", "/usr/local/libexec/nitro-control-rgb-helper"),\n'
        '            "io.github.tobiasguta.NitroControl.rgb.policy": (polkit_fd, "io.github.tobiasguta.NitroControl.rgb.policy", "/usr/share/polkit-1/actions/io.github.tobiasguta.NitroControl.rgb.policy"),\n'
        "        }\n"
        "        for key in expected:\n"
        "            if key not in files_map:\n"
        '                raise RuntimeError(f"RGB helper provenance manifest is missing required entries ({key})")\n'
        '            if key.startswith("nitro_control/"):\n'
        '                fname = key.split("/", 1)[1]\n'
        '                dfd, entry, path = pkg_fd, fname, f"/usr/local/libexec/nitro-control-rgb/nitro_control/{fname}"\n'
        "            else:\n"
        "                dfd, entry, path = locs[key]\n"
        "            if os.path.islink(path) or entry not in os.listdir(dfd):\n"
        '                raise RuntimeError(f"Incomplete RGB helper installation: missing {path}")\n'
        "            digest = hashlib.sha256(_read_reg(dfd, entry, path)).hexdigest()\n"
        "            if digest != files_map.get(key):\n"
        '                raise RuntimeError(f"RGB helper integrity mismatch at {path}; re-run sudo ./scripts/install-rgb-helper-fedora.sh")\n'
        "    finally:\n"
        "        os.close(pkg_fd)\n"
        "        os.close(base_fd)\n"
        "        os.close(polkit_fd)\n"
        "        os.close(libexec_fd)\n"
        "lock_fd = -1\n"
        "try:\n"
        "    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC\n"
        '    run_fd = os.open("/run", flags)\n'
        "    try:\n"
        "        rinfo = os.fstat(run_fd)\n"
        "        if not stat.S_ISDIR(rinfo.st_mode) or rinfo.st_uid != 0 or (rinfo.st_mode & 0o022):\n"
        '            raise OSError("Unsafe /run directory permissions")\n'
        "        try:\n"
        '            os.mkdir("nitro-control", 0o700, dir_fd=run_fd)\n'
        "        except FileExistsError:\n"
        "            pass\n"
        '        dir_fd = os.open("nitro-control", flags, dir_fd=run_fd)\n'
        "        try:\n"
        "            dinfo = os.fstat(dir_fd)\n"
        "            if not stat.S_ISDIR(dinfo.st_mode) or dinfo.st_uid != 0 or stat.S_IMODE(dinfo.st_mode) != 0o700:\n"
        '                raise OSError("Unsafe /run/nitro-control directory permissions")\n'
        '            lock_fd = os.open("rgb-helper.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, 0o600, dir_fd=dir_fd)\n'
        "            linfo = os.fstat(lock_fd)\n"
        "            if not stat.S_ISREG(linfo.st_mode) or linfo.st_uid != 0 or stat.S_IMODE(linfo.st_mode) != 0o600 or linfo.st_nlink != 1:\n"
        '                raise OSError("Unsafe /run/nitro-control/rgb-helper.lock permissions")\n'
        "        finally:\n"
        "            os.close(dir_fd)\n"
        "    finally:\n"
        "        os.close(run_fd)\n"
        "    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
        "except BlockingIOError:\n"
        "    if lock_fd != -1:\n"
        "        os.close(lock_fd)\n"
        '    print(json.dumps({"ok": False, "error": "Another RGB helper operation or installation is active; retry after it finishes."}))\n'
        "    raise SystemExit(1)\n"
        "except OSError as exc:\n"
        "    if lock_fd != -1:\n"
        "        os.close(lock_fd)\n"
        '    print(json.dumps({"ok": False, "error": f"RGB helper lock unavailable: {exc}"}))\n'
        "    raise SystemExit(1)\n"
        "try:\n"
        "    _verify_helper_coherence()\n"
        "    from nitro_control.rgb_privileged import main\n"
        "    raise SystemExit(main(sys.argv[1:], _lock_held=True))\n"
        "except SystemExit:\n"
        "    raise\n"
        "except Exception as exc:\n"
        '    print(json.dumps({"ok": False, "error": str(exc)}))\n'
        "    raise SystemExit(1)\n"
        "finally:\n"
        "    os.close(lock_fd)\n"
    ).encode("utf-8")


def _render_rgb_helper_policy(authorization: str) -> bytes:
    if authorization not in {"auth_admin", "auth_admin_keep"}:
        raise ModuleStagingError(f"Unsupported authorization mode {authorization!r}")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE policyconfig PUBLIC "-//freedesktop//DTD PolicyKit Policy Configuration 1.0//EN" '
        '"http://www.freedesktop.org/standards/PolicyKit/1.0/policyconfig.dtd">\n'
        "<policyconfig>\n"
        f"  {RGB_HELPER_POLICY_MARKER}\n"
        "    <description>Apply Acer Nitro keyboard RGB lighting</description>\n"
        "    <message>Authentication is required to change your keyboard RGB lighting.</message>\n"
        "    <defaults>\n"
        "      <allow_any>no</allow_any>\n"
        "      <allow_inactive>no</allow_inactive>\n"
        f"      <allow_active>{authorization}</allow_active>\n"
        "    </defaults>\n"
        f'    <annotate key="org.freedesktop.policykit.exec.path">{RGB_HELPER_EXEC_MARKER}</annotate>\n'
        "  </action>\n"
        "</policyconfig>\n"
    ).encode("utf-8")


def _parse_rgb_helper_manifest_bytes(raw: bytes) -> dict[str, object]:
    try:
        obj = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise ModuleStagingError("Corrupt RGB helper provenance manifest") from exc
    if (
        not isinstance(obj, dict)
        or obj.get("managed_by") != MANAGED_BY
        or obj.get("component") != "rgb-helper"
        or obj.get("schema_version") != SCHEMA_VERSION
        or type(obj.get("helper_protocol")) is not int
        or obj["helper_protocol"] < 1
        or not isinstance(obj.get("capabilities"), list)
        or not all(isinstance(c, str) for c in obj["capabilities"])
        or not isinstance(obj.get("files"), dict)
    ):
        raise ModuleStagingError("Invalid fields in RGB helper provenance manifest")
    for rel, digest in obj["files"].items():
        if not isinstance(rel, str) or not isinstance(digest, str) or not _HEX_SHA.fullmatch(digest):
            raise ModuleStagingError("Invalid file digest entry in RGB helper provenance manifest")
    missing_keys = sorted(_RGB_HELPER_EXPECTED_KEYS - set(obj["files"]))
    if missing_keys:
        raise ModuleStagingError(
            f"RGB helper provenance manifest is missing required entries ({', '.join(missing_keys)})"
        )
    return obj


def _parse_rgb_helper_tx_bytes(raw: bytes) -> dict[str, object]:
    try:
        obj = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise ModuleStagingError("Corrupt RGB helper transaction marker") from exc
    if (
        not isinstance(obj, dict)
        or obj.get("managed_by") != MANAGED_BY
        or obj.get("component") != "rgb-helper"
        or obj.get("schema_version") != SCHEMA_VERSION
        or obj.get("state") != "installing"
        or not isinstance(obj.get("target_files"), dict)
    ):
        raise ModuleStagingError("Invalid RGB helper transaction marker")
    for rel, digest in obj["target_files"].items():
        if not isinstance(rel, str) or not isinstance(digest, str) or not _HEX_SHA.fullmatch(digest):
            raise ModuleStagingError("Invalid target_files digest in RGB helper transaction marker")

    # Normalize either known_files (dict[str, list[str]]) or legacy previous_files (dict[str, str]).
    known_norm: dict[str, list[str]] = {}
    if "known_files" in obj:
        kf = obj["known_files"]
        if not isinstance(kf, dict):
            raise ModuleStagingError("Invalid known_files in RGB helper transaction marker")
        for rel, digests in kf.items():
            if (
                not isinstance(rel, str)
                or not isinstance(digests, list)
                or not all(isinstance(d, str) and _HEX_SHA.fullmatch(d) for d in digests)
            ):
                raise ModuleStagingError("Invalid known_files digest list in RGB helper transaction marker")
            known_norm[rel] = list(digests)
    elif "previous_files" in obj:
        pf = obj["previous_files"]
        if not isinstance(pf, dict):
            raise ModuleStagingError("Invalid previous_files in RGB helper transaction marker")
        for rel, digest in pf.items():
            if not isinstance(rel, str) or not isinstance(digest, str) or not _HEX_SHA.fullmatch(digest):
                raise ModuleStagingError("Invalid previous_files digest in RGB helper transaction marker")
            known_norm[rel] = [digest]
    else:
        raise ModuleStagingError("RGB helper transaction marker lacks prior provenance digests")
    obj["known_files"] = known_norm
    return obj


def _is_legacy_nitro_launcher(raw: bytes) -> bool:
    text = raw.decode("utf-8", errors="replace")
    return RGB_HELPER_LAUNCHER_MARKER in text or all(sig in text for sig in RGB_HELPER_LEGACY_SIGNATURES)


def _is_nitro_policy(raw: bytes) -> bool:
    text = raw.decode("utf-8", errors="replace")
    return RGB_HELPER_POLICY_MARKER in text and RGB_HELPER_EXEC_MARKER in text


def _is_nitro_py_file(fname: str, raw: bytes) -> bool:
    markers = _RGB_HELPER_PY_MARKERS.get(fname, ())
    return bool(markers) and any(m in raw for m in markers)


def _read_existing_rgb_helper_files(
    root: Path,
    libexec_fd: int | None,
    polkit_fd: int | None,
    pkg_fd: int | None,
    uids: set[int],
) -> tuple[dict[str, tuple[str, bytes]], dict[str, Path]]:
    """Verify non-symlink, regular file, root ownership and compute SHA-256 of present helper files."""
    launcher_path = root / "usr/local/libexec/nitro-control-rgb-helper"
    policy_path = root / "usr/share/polkit-1/actions/io.github.tobiasguta.NitroControl.rgb.policy"
    pkg_dir = root / "usr/local/libexec/nitro-control-rgb/nitro_control"

    if launcher_path.is_symlink():
        raise ModuleStagingError(f"Refusing symlinked launcher at {launcher_path}")
    if policy_path.is_symlink():
        raise ModuleStagingError(f"Refusing symlinked Polkit policy at {policy_path}")

    found: dict[str, tuple[str, bytes]] = {}
    key_paths: dict[str, Path] = {
        "nitro-control-rgb-helper": launcher_path,
        "io.github.tobiasguta.NitroControl.rgb.policy": policy_path,
        **{f"nitro_control/{f}": pkg_dir / f for f in RGB_HELPER_FILES},
    }

    if libexec_fd is not None and "nitro-control-rgb-helper" in os.listdir(libexec_fd):
        _, lraw = open_regular_ro("nitro-control-rgb-helper", dir_fd=libexec_fd, allowed_uids=uids, label=str(launcher_path))
        found["nitro-control-rgb-helper"] = (hashlib.sha256(lraw).hexdigest(), lraw)

    if polkit_fd is not None and "io.github.tobiasguta.NitroControl.rgb.policy" in os.listdir(polkit_fd):
        _, praw = open_regular_ro(
            "io.github.tobiasguta.NitroControl.rgb.policy", dir_fd=polkit_fd, allowed_uids=uids, label=str(policy_path)
        )
        found["io.github.tobiasguta.NitroControl.rgb.policy"] = (hashlib.sha256(praw).hexdigest(), praw)

    if pkg_fd is not None:
        pkg_entries = set(os.listdir(pkg_fd))
        for fname in RGB_HELPER_FILES:
            fpath = pkg_dir / fname
            if fpath.is_symlink():
                raise ModuleStagingError(f"Refusing symlinked RGB helper file at {fpath}")
            if fname in pkg_entries:
                _, raw = open_regular_ro(fname, dir_fd=pkg_fd, allowed_uids=uids, label=str(fpath))
                found[f"nitro_control/{fname}"] = (hashlib.sha256(raw).hexdigest(), raw)
    return found, key_paths


def _verify_existing_rgb_helper_provenance(
    root: Path,
    libexec_fd: int | None,
    polkit_fd: int | None,
    base_fd: int | None,
    pkg_fd: int | None,
    uids: set[int],
    *,
    adopt_legacy: bool,
    for_uninstall: bool,
) -> tuple[dict[str, object] | None, dict[str, list[str]], bool, dict[str, bytes]]:
    """Classify RGB helper artifacts cleanly across coherent, interrupted, modified, incomplete, and legacy states."""
    base_dir = root / "usr/local/libexec/nitro-control-rgb"
    pkg_dir = base_dir / "nitro_control"
    man_path = base_dir / RGB_HELPER_MANIFEST
    tx_path = base_dir / RGB_HELPER_TX_FILE

    if base_dir.is_symlink() or pkg_dir.is_symlink() or man_path.is_symlink() or tx_path.is_symlink():
        raise ModuleStagingError(f"Refusing symlinked RGB helper directory or metadata under {base_dir}")

    found, key_paths = _read_existing_rgb_helper_files(root, libexec_fd, polkit_fd, pkg_fd, uids)
    has_manifest = base_fd is not None and RGB_HELPER_MANIFEST in os.listdir(base_fd)
    has_tx = base_fd is not None and RGB_HELPER_TX_FILE in os.listdir(base_fd)
    prior_raw_map = {k: raw for k, (_, raw) in found.items()}

    if not found and not has_manifest and not has_tx:
        return None, {}, False, {}

    manifest: dict[str, object] | None = None
    if has_manifest:
        assert base_fd is not None
        _, mraw = open_regular_ro(RGB_HELPER_MANIFEST, dir_fd=base_fd, allowed_uids=uids, exact_mode=0o644, label=str(man_path))
        manifest = _parse_rgb_helper_manifest_bytes(mraw)

    # Check foreign launcher/policy markers first so non-Nitro files are never overwritten or unlinked.
    if "nitro-control-rgb-helper" in found and not _is_legacy_nitro_launcher(found["nitro-control-rgb-helper"][1]):
        raise ModuleStagingError(f"Foreign file occupies {key_paths['nitro-control-rgb-helper']}; preserving existing files")
    if (
        "io.github.tobiasguta.NitroControl.rgb.policy" in found
        and not _is_nitro_policy(found["io.github.tobiasguta.NitroControl.rgb.policy"][1])
    ):
        raise ModuleStagingError(
            f"Foreign Polkit policy occupies {key_paths['io.github.tobiasguta.NitroControl.rgb.policy']}; preserving existing files"
        )

    if has_tx:
        assert base_fd is not None
        _, tx_raw = open_regular_ro(RGB_HELPER_TX_FILE, dir_fd=base_fd, allowed_uids=uids, exact_mode=0o644, label=str(tx_path))
        tx = _parse_rgb_helper_tx_bytes(tx_raw)
        known_files: dict[str, list[str]] = tx["known_files"]  # type: ignore[assignment]
        tgt_files: dict[str, str] = tx["target_files"]  # type: ignore[assignment]
        man_files: dict[str, str] = manifest["files"] if manifest is not None else {}  # type: ignore[assignment]
        next_known: dict[str, list[str]] = {}
        for key, (digest, raw) in found.items():
            allowed = set(known_files.get(key, []))
            if key in tgt_files:
                allowed.add(tgt_files[key])
            if key in man_files:
                allowed.add(man_files[key])
            if allowed:
                if digest not in allowed:
                    raise ModuleStagingError(
                        f"Locally modified or conflicting file at {key_paths[key]} during interrupted helper "
                        "transaction; preserving local changes"
                    )
            elif key.startswith("nitro_control/") and not _is_nitro_py_file(key.split("/", 1)[1], raw):
                raise ModuleStagingError(f"Unrecognized file at {key_paths[key]}; preserving existing files")
            # Preserve both the committed baseline digest (if any) and the verified intermediate on-disk digest.
            preserved = sorted({d for d in (man_files.get(key), digest, *known_files.get(key, [])) if d})
            next_known[key] = preserved
        for key, klist in known_files.items():
            if key not in next_known and klist:
                next_known[key] = list(klist)
        return manifest, next_known, True, prior_raw_map

    found_hashes = {k: [d] for k, (d, _) in found.items()}

    if manifest is not None:
        files_map: dict[str, str] = manifest["files"]  # type: ignore[assignment]
        modified: list[str] = []
        missing: list[str] = []
        for key, expected_sha in sorted(files_map.items()):
            path = key_paths.get(key, base_dir / key)
            entry = found.get(key)
            if entry is None:
                missing.append(str(path))
            elif entry[0] != expected_sha:
                modified.append(str(path))
        if modified:
            # Never treat --adopt-legacy as permission to overwrite or delete locally modified manifested files!
            raise ModuleStagingError(
                f"Digest mismatch for locally modified manifested RGB helper artifact(s) "
                f"({', '.join(modified)}); inspect and resolve before reinstalling or uninstalling"
            )
        if missing and not for_uninstall:
            raise ModuleStagingError(
                f"Incomplete manifested RGB helper installation (missing: {', '.join(missing)}); "
                "run sudo ./scripts/uninstall-rgb-helper-fedora.sh or inspect before reinstalling"
            )
        return manifest, found_hashes, False, prior_raw_map

    # Unmanifested artifacts: accept only verified Nitro signatures + explicit --adopt-legacy.
    for key, (_, raw) in found.items():
        if key.startswith("nitro_control/"):
            fname = key.split("/", 1)[1]
            if not _is_nitro_py_file(fname, raw):
                raise ModuleStagingError(f"Unrecognized file at {key_paths[key]}; preserving existing files")

    if not adopt_legacy:
        raise ModuleStagingError(
            "Existing unmanifested Nitro RGB helper installation detected; "
            "inspect files and pass --adopt-legacy"
        )
    return None, found_hashes, False, prior_raw_map


def verify_privileged_rgb_helper(root: Path) -> None:
    """Privileged execution coherence check (call inside RGB_HELPER_LOCK before touching hardware)."""
    root = Path(root)
    uids = allowed_root_uids(root)
    libexec_fd = verify_dir_chain(root, ("usr", "local", "libexec"), allowed_uids=uids)
    polkit_fd = verify_dir_chain(root, ("usr", "share", "polkit-1", "actions"), allowed_uids=uids)
    base_dir = root / "usr/local/libexec/nitro-control-rgb"
    pkg_dir = base_dir / "nitro_control"
    base_fd: int | None = None
    pkg_fd: int | None = None
    try:
        if base_dir.is_symlink() or not base_dir.exists():
            raise ModuleStagingError("RGB helper installation metadata directory is missing or unsafe")
        base_fd = verify_dir_chain(root, ("usr", "local", "libexec", "nitro-control-rgb"), allowed_uids=uids)
        if pkg_dir.is_symlink() or not pkg_dir.exists():
            raise ModuleStagingError("RGB helper package directory is missing or unsafe")
        pkg_fd = verify_dir_chain(root, ("usr", "local", "libexec", "nitro-control-rgb", "nitro_control"), allowed_uids=uids)

        tx_path = base_dir / RGB_HELPER_TX_FILE
        man_path = base_dir / RGB_HELPER_MANIFEST
        if tx_path.is_symlink() or RGB_HELPER_TX_FILE in os.listdir(base_fd):
            raise ModuleStagingError(
                "Interrupted RGB helper installation detected; re-run sudo ./scripts/install-rgb-helper-fedora.sh before use"
            )
        if man_path.is_symlink() or RGB_HELPER_MANIFEST not in os.listdir(base_fd):
            raise ModuleStagingError(
                "RGB helper provenance manifest is missing or unsafe; re-run sudo ./scripts/install-rgb-helper-fedora.sh"
            )
        _, mraw = open_regular_ro(RGB_HELPER_MANIFEST, dir_fd=base_fd, allowed_uids=uids, exact_mode=0o644, label=str(man_path))
        manifest = _parse_rgb_helper_manifest_bytes(mraw)
        files_map: dict[str, str] = manifest["files"]  # type: ignore[assignment]
        found, key_paths = _read_existing_rgb_helper_files(root, libexec_fd, polkit_fd, pkg_fd, uids)
        for key in sorted(_RGB_HELPER_EXPECTED_KEYS):
            path = key_paths[key]
            entry = found.get(key)
            if entry is None:
                raise ModuleStagingError(f"Incomplete RGB helper installation: missing {path}")
            if entry[0] != files_map.get(key):
                raise ModuleStagingError(
                    f"RGB helper integrity mismatch at {path}; re-run sudo ./scripts/install-rgb-helper-fedora.sh"
                )
    finally:
        if pkg_fd is not None:
            os.close(pkg_fd)
        if base_fd is not None:
            os.close(base_fd)
        os.close(polkit_fd)
        os.close(libexec_fd)


def install_rgb_helper(
    root: Path,
    repo_root: Path,
    *,
    authorization: str = "auth_admin",
    adopt_legacy: bool = False,
    runner: Runner = _default_runner,
    on_stage: Callable[[str], None] | None = None,
) -> dict[str, object]:
    """Install/upgrade the optional root-owned RGB helper transactionally with provenance manifest."""
    root = Path(root)
    repo_root = Path(repo_root).resolve()
    uids = allowed_root_uids(root)
    src_pkg = repo_root / "nitro_control"
    sources: dict[str, bytes] = {}
    for fname in RGB_HELPER_FILES:
        spath = src_pkg / fname
        sources[fname] = _read_source_bytes(spath, reject_world_write=True)

    launcher_bytes = _render_rgb_helper_launcher()
    policy_bytes = _render_rgb_helper_policy(authorization)
    target_files: dict[str, str] = {
        f"nitro_control/{fname}": hashlib.sha256(fbytes).hexdigest()
        for fname, fbytes in sources.items()
    }
    target_files["nitro-control-rgb-helper"] = hashlib.sha256(launcher_bytes).hexdigest()
    target_files["io.github.tobiasguta.NitroControl.rgb.policy"] = hashlib.sha256(policy_bytes).hexdigest()

    with acquire_private_locks(
        root,
        (RGB_HELPER_LOCK,),
        "Another RGB helper operation is active; retry after it finishes.",
    ):
        libexec_fd = verify_dir_chain(root, ("usr", "local", "libexec"), create_from_index=1, new_dir_mode=0o755, allowed_uids=uids)
        polkit_fd = verify_dir_chain(root, ("usr", "share", "polkit-1", "actions"), create_from_index=1, new_dir_mode=0o755, allowed_uids=uids)
        base_dir = root / "usr/local/libexec/nitro-control-rgb"
        pkg_dir = base_dir / "nitro_control"
        if base_dir.is_symlink() or pkg_dir.is_symlink():
            os.close(libexec_fd)
            os.close(polkit_fd)
            raise ModuleStagingError(f"Refusing symlinked RGB helper directory under {base_dir}")

        base_fd: int | None = None
        pkg_fd: int | None = None
        try:
            if base_dir.exists():
                base_fd = verify_dir_chain(root, ("usr", "local", "libexec", "nitro-control-rgb"), allowed_uids=uids)
            if pkg_dir.exists():
                pkg_fd = verify_dir_chain(root, ("usr", "local", "libexec", "nitro-control-rgb", "nitro_control"), allowed_uids=uids)

            _, known_files, _, prior_raw_map = _verify_existing_rgb_helper_provenance(
                root,
                libexec_fd,
                polkit_fd,
                base_fd,
                pkg_fd,
                uids,
                adopt_legacy=adopt_legacy,
                for_uninstall=False,
            )

            if pkg_fd is None:
                if base_fd is not None:
                    os.close(base_fd)
                    base_fd = None
                pkg_fd = verify_dir_chain(
                    root, ("usr", "local", "libexec", "nitro-control-rgb", "nitro_control"),
                    create_from_index=3, new_dir_mode=0o755, allowed_uids=uids,
                )
                base_fd = verify_dir_chain(root, ("usr", "local", "libexec", "nitro-control-rgb"), allowed_uids=uids)

            assert base_fd is not None
            tx_obj = {
                "managed_by": MANAGED_BY,
                "schema_version": SCHEMA_VERSION,
                "component": "rgb-helper",
                "state": "installing",
                "known_files": known_files,
                "target_files": target_files,
            }
            _atomic_write_in_dir(
                base_fd,
                RGB_HELPER_TX_FILE,
                (json.dumps(tx_obj, sort_keys=True, indent=2) + "\n").encode("utf-8"),
                0o644,
                uids=uids,
                label=str(base_dir / RGB_HELPER_TX_FILE),
            )
            if on_stage is not None:
                on_stage("after_tx_prepare")

            for fname, fbytes in sources.items():
                _atomic_write_in_dir(pkg_fd, fname, fbytes, 0o644, uids=uids, label=str(pkg_dir / fname))
                if fname == "rgb.py" and on_stage is not None:
                    on_stage("after_partial_py")

            known_py_sources = {
                fname.rsplit(".", 1)[0]: tuple(
                    b for b in (prior_raw_map.get(f"nitro_control/{fname}"), sources[fname]) if b is not None
                )
                for fname in RGB_HELPER_FILES
            }
            _clean_pycache_in_dir(
                pkg_fd, pkg_dir, uids, owned_stems=_RGB_HELPER_PY_STEMS, known_sources=known_py_sources
            )

            launcher_path = root / RGB_HELPER_EXEC_MARKER.lstrip("/")
            policy_path = root / "usr/share/polkit-1/actions/io.github.tobiasguta.NitroControl.rgb.policy"
            _atomic_write_in_dir(libexec_fd, "nitro-control-rgb-helper", launcher_bytes, 0o755, uids=uids, label=str(launcher_path))
            if on_stage is not None:
                on_stage("after_launcher")
            _atomic_write_in_dir(polkit_fd, "io.github.tobiasguta.NitroControl.rgb.policy", policy_bytes, 0o644, uids=uids, label=str(policy_path))
            if on_stage is not None:
                on_stage("after_policy")

            manifest_obj = {
                "managed_by": MANAGED_BY,
                "schema_version": SCHEMA_VERSION,
                "component": "rgb-helper",
                "helper_protocol": RGB_HELPER_PROTOCOL,
                "capabilities": list(RGB_HELPER_CAPABILITIES),
                "max_request_bytes": 1024,
                "authorization": authorization,
                "files": target_files,
            }
            manifest_bytes = (json.dumps(manifest_obj, sort_keys=True, indent=2) + "\n").encode("utf-8")
            _atomic_write_in_dir(base_fd, RGB_HELPER_MANIFEST, manifest_bytes, 0o644, uids=uids, label=str(base_dir / RGB_HELPER_MANIFEST))
            if on_stage is not None:
                on_stage("after_manifest")

            if RGB_HELPER_TX_FILE in os.listdir(base_fd):
                os.unlink(RGB_HELPER_TX_FILE, dir_fd=base_fd)
                _fsync_dir(base_fd)

            if shutil.which("restorecon"):
                runner([
                    "restorecon", "-F",
                    str(launcher_path),
                    str(policy_path),
                    str(base_dir / RGB_HELPER_MANIFEST),
                    *(str(pkg_dir / fname) for fname in RGB_HELPER_FILES),
                ])

            return {
                "launcher": str(launcher_path),
                "policy": str(policy_path),
                "manifest": str(base_dir / RGB_HELPER_MANIFEST),
                "authorization": authorization,
                "helper_protocol": RGB_HELPER_PROTOCOL,
                "capabilities": list(RGB_HELPER_CAPABILITIES),
            }
        finally:
            if pkg_fd is not None:
                os.close(pkg_fd)
            if base_fd is not None:
                os.close(base_fd)
            os.close(libexec_fd)
            os.close(polkit_fd)


def uninstall_rgb_helper(
    root: Path,
    *,
    adopt_legacy: bool = False,
) -> dict[str, object]:
    """Remove only Nitro-owned RGB helper artifacts, preserving unrelated, modified, or foreign files."""
    root = Path(root)
    uids = allowed_root_uids(root)
    removed_paths: list[str] = []

    with acquire_private_locks(
        root,
        (RGB_HELPER_LOCK,),
        "Another RGB helper operation is active; retry after it finishes.",
    ):
        libexec_dir = root / "usr/local/libexec"
        polkit_dir = root / "usr/share/polkit-1/actions"
        base_dir = root / "usr/local/libexec/nitro-control-rgb"
        pkg_dir = base_dir / "nitro_control"
        launcher_path = libexec_dir / "nitro-control-rgb-helper"
        policy_path = polkit_dir / "io.github.tobiasguta.NitroControl.rgb.policy"
        man_path = base_dir / RGB_HELPER_MANIFEST

        if (
            launcher_path.is_symlink()
            or policy_path.is_symlink()
            or base_dir.is_symlink()
            or pkg_dir.is_symlink()
            or man_path.is_symlink()
        ):
            raise ModuleStagingError("Refusing symlinked RGB helper path during removal")

        libexec_fd = verify_dir_chain(root, ("usr", "local", "libexec"), allowed_uids=uids) if libexec_dir.exists() else None
        polkit_fd = verify_dir_chain(root, ("usr", "share", "polkit-1", "actions"), allowed_uids=uids) if polkit_dir.exists() else None
        base_fd = verify_dir_chain(root, ("usr", "local", "libexec", "nitro-control-rgb"), allowed_uids=uids) if base_dir.exists() else None
        pkg_fd = verify_dir_chain(root, ("usr", "local", "libexec", "nitro-control-rgb", "nitro_control"), allowed_uids=uids) if pkg_dir.exists() else None

        try:
            if libexec_fd is None and polkit_fd is None and base_fd is None and pkg_fd is None:
                return {"removed": []}

            manifest, _, had_tx, prior_raw_map = _verify_existing_rgb_helper_provenance(
                root,
                libexec_fd,
                polkit_fd,
                base_fd,
                pkg_fd,
                uids,
                adopt_legacy=adopt_legacy,
                for_uninstall=True,
            )

            if libexec_fd is not None and "nitro-control-rgb-helper" in os.listdir(libexec_fd):
                os.unlink("nitro-control-rgb-helper", dir_fd=libexec_fd)
                _fsync_dir(libexec_fd)
                removed_paths.append(str(launcher_path))
            if polkit_fd is not None and "io.github.tobiasguta.NitroControl.rgb.policy" in os.listdir(polkit_fd):
                os.unlink("io.github.tobiasguta.NitroControl.rgb.policy", dir_fd=polkit_fd)
                _fsync_dir(polkit_fd)
                removed_paths.append(str(policy_path))

            if pkg_fd is not None:
                tracked_py = list(RGB_HELPER_FILES)
                if manifest is not None and isinstance(manifest.get("files"), dict):
                    for key in manifest["files"]:
                        if isinstance(key, str) and key.startswith("nitro_control/"):
                            fname = key.split("/", 1)[1]
                            if "/" not in fname and fname not in tracked_py:
                                tracked_py.append(fname)
                for fname in tracked_py:
                    if fname in os.listdir(pkg_fd):
                        open_regular_ro(fname, dir_fd=pkg_fd, allowed_uids=uids, label=str(pkg_dir / fname))
                        os.unlink(fname, dir_fd=pkg_fd)
                        removed_paths.append(str(pkg_dir / fname))
                _fsync_dir(pkg_fd)
                known_py_sources = {
                    fname.rsplit(".", 1)[0]: (prior_raw_map[f"nitro_control/{fname}"],)
                    for fname in RGB_HELPER_FILES
                    if f"nitro_control/{fname}" in prior_raw_map
                }
                _clean_pycache_in_dir(
                    pkg_fd, pkg_dir, uids, owned_stems=_RGB_HELPER_PY_STEMS, known_sources=known_py_sources
                )

            if base_fd is not None:
                if RGB_HELPER_MANIFEST in os.listdir(base_fd):
                    os.unlink(RGB_HELPER_MANIFEST, dir_fd=base_fd)
                    removed_paths.append(str(man_path))
                if had_tx and RGB_HELPER_TX_FILE in os.listdir(base_fd):
                    os.unlink(RGB_HELPER_TX_FILE, dir_fd=base_fd)
                    removed_paths.append(str(base_dir / RGB_HELPER_TX_FILE))
                _fsync_dir(base_fd)
        finally:
            if pkg_fd is not None:
                os.close(pkg_fd)
            if base_fd is not None:
                os.close(base_fd)
            if polkit_fd is not None:
                os.close(polkit_fd)
            if libexec_fd is not None:
                os.close(libexec_fd)

        for d in (pkg_dir, base_dir):
            try:
                if d.exists() and not d.is_symlink():
                    d.rmdir()
            except OSError:
                pass
        return {"removed": removed_paths}


def verify_privileged_runtime(
    root: Path,
    component: str,
    kernel: str,
    *,
    runner: Runner = _default_runner,
) -> Path:
    """Privileged full provenance + integrity + unresolved-migration verification (call inside lock)."""
    spec = COMPONENT_SPECS[component]
    kernel = validate_kernel_name(kernel)
    root = Path(root)
    uids = allowed_root_uids(root)

    leg_dir = root / "usr/lib/modules" / kernel / "extra/nitro-control"
    if (leg_dir / spec.module_file).exists() or (leg_dir / f"{spec.module_file}.sha256").exists() or (leg_dir / spec.module_file).is_symlink():
        raise ModuleStagingError(f"Unresolved legacy indexed module in {leg_dir}; re-run installer to migrate")
    if _modules_dep_stale(root, kernel, spec.module_file, spec.module_name, spec.component):
        raise ModuleStagingError(f"Stale kernel module index references {spec.module_file}; run depmod -a {kernel}")
    mstate = _load_migration_state(root, spec)
    if kernel in mstate["pending_kernels"]:
        raise ModuleStagingError(f"Unresolved migration or clearance state for {kernel}: {mstate['errors'].get(kernel, 're-run installer')}")

    rel = (*PRIVATE_MODULES_REL, kernel)
    try:
        dir_fd = verify_dir_chain(root, rel, allowed_uids=uids)
    except ModuleStagingError as exc:
        raise ModuleStagingError(f"Native RGB staging directory check failed: {exc}") from exc
    dir_path = root.joinpath(*rel)
    try:
        sentinel = dir_path / clearance_sentinel_name(spec.module_file)
        if sentinel.exists() or sentinel.is_symlink():
            raise ModuleStagingError(f"Unresolved migration clearance marker present in {dir_path}")
        state, _ = _inspect_location(root, dir_fd, dir_path, spec, kernel, runner=runner, allow_adopt=False)
        if state == "absent":
            raise ModuleStagingError(f"Install the opt-in {component} module for {kernel}")
        if state == "unadopted":
            raise ModuleStagingError(f"Staged artifact in {dir_path} lacks Nitro Control provenance manifest")
        if state == "interrupted":
            raise ModuleStagingError(f"Interrupted transaction in {dir_path}; re-run installer to recover")
        if component == "native-rgb":
            _check_native_module_params(dir_path / spec.module_file, runner)
        return dir_path / spec.module_file
    finally:
        os.close(dir_fd)


def _cli_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nitro_control.module_staging")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_inst = sub.add_parser("install")
    p_inst.add_argument("--component", required=True, choices=sorted(COMPONENT_SPECS))
    p_inst.add_argument("--kernel", required=True)
    p_inst.add_argument("--source", required=True)
    p_inst.add_argument("--adopt-legacy", action="store_true")
    p_inst.add_argument("--controller-src")
    p_inst.add_argument("--unit-src")

    p_un = sub.add_parser("uninstall")
    p_un.add_argument("--component", required=True, choices=sorted(COMPONENT_SPECS))
    p_un.add_argument("--kernel", required=True)
    p_un.add_argument("--adopt-legacy", action="store_true")

    p_ver = sub.add_parser("verify-runtime")
    p_ver.add_argument("--component", required=True, choices=sorted(COMPONENT_SPECS))
    p_ver.add_argument("--kernel", required=True)

    p_lock = sub.add_parser("run-locked")
    p_lock.add_argument("--lock", dest="locks", action="append", required=True)
    p_lock.add_argument("--busy-message", required=True)
    p_lock.add_argument("child", nargs=argparse.REMAINDER)

    p_exec = sub.add_parser("lock-and-exec")
    p_exec.add_argument("--lock", dest="locks", action="append", required=True)
    p_exec.add_argument("--busy-message", required=True)
    p_exec.add_argument("child", nargs=argparse.REMAINDER)

    p_h_inst = sub.add_parser("install-rgb-helper")
    p_h_inst.add_argument("--repo-root", required=True)
    p_h_inst.add_argument("--authorization", default="auth_admin", choices=("auth_admin", "auth_admin_keep"))
    p_h_inst.add_argument("--adopt-legacy", action="store_true")

    p_h_un = sub.add_parser("uninstall-rgb-helper")
    p_h_un.add_argument("--adopt-legacy", action="store_true")

    args = parser.parse_args(argv)
    if os.geteuid() != 0:
        print("ERROR: Administrator root privileges required.", file=sys.stderr)
        return 1
    try:
        if args.cmd == "install":
            res = stage_install(
                Path("/"),
                args.component,
                args.kernel,
                Path(args.source),
                adopt_legacy=args.adopt_legacy,
                controller_src=Path(args.controller_src) if args.controller_src else None,
                unit_src=Path(args.unit_src) if args.unit_src else None,
            )
            for msg in (*res["notes"], *res["warnings"]):
                print(msg, file=sys.stderr if msg.startswith(("NOTICE:", "UNVERIFIED", "WARNING")) else sys.stdout)
            return 0
        if args.cmd == "uninstall":
            res = uninstall_modules(Path("/"), args.component, args.kernel, adopt_legacy=args.adopt_legacy)
            for warn in res["warnings"]:
                print(warn, file=sys.stderr)
            return 0
        if args.cmd == "install-rgb-helper":
            install_rgb_helper(
                Path("/"),
                Path(args.repo_root),
                authorization=args.authorization,
                adopt_legacy=args.adopt_legacy,
            )
            return 0
        if args.cmd == "uninstall-rgb-helper":
            uninstall_rgb_helper(Path("/"), adopt_legacy=args.adopt_legacy)
            return 0
        if args.cmd == "verify-runtime":
            verify_privileged_runtime(Path("/"), args.component, args.kernel)
            return 0
        if args.cmd == "lock-and-exec":
            child = list(args.child)
            if child[:1] == ["--"]:
                child = child[1:]
            if not child:
                raise ModuleStagingError("No command specified for lock-and-exec")
            with acquire_private_locks(Path("/"), tuple(args.locks), args.busy_message, inheritable=True):
                os.execv(child[0], child)
        if args.cmd == "run-locked":
            child = list(args.child)
            if child[:1] == ["--"]:
                child = child[1:]
            if not child:
                raise ModuleStagingError("No command specified for run-locked")
            with acquire_private_locks(Path("/"), tuple(args.locks), args.busy_message):
                proc = subprocess.Popen(child, start_new_session=True)
                forwarded_sig: int | None = None

                # Forward termination signals without calling proc.wait() from the
                # signal handler: proc.wait() holds _waitpid_lock on the main thread.
                def _forward(signum: int, _frame: object) -> None:
                    nonlocal forwarded_sig
                    forwarded_sig = signum
                    try:
                        os.killpg(proc.pid, signum)
                    except (ProcessLookupError, PermissionError):
                        pass

                prev_handlers = {}
                for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
                    prev_handlers[sig] = signal.getsignal(sig)
                    signal.signal(sig, _forward)
                try:
                    rc = proc.wait()
                finally:
                    for sig, handler in prev_handlers.items():
                        signal.signal(sig, handler)
                return (128 + forwarded_sig) if forwarded_sig is not None else rc
    except (ModuleStagingError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli_main())
