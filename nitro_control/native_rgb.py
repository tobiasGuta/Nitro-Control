"""Opt-in, kernel-pinned, one-shot native Acer WMI RGB backend.

This does NOT replace acer_wmi. A root-owned, verified development module runs
once per authorized request and is unloaded immediately. Software success is
NOT proof that the physical LEDs changed; inspect the keyboard.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import os
from pathlib import Path
import re
import stat
import subprocess

from . import RGB_WMI_GUID
from .module_staging import (
    COMPONENT_SPECS,
    DISALLOWED_NATIVE_EXPERIMENTAL_PARAMS,
    ModuleStagingError,
    clearance_sentinel_name,
    inspect_native_unprivileged_readiness,
    manifest_name,
    migration_state_name,
    parse_manifest_bytes,
    tx_dir_name,
    tx_json_name,
    _load_migration_state,
    _modules_dep_stale,
)
from .rgb import EffectPlan, LightingPlan, PHYSICAL_EFFECT_MODES

MOD_NAME = "nitro_rgb_enable_poc"
MODULE_FILENAME = "nitro_rgb_enable_poc.ko"
PRIVATE_MODULES_DIR = Path("usr/local/lib/nitro-control/modules")
MODULE_SUBPATH = f"modules/{MODULE_FILENAME}"
LEGACY_MODULE_SUBPATH = f"extra/nitro-control/{MODULE_FILENAME}"
_LOCK_SUBDIR = "nitro-control"
_LOCK_NAME = "native-rgb.lock"
_LOCK = Path(f"/run/{_LOCK_SUBDIR}/{_LOCK_NAME}")
_HEX_SHA = re.compile(r"[a-f0-9]{64}\Z")


def _validate_kernel_name(kernel: str) -> str:
    if not kernel or "/" in kernel or "\\" in kernel or "\x00" in kernel or kernel in {".", ".."}:
        raise ValueError(f"Unsafe kernel release string: {kernel!r}")
    return kernel


def _allowed_root_uids(root: Path) -> set[int]:
    if root == Path("/"):
        return {0}
    try:
        root_uid = root.stat().st_uid
    except OSError:
        return {0}
    # Support unprivileged synthetic root fixtures while still requiring root:root
    # on real `/` and inside root-mapped containers.
    return {0} if root_uid == 0 else {0, root_uid}


class NativeRGBError(RuntimeError):
    """A native RGB preflight, write or verification failed."""


class NativeRGBTimeoutError(NativeRGBError):
    """A kernel module command timed out cleanly."""


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError) as exc:
        raise NativeRGBError(f"Cannot read {path.name}: {exc}") from exc


def _run(argv: list[str], *, timeout: int = 20) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(argv, capture_output=True, text=True,
                              timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise NativeRGBTimeoutError(f"RGB module operation timed out after {timeout}s") from exc
    except OSError as exc:
        raise NativeRGBError(f"RGB module operation failed: {type(exc).__name__}") from exc


class NativeRGB:
    """No kernel operation is performed by status(); apply() requires root."""

    def __init__(self, root: Path = Path("/"), kernel: str | None = None, *, runner=None):
        self.root = Path(root)
        self.kernel = _validate_kernel_name(kernel or os.uname().release)
        self.module = self.root / PRIVATE_MODULES_DIR / self.kernel / MODULE_FILENAME
        self.digest = Path(f"{self.module}.sha256")
        self.manifest = Path(f"{self.module}.manifest.json")
        self.legacy_module = self.root / "usr/lib/modules" / self.kernel / LEGACY_MODULE_SUBPATH
        self._runner = runner

    def path(self, relative: str) -> Path:
        return self.root / relative.lstrip("/")

    def _fans_available(self) -> bool:
        for hwmon in self.path("sys/class/hwmon").glob("hwmon*"):
            try:
                if (_read(hwmon / "name") == "acer" and
                        (hwmon / "fan1_input").is_file() and
                        (hwmon / "fan2_input").is_file()):
                    return True
            except NativeRGBError:
                continue
        return False

    def status_and_effects(self) -> tuple[bool, str, bool]:
        try:
            if (_read(self.path("sys/class/dmi/id/sys_vendor")) != "Acer" or
                    _read(self.path("sys/class/dmi/id/product_name")) != "Nitro AN515-58"):
                return False, "Native WMI is restricted to Acer Nitro AN515-58", False
            if not any(self.path("sys/bus/wmi/devices").glob(f"{RGB_WMI_GUID}-*")):
                return False, "Required numbered Acer gaming WMI GUID is missing", False
            if not self.path("sys/module/acer_wmi").is_dir():
                return False, "Stock acer_wmi must be loaded", False
            if self.path("sys/module/linuwu_sense").exists():
                return False, "Conflicting Linuwu driver is loaded", False
            if self.path("sys/module/nitro_rgb_enable_poc").exists():
                return False, "Native RGB test module is already loaded", False
            if "acer_wmi.force_caps=" in _read(self.path("proc/cmdline")):
                return False, "Non-default acer_wmi.force_caps is not supported", False
            if not self._fans_available():
                return False, "Acer HWMON fan1/fan2 telemetry is unavailable", False
            if not self.path("sys/firmware/acpi/platform_profile").is_file():
                return False, "Firmware platform profile interface is unavailable", False
            ok_ready, detail, insp = inspect_native_unprivileged_readiness(
                self.root, self.kernel, runner=self._runner
            )
            if not ok_ready:
                return False, detail or "Install the opt-in native RGB module for this kernel", False
            has_effects = insp is not None and "effect_mode" in insp.exposed
            return True, "Kernel-pinned native WMI helper present; requires administrator authorization", has_effects
        except NativeRGBError as exc:
            return False, str(exc), False

    def status(self) -> tuple[bool, str]:
        ok_ready, detail, _ = self.status_and_effects()
        return ok_ready, detail

    def _staging_directories(self, *, include_ancestors: bool = False) -> tuple[Path, ...]:
        base = self.path("usr/local/lib/nitro-control")
        dirs = (base, base / "modules", base / "modules" / self.kernel)
        if not include_ancestors:
            return dirs
        return (self.path("usr"), self.path("usr/local"), self.path("usr/local/lib"), *dirs)

    def _verify_install(self) -> None:
        allowed_uids = _allowed_root_uids(self.root)
        priv_dir = self.module.parent
        tx_json = priv_dir / tx_json_name(MODULE_FILENAME)
        tx_dir = priv_dir / tx_dir_name(MODULE_FILENAME)
        sentinel = priv_dir / clearance_sentinel_name(MODULE_FILENAME)
        legacy_sha = Path(f"{self.legacy_module}.sha256")
        legacy_manifest = self.legacy_module.parent / manifest_name(MODULE_FILENAME)
        if tx_json.exists() or tx_json.is_symlink() or tx_dir.exists() or tx_dir.is_symlink():
            raise NativeRGBError("Interrupted native RGB installation detected; re-run installer to recover")
        if sentinel.exists() or sentinel.is_symlink():
            raise NativeRGBError(f"Unresolved migration or clearance state for {self.kernel}; re-run installer")
        if (self.legacy_module.exists() or self.legacy_module.is_symlink()
                or legacy_sha.exists() or legacy_sha.is_symlink()
                or legacy_manifest.exists() or legacy_manifest.is_symlink()):
            raise NativeRGBError("Unresolved legacy indexed native RGB module detected; re-run installer to migrate")
        if _modules_dep_stale(self.root, self.kernel, MODULE_FILENAME, MOD_NAME, "native-rgb"):
            raise NativeRGBError("Stale module index references legacy native RGB path; refresh depmod via installer")
        mfile = self.path("usr/local/lib/nitro-control") / migration_state_name("native-rgb")
        if mfile.exists() or mfile.is_symlink():
            try:
                mstate = _load_migration_state(self.root, COMPONENT_SPECS["native-rgb"])
                if self.kernel in mstate["pending_kernels"]:
                    raise NativeRGBError(f"Unresolved migration or clearance state for {self.kernel}; re-run installer")
            except ModuleStagingError as exc:
                raise NativeRGBError(str(exc)) from exc
        try:
            for ancestor in self._staging_directories(include_ancestors=True):
                if ancestor.is_symlink():
                    raise NativeRGBError("Native RGB staging directory is a symlink")
            for directory in self._staging_directories():
                if directory.is_symlink() or not directory.is_dir():
                    raise NativeRGBError("Native RGB staging directory is missing or a symlink")
                dinfo = directory.stat()
                if dinfo.st_uid not in allowed_uids or (dinfo.st_mode & 0o022) != 0:
                    raise NativeRGBError("Native RGB staging directory ownership or mode mismatch")
            for path, expected_mode in ((self.module, 0o644), (self.digest, 0o600), (self.manifest, 0o600)):
                if path.is_symlink() or not path.is_file():
                    raise NativeRGBError("Native RGB installed file is missing or a symlink")
                info = path.stat()
                if info.st_uid != 0 or stat.S_IMODE(info.st_mode) != expected_mode or info.st_nlink != 1:
                    raise NativeRGBError("Native RGB installed file ownership or mode mismatch")
            expected = _read(self.digest)
            if not _HEX_SHA.fullmatch(expected):
                raise NativeRGBError("Invalid native RGB module digest")
            actual = hashlib.sha256(self.module.read_bytes()).hexdigest()
            if actual != expected:
                raise NativeRGBError("Native RGB module digest mismatch")
            try:
                parse_manifest_bytes(
                    self.manifest.read_bytes(),
                    expected_component="native-rgb",
                    expected_kernel=self.kernel,
                    expected_module_name=MOD_NAME,
                    expected_sha256=actual,
                )
            except ModuleStagingError as exc:
                raise NativeRGBError(str(exc)) from exc
        except OSError as exc:
            raise NativeRGBError("Native RGB module integrity check could not read installed files") from exc
        mod_name = _run(["/usr/sbin/modinfo", "-F", "name", str(self.module)])
        vermagic = _run(["/usr/sbin/modinfo", "-F", "vermagic", str(self.module)])
        if (mod_name.returncode != 0 or mod_name.stdout.strip() != MOD_NAME or
                vermagic.returncode != 0 or
                vermagic.stdout.split()[:1] != [self.kernel]):
            raise NativeRGBError("Native RGB module identity or kernel version mismatch")
        params_proc = _run(["/usr/sbin/modinfo", "-p", str(self.module)], timeout=2)
        if params_proc.returncode != 0:
            raise NativeRGBError("Unable to inspect native RGB module parameters")
        exposed = {
            line.split(":", 1)[0].strip()
            for line in params_proc.stdout.splitlines()
            if ":" in line
        }
        forbidden = sorted(exposed & DISALLOWED_NATIVE_EXPERIMENTAL_PARAMS)
        if forbidden:
            raise NativeRGBError(
                f"Experimental diagnostic module parameters ({', '.join(forbidden)}) are not permitted in runtime staging"
            )

    def _has_effect_parameter(self) -> bool:
        try:
            params = _run(["/usr/sbin/modinfo", "-p", str(self.module)], timeout=2)
        except NativeRGBError:
            return False
        return params.returncode == 0 and any(
            line.startswith("effect_mode:") for line in params.stdout.splitlines()
        )

    def effects_supported(self) -> bool:
        _, _, eff = self.status_and_effects()
        return eff

    def apply_effect(self, plan: EffectPlan) -> str:
        if not isinstance(plan, EffectPlan):
            raise NativeRGBError("A validated lighting effect is required")
        mode = PHYSICAL_EFFECT_MODES[plan.effect]
        self._apply_module([f"effect_mode={mode}", f"effect_speed={plan.speed}",
                            f"effect_rgb=0x{plan.color[1:]}", "brightness=100"],
                           require_effects=True)
        return (
            f"{plan.effect} requested; firmware readback confirmed mode={mode}, speed={plan.speed}, "
            "brightness=100 (effect color and physical LEDs are not firmware-verified). "
            "Select Static and apply a palette to switch back."
        )

    def apply(self, plan: LightingPlan) -> str:
        if not isinstance(plan, LightingPlan):
            raise NativeRGBError("A validated four-zone lighting plan is required")
        # Fedora 45 AN515-58 testing: 65% WMI brightness made Fn+F9
        # change visible colors; 100% preserved normal Fn dimming.
        # This is a temporary hardware-specific guard, not a protocol limit.
        if plan.brightness != 100:
            raise NativeRGBError(
                "Native Acer WMI currently requires 100% apply brightness; "
                "use Fn+F9/Fn+F10 for physical dimming")
        colors = ",".join(zone[1:] for zone in plan.zones)
        self._apply_module(["apply=1", f"colors={colors}", f"brightness={plan.brightness}"])
        return "Native WMI firmware confirmed static mode, WMI brightness 100%, and four zone color readbacks; verify the physical keyboard visually"

    @contextmanager
    def _acquire_lock(self):
        default_lock = Path(f"/run/{_LOCK_SUBDIR}/{_LOCK_NAME}")
        if _LOCK != default_lock:
            try:
                fd = os.open(_LOCK, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            except OSError as exc:
                raise NativeRGBError("Native RGB lock unavailable; no firmware writes attempted") from exc
            try:
                info = os.fstat(fd)
                if info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o600:
                    raise NativeRGBError("Native RGB lock has unsafe permissions")
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                yield
            except BlockingIOError as exc:
                raise NativeRGBError("Another native RGB operation is already active") from exc
            finally:
                os.close(fd)
            return

        allowed_uids = _allowed_root_uids(self.root)
        run_path = self.root / "run"
        if self.root != Path("/"):
            try:
                os.mkdir(run_path, 0o755)
            except FileExistsError:
                pass
            except OSError as exc:
                raise NativeRGBError("Native RGB lock unavailable; no firmware writes attempted") from exc
        run_fd = -1
        dir_fd = -1
        lock_fd = -1
        try:
            try:
                run_fd = os.open(run_path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
                run_info = os.fstat(run_fd)
            except OSError as exc:
                raise NativeRGBError("Native RGB lock unavailable; no firmware writes attempted") from exc
            if (not stat.S_ISDIR(run_info.st_mode) or run_info.st_uid not in allowed_uids
                    or (run_info.st_mode & 0o022) != 0):
                raise NativeRGBError("Native RGB lock has unsafe permissions")
            try:
                try:
                    os.mkdir(_LOCK_SUBDIR, 0o700, dir_fd=run_fd)
                except FileExistsError:
                    pass
                dir_fd = os.open(_LOCK_SUBDIR,
                                 os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                                 dir_fd=run_fd)
                dir_info = os.fstat(dir_fd)
            except OSError as exc:
                raise NativeRGBError("Native RGB lock unavailable; no firmware writes attempted") from exc
            finally:
                os.close(run_fd)
                run_fd = -1
            if (not stat.S_ISDIR(dir_info.st_mode) or dir_info.st_uid not in allowed_uids
                    or stat.S_IMODE(dir_info.st_mode) != 0o700):
                raise NativeRGBError("Native RGB lock has unsafe permissions")
            try:
                lock_fd = os.open(_LOCK_NAME, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
                                  0o600, dir_fd=dir_fd)
                lock_info = os.fstat(lock_fd)
            except OSError as exc:
                raise NativeRGBError("Native RGB lock unavailable; no firmware writes attempted") from exc
            finally:
                os.close(dir_fd)
                dir_fd = -1
            if (not stat.S_ISREG(lock_info.st_mode) or lock_info.st_uid not in allowed_uids
                    or stat.S_IMODE(lock_info.st_mode) != 0o600
                    or getattr(lock_info, "st_nlink", 1) != 1):
                raise NativeRGBError("Native RGB lock has unsafe permissions")
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise NativeRGBError("Another native RGB operation is already active") from exc
            yield
        finally:
            if run_fd != -1:
                os.close(run_fd)
            if dir_fd != -1:
                os.close(dir_fd)
            if lock_fd != -1:
                os.close(lock_fd)

    def _apply_module(self, params: list[str], *, require_effects: bool = False) -> None:
        if os.geteuid() != 0:
            raise NativeRGBError("Native RGB changes require administrator authorization")
        # Acquire the shared lifecycle lock BEFORE integrity verification and
        # module insertion so installers/uninstallers cannot mutate the artifact mid-check.
        with self._acquire_lock():
            ready, reason = self.status()
            if not ready:
                raise NativeRGBError(reason)
            self._verify_install()
            if require_effects and not self._has_effect_parameter():
                raise NativeRGBError("Install the Dynamic-capable native RGB module for this kernel")
            inserted = False
            op_error: str | None = None
            try:
                outcome = _run(["/usr/sbin/insmod", str(self.module), *params])
                inserted = outcome.returncode == 0
                if not inserted:
                    detail = outcome.stderr.strip() or outcome.stdout.strip() or f"insmod exited {outcome.returncode}"
                    op_error = (
                        f"Native RGB firmware write failed ({detail[:160]}); firmware lighting state "
                        "may have changed and automatic recovery could not be confirmed. "
                        "Inspect keyboard and journalctl -k, or apply Static."
                    )
            except NativeRGBTimeoutError as exc:
                op_error = (
                    f"{exc}; kernel module execution did not finish and firmware lighting state "
                    "is uncertain. Inspect lsmod, keyboard, and journalctl -k."
                )
            except NativeRGBError as exc:
                op_error = (
                    f"Native RGB module invocation failed ({exc}); firmware lighting state "
                    "may be uncertain. Inspect lsmod, keyboard, and journalctl -k."
                )
            finally:
                # Conditionally unload only when insmod succeeded or module sysfs entry is present.
                unload_error: str | None = None
                if inserted or self.path("sys/module/nitro_rgb_enable_poc").exists():
                    try:
                        unload = _run(["/usr/sbin/rmmod", MOD_NAME])
                        if unload.returncode != 0:
                            ud = unload.stderr.strip() or unload.stdout.strip() or f"rmmod exited {unload.returncode}"
                            unload_error = (
                                f"CRITICAL: native RGB module could not be unloaded ({ud[:120]}); inspect lsmod"
                            )
                    except NativeRGBError as exc:
                        unload_error = f"CRITICAL: native RGB module unload failed ({exc}); inspect lsmod"
                if unload_error is not None:
                    if op_error is not None:
                        raise NativeRGBError(f"{unload_error} (after operation error: {op_error})")
                    raise NativeRGBError(
                        f"{unload_error}; firmware write may have succeeded but module remains loaded"
                    )
            if op_error is not None:
                raise NativeRGBError(op_error)
            if not self._fans_available() or not self.path("sys/firmware/acpi/platform_profile").is_file():
                raise NativeRGBError("RGB was applied, but Acer monitoring disappeared; inspect driver state")
