"""AURUS Evaluation Edition entry point."""

from __future__ import annotations

import atexit
from dataclasses import dataclass
import glob
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import tempfile
import subprocess
import shutil
import signal
import time

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from src.runtime import RobotRuntime
from src.web.app import create_app


LEGACY_SERVICES = (
    "aurus-assistant.service",
    "aurus-autonomy.service",
    "aurus-coordinator.service",
    "aurus-hardware.service",
    "aurus-vision.service",
    "aurus-web.service",
)
CAPTURE_HELPERS = {
    "arecord", "rpicam-hello", "rpicam-still", "rpicam-vid",
    "libcamera-hello", "libcamera-still", "libcamera-vid",
}


@dataclass(frozen=True)
class ProcessOwner:
    pid: int
    ppid: int
    uid: int
    executable: str
    command: str
    devices: tuple[str, ...] = ()


def active_legacy_services() -> list[str]:
    if os.name == "nt" or not shutil.which("systemctl"):
        return []
    active = []
    for service in LEGACY_SERVICES:
        try:
            result = subprocess.run(
                ["systemctl", "is-active", "--quiet", service],
                capture_output=True,
                timeout=3,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if result.returncode == 0:
            active.append(service)
    return active


def stop_legacy_services(services: list[str], logger: logging.Logger) -> None:
    """Stop only explicitly known obsolete AURUS units."""
    if not services:
        return
    logger.warning("Stopping legacy AURUS services: %s", " ".join(services))
    try:
        result = subprocess.run(
            ["systemctl", "stop", *services],
            capture_output=True, text=True, timeout=15, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"could not stop legacy AURUS services: {exc}") from exc
    remaining = active_legacy_services()
    if result.returncode != 0 or remaining:
        detail = (result.stderr or result.stdout).strip()[:300]
        raise RuntimeError(
            "legacy AURUS services still own hardware; run: sudo systemctl stop "
            + " ".join(remaining or services)
            + (f" ({detail})" if detail else "")
        )


def _process_owner(pid: int, devices: tuple[str, ...], proc_root: Path = Path("/proc")) -> ProcessOwner | None:
    try:
        process = proc_root / str(pid)
        status = (process / "status").read_text(encoding="utf-8", errors="replace")
        values = {}
        for line in status.splitlines():
            if ":" in line:
                key, value = line.split(":", 1)
                values[key] = value.strip()
        command = (process / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace").strip()
        executable = os.readlink(process / "exe")
        return ProcessOwner(
            pid=pid,
            ppid=int(values.get("PPid", "0").split()[0]),
            uid=int(values.get("Uid", "-1").split()[0]),
            executable=executable,
            command=command,
            devices=devices,
        )
    except (OSError, ValueError, IndexError):
        return None


def device_owners(proc_root: Path = Path("/proc")) -> list[ProcessOwner]:
    """Find processes with open CSI camera or ALSA capture device nodes."""
    if os.name == "nt" or not proc_root.is_dir():
        return []
    device_nodes = {
        str(Path(path).resolve())
        for pattern in ("/dev/video*", "/dev/media*", "/dev/snd/pcm*C*c")
        for path in glob.glob(pattern)
    }
    if not device_nodes:
        return []
    owners = []
    for entry in proc_root.iterdir():
        if not entry.name.isdigit() or int(entry.name) == os.getpid():
            continue
        opened = set()
        try:
            for fd in (entry / "fd").iterdir():
                try:
                    target = str(Path(os.readlink(fd)).resolve())
                except OSError:
                    continue
                if target in device_nodes:
                    opened.add(target)
        except OSError:
            continue
        if opened:
            owner = _process_owner(int(entry.name), tuple(sorted(opened)), proc_root)
            if owner:
                owners.append(owner)
    return owners


def is_reclaimable_owner(owner: ProcessOwner, project_root: Path, current_uid: int) -> bool:
    """Allow cleanup only for same-user AURUS processes or orphaned capture helpers."""
    if owner.uid != current_uid or owner.pid == os.getpid():
        return False
    command = owner.command.replace("\\", "/").lower()
    project = str(project_root).replace("\\", "/").lower().rstrip("/")
    executable_name = Path(owner.executable).name.lower()
    command_name = Path(owner.command.split()[0]).name.lower() if owner.command else ""
    project_python = project in command and ("python" in executable_name or ".py" in command)
    helper = executable_name in CAPTURE_HELPERS or command_name in CAPTURE_HELPERS
    return project_python or (helper and owner.ppid == 1)


def reclaim_stale_devices(project_root: Path, logger: logging.Logger) -> list[ProcessOwner]:
    """Release stale AURUS camera/mic owners before constructing hardware services."""
    if os.name == "nt" or os.getenv("AURUS_RECLAIM_DEVICES", "true").lower() != "true":
        return []
    current_uid = os.getuid()
    owners = device_owners()
    reclaimable = [owner for owner in owners if is_reclaimable_owner(owner, project_root, current_uid)]
    for owner in reclaimable:
        logger.warning(
            "Releasing stale hardware owner PID %s (%s) on %s",
            owner.pid, owner.command[:160], ", ".join(owner.devices),
        )
        try:
            os.kill(owner.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError) as exc:
            logger.warning("Could not terminate PID %s: %s", owner.pid, exc)

    try:
        grace = max(0.5, min(8.0, float(os.getenv("AURUS_RECLAIM_GRACE_SECONDS", "3"))))
    except ValueError:
        grace = 3.0
    deadline = time.monotonic() + grace
    remaining = {owner.pid for owner in reclaimable}
    while remaining and time.monotonic() < deadline:
        remaining = {pid for pid in remaining if Path(f"/proc/{pid}").exists()}
        if remaining:
            time.sleep(0.1)
    for pid in remaining:
        try:
            logger.warning("Force-closing stale AURUS hardware owner PID %s", pid)
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError) as exc:
            logger.warning("Could not force-close PID %s: %s", pid, exc)

    kill_deadline = time.monotonic() + 1.0
    while remaining and time.monotonic() < kill_deadline:
        remaining = {pid for pid in remaining if Path(f"/proc/{pid}").exists()}
        if remaining:
            time.sleep(0.05)
    unresolved = device_owners()
    stale_remaining = [
        owner for owner in unresolved
        if is_reclaimable_owner(owner, project_root, current_uid)
    ]
    unrelated = [
        owner for owner in unresolved
        if not is_reclaimable_owner(owner, project_root, current_uid)
    ]
    for owner in stale_remaining:
        logger.warning(
            "Stale AURUS owner PID %s did not release %s",
            owner.pid, ", ".join(owner.devices),
        )
    for owner in unrelated:
        logger.warning(
            "Hardware is still owned by unrelated PID %s (%s) on %s; it was not terminated",
            owner.pid, owner.command[:160], ", ".join(owner.devices),
        )
    if (stale_remaining or unrelated) and os.getenv(
        "AURUS_DEVICE_RECLAIM_STRICT", "false"
    ).lower() == "true":
        raise RuntimeError(
            "camera/microphone still owned by processes: "
            + "; ".join(
                f"PID {owner.pid} {owner.command[:80]}"
                for owner in stale_remaining + unrelated
            )
        )
    return reclaimable


def acquire_runtime_lock(project_root: Path | None = None, logger: logging.Logger | None = None):
    """Prevent duplicate runtimes, with guarded systemd takeover of a manual run."""
    if os.name == "nt":
        return None
    import fcntl

    path = Path(tempfile.gettempdir()) / "aurus-runtime.lock"
    for attempt in range(2):
        handle = path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            handle.seek(0)
            owner_text = handle.read().strip()
            handle.close()
            try:
                owner_pid = int(owner_text)
            except ValueError:
                owner_pid = 0
            systemd_takeover = bool(os.getenv("INVOCATION_ID")) and os.getenv(
                "AURUS_RECLAIM_DEVICES", "true"
            ).lower() == "true"
            owner = _process_owner(owner_pid, ()) if owner_pid > 0 else None
            can_replace = bool(
                attempt == 0
                and systemd_takeover
                and owner is not None
                and project_root is not None
                and is_reclaimable_owner(owner, project_root, os.getuid())
            )
            if not can_replace:
                raise RuntimeError(
                    f"another AURUS process is already running (PID {owner_text or 'unknown'}); "
                    "do not run python run.py while the aurus systemd service is active"
                ) from exc
            if logger:
                logger.warning(
                    "Systemd is replacing manual AURUS runtime PID %s before hardware startup",
                    owner_pid,
                )
            try:
                os.kill(owner_pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            deadline = time.monotonic() + 4.0
            while Path(f"/proc/{owner_pid}").exists() and time.monotonic() < deadline:
                time.sleep(0.1)
            if Path(f"/proc/{owner_pid}").exists():
                try:
                    os.kill(owner_pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            time.sleep(0.1)
            continue
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
        handle.flush()
        return handle
    raise RuntimeError("could not acquire the AURUS runtime lock after stale-owner cleanup")


def configure_logging(root: Path) -> None:
    log_dir = root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    file_handler = RotatingFileHandler(log_dir / "aurus.log", maxBytes=1_000_000, backupCount=3)
    file_handler.setFormatter(formatter)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logging.basicConfig(level=logging.INFO, handlers=[file_handler, console_handler])


def main() -> None:
    root = Path(__file__).resolve().parent
    configure_logging(root)
    logger = logging.getLogger("aurus")
    legacy = active_legacy_services()
    if legacy:
        try:
            stop_legacy_services(legacy, logger)
        except RuntimeError as exc:
            raise SystemExit(str(exc)) from exc
    try:
        runtime_lock = acquire_runtime_lock(root, logger)
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc
    try:
        reclaim_stale_devices(root, logger)
    except RuntimeError as exc:
        if runtime_lock is not None:
            runtime_lock.close()
        raise SystemExit(str(exc)) from exc
    runtime = RobotRuntime(root)
    app, socketio, start_web_tasks, stop_web_tasks = create_app(runtime)
    stopped = False

    def shutdown():
        nonlocal stopped
        if stopped:
            return
        stopped = True
        stop_web_tasks()
        runtime.stop()

    atexit.register(shutdown)
    runtime.start()
    start_web_tasks()
    logger.info(
        "AURUS Evaluation Edition ready on %s:%s",
        os.getenv("AURUS_HOST", "0.0.0.0"),
        os.getenv("AURUS_PORT", "5000"),
    )
    try:
        socketio.run(
            app,
            host=os.getenv("AURUS_HOST", "0.0.0.0"),
            port=int(os.getenv("AURUS_PORT", "5000")),
            debug=False,
            allow_unsafe_werkzeug=True,
        )
    finally:
        shutdown()
        if runtime_lock is not None:
            runtime_lock.close()


if __name__ == "__main__":
    main()
