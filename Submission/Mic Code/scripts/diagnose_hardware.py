"""Read-only device report with optional isolated camera/audio tests.

Run without arguments while AURUS is active to inspect ownership and health.
Stop AURUS, then pass ``--active`` to open each device independently.
"""

from __future__ import annotations

import argparse
import glob
import json
import math
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.request
import wave

import numpy as np

if os.name != "nt":
    import grp
    import pwd


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:
    pass

KNOWN_SERVICES = (
    "aurus.service",
    "aurus-assistant.service",
    "aurus-autonomy.service",
    "aurus-coordinator.service",
    "aurus-hardware.service",
    "aurus-vision.service",
    "aurus-web.service",
)


def run(command: list[str], timeout: float = 15.0) -> dict:
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=timeout, check=False
        )
        output = "\n".join(
            part.strip() for part in (result.stdout, result.stderr) if part.strip()
        )
        return {"exit": result.returncode, "output": output[:8000] or "none"}
    except Exception as exc:
        return {"exit": None, "output": f"{type(exc).__name__}: {exc}"}


def device_info(patterns: tuple[str, ...]) -> list[dict]:
    result = []
    for pattern in patterns:
        for name in sorted(glob.glob(pattern)):
            try:
                details = os.stat(name)
                result.append(
                    {
                        "path": name,
                        "mode": stat.filemode(details.st_mode),
                        "owner": pwd.getpwuid(details.st_uid).pw_name,
                        "group": grp.getgrgid(details.st_gid).gr_name,
                    }
                )
            except Exception as exc:
                result.append({"path": name, "error": str(exc)})
    return result


def runtime_health() -> dict | str:
    try:
        with urllib.request.urlopen("http://127.0.0.1:5000/health", timeout=3) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        return f"unavailable: {exc}"


def service_state() -> dict:
    if not shutil.which("systemctl"):
        return {"active": False, "detail": "systemctl unavailable"}
    active_services = []
    details = {}
    for service in KNOWN_SERVICES:
        active = run(["systemctl", "is-active", service])
        if active["output"] == "active":
            active_services.append(service)
        details[service] = run(
            [
                "systemctl",
                "show",
                service,
                "--property=MainPID,User,Group,SupplementaryGroups,ExecMainStatus,SubState,FragmentPath,WorkingDirectory,ExecStart,EnvironmentFiles",
            ]
        )
    return {"active": bool(active_services), "active_services": active_services, "detail": details}


def device_owners() -> dict | str:
    if not shutil.which("fuser") or os.name == "nt":
        return "fuser unavailable"
    devices = []
    for pattern in ("/dev/video*", "/dev/media*", "/dev/snd/pcm*"):
        devices.extend(sorted(glob.glob(pattern)))
    return run(["fuser", "-v", *devices]) if devices else "no device nodes"


def active_microphone_test() -> dict:
    from src.services.speech_service import _discover_arecord_device

    selected = _discover_arecord_device()
    device = selected[0] if selected else "default"
    description = selected[1] if selected else "ALSA default"
    if not shutil.which("arecord"):
        return {"ok": False, "error": "arecord is not installed"}
    print(f"Speak normally for three seconds. Capturing from {device} ({description})...", flush=True)
    with tempfile.TemporaryDirectory(prefix="aurus-diagnostic-") as directory:
        path = Path(directory) / "microphone.wav"
        command = [
            "arecord", "-q", "-D", device, "-d", "3", "-f", "S16_LE",
            "-c", "1", "-r", "16000", "-t", "wav", str(path),
        ]
        capture = run(command, timeout=8)
        if capture["exit"] != 0 or not path.is_file():
            return {"ok": False, "device": device, "capture": capture}
        with wave.open(str(path), "rb") as source:
            samples = np.frombuffer(source.readframes(source.getnframes()), dtype=np.int16)
        if samples.size == 0:
            return {"ok": False, "device": device, "error": "capture contained no samples"}
        floats = samples.astype(np.float64)
        rms = float(math.sqrt(np.mean(floats * floats)))
        peak = int(np.max(np.abs(floats)))
        clipping = float(np.mean(np.abs(floats) >= 32760) * 100.0)
        return {
            "ok": rms >= 100.0,
            "device": device,
            "description": description,
            "rms": round(rms, 1),
            "peak": peak,
            "clipping_percent": round(clipping, 3),
            "assessment": (
                "good signal"
                if rms >= 300.0 and peak >= 1500
                else "weak signal; raise the capture mixer or move closer"
                if rms >= 100.0
                else "silent/wrong input/muted"
            ),
        }


def active_camera_test() -> dict:
    command = shutil.which("rpicam-still") or shutil.which("libcamera-still")
    if not command:
        return {"ok": False, "error": "rpicam-still/libcamera-still is unavailable"}
    with tempfile.TemporaryDirectory(prefix="aurus-diagnostic-") as directory:
        path = Path(directory) / "camera.jpg"
        result = run([command, "-n", "-t", "1000", "-o", str(path)], timeout=12)
        size = path.stat().st_size if path.is_file() else 0
        return {"ok": result["exit"] == 0 and size > 1024, "bytes": size, "command": result}


def active_speaker_test() -> dict:
    if not shutil.which("aplay"):
        return {"ok": False, "error": "aplay is not installed"}
    device = os.getenv("ALSA_PLAYBACK_DEVICE", "").strip()
    print("Playing a two-second -6 dB diagnostic tone. Note whether it is clearly audible...", flush=True)
    with tempfile.TemporaryDirectory(prefix="aurus-diagnostic-") as directory:
        path = Path(directory) / "tone.wav"
        rate = 48000
        positions = np.arange(rate * 2, dtype=np.float64)
        samples = (np.sin(2 * np.pi * 440.0 * positions / rate) * 16384).astype(np.int16)
        with wave.open(str(path), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(rate)
            output.writeframes(samples.tobytes())
        command = ["aplay", "-q"]
        if device:
            command.extend(["-D", device])
        command.append(str(path))
        result = run(command, timeout=8)
        return {"ok": result["exit"] == 0, "device": device or "default", "command": result}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--active",
        action="store_true",
        help="open the camera, capture microphone audio, and play a diagnostic tone",
    )
    args = parser.parse_args()

    groups = (
        [grp.getgrgid(group_id).gr_name for group_id in os.getgroups()]
        if os.name != "nt"
        else []
    )
    service = service_state()
    report = {
        "user": pwd.getpwuid(os.getuid()).pw_name if os.name != "nt" else os.getenv("USERNAME"),
        "groups": groups,
        "python": sys.executable,
        "service": service,
        "runtime_health": runtime_health(),
        "configured": {
            "MIC_INDEX": os.getenv("MIC_INDEX", ""),
            "ALSA_CAPTURE_DEVICE": os.getenv("ALSA_CAPTURE_DEVICE", ""),
            "ALSA_PLAYBACK_DEVICE": os.getenv("ALSA_PLAYBACK_DEVICE", ""),
            "AURUS_WAKE_ENABLED": os.getenv("AURUS_WAKE_ENABLED", ""),
            "AURUS_TTS_VOLUME_PERCENT": os.getenv("AURUS_TTS_VOLUME_PERCENT", ""),
            "AURUS_TTS_GAIN_DB": os.getenv("AURUS_TTS_GAIN_DB", ""),
        },
        "camera_devices": device_info(("/dev/video*", "/dev/media*")) if os.name != "nt" else [],
        "sound_devices": device_info(("/dev/snd/*",)) if os.name != "nt" else [],
        "device_owners": device_owners(),
        "related_processes": run(["pgrep", "-af", "aurus|python.*run.py|rpicam|libcamera"])
        if shutil.which("pgrep")
        else "pgrep unavailable",
        "camera_list": run(["rpicam-hello", "--list-cameras"])
        if shutil.which("rpicam-hello")
        else "rpicam-hello unavailable",
        "capture_cards": run(["arecord", "-l"])
        if shutil.which("arecord")
        else "arecord unavailable",
        "playback_cards": run(["aplay", "-l"])
        if shutil.which("aplay")
        else "aplay unavailable",
        "mixer_controls": run(["amixer", "scontrols"])
        if shutil.which("amixer")
        else "amixer unavailable",
    }

    if args.active:
        if service["active"] or not isinstance(report["runtime_health"], str):
            report["active_tests"] = {
                "skipped": "Stop aurus.service and every manual run.py process before active tests."
            }
        else:
            report["active_tests"] = {
                "microphone": active_microphone_test(),
                "camera": active_camera_test(),
                "speaker": active_speaker_test(),
            }

    print("\n=== AURUS HARDWARE DIAGNOSTIC ===")
    print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
