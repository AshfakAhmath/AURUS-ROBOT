"""Read-only Raspberry Pi environment gate for the evaluation build."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:
    pass


def command_output(command: list[str]) -> str:
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=10, check=False
        )
        output = "\n".join(part.strip() for part in (result.stdout, result.stderr) if part.strip())
        return output or "none"
    except (OSError, subprocess.SubprocessError):
        return "unavailable"


def module(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def main() -> int:
    result = {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python": sys.version.split()[0],
        "os_release": command_output(["cat", "/etc/os-release"])
        if platform.system() == "Linux"
        else platform.system(),
        "camera_cli": command_output(["rpicam-hello", "--list-cameras"])
        if shutil.which("rpicam-hello")
        else "unavailable",
        "microphones": command_output(["arecord", "-l"])
        if shutil.which("arecord")
        else "unavailable",
        "speakers": command_output(["aplay", "-l"])
        if shutil.which("aplay")
        else "unavailable",
        "camera_owners": command_output(["fuser", "-v", "/dev/video0", "/dev/media0"])
        if shutil.which("fuser") and platform.system() == "Linux"
        else "unavailable",
        "mixer_controls": command_output(["amixer", "scontrols"])
        if shutil.which("amixer")
        else "unavailable",
        "modules": {
            name: module(name)
            for name in (
                "flask",
                "flask_socketio",
                "numpy",
                "cv2",
                "mediapipe",
                "picamera2",
                "pyaudio",
                "vosk",
                "sherpa_onnx",
                "piper",
                "groq",
            )
        },
        "models": {
            "gesture": (ROOT / "models" / "gesture_recognizer.task").is_file(),
            "vosk": (ROOT / "models" / "vosk-model-small-en-us-0.15").is_dir(),
            "wake_word": (
                ROOT
                / "models"
                / "sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01"
                / "aurus_keywords.txt"
            ).is_file(),
            "silero_vad": (ROOT / "models" / "silero_vad.onnx").is_file(),
            "piper": all(
                (ROOT / "models" / name).is_file()
                for name in ("en_US-lessac-medium.onnx", "en_US-lessac-medium.onnx.json")
            ),
        },
        "executables": {
            name: bool(shutil.which(name))
            for name in (
                "arecord", "aplay", "edge-tts", "mpg123", "ffplay", "mplayer", "espeak-ng"
            )
        },
    }
    if module("cv2"):
        import cv2

        result["opencv"] = {"version": cv2.__version__}

    result["gesture_assets_ready"] = all(
        (result["modules"]["cv2"], result["modules"]["mediapipe"], result["models"]["gesture"])
    )

    local_stt_ready = bool(result["modules"]["vosk"] and result["models"]["vosk"])
    groq_stt_ready = bool(result["modules"]["groq"] and os.getenv("GROQ_API_KEY", "").strip())
    capture_ready = bool(result["modules"]["pyaudio"] or result["executables"]["arecord"])
    result["voice_ready"] = capture_ready and (local_stt_ready or groq_stt_ready)
    result["wake_ready"] = bool(
        capture_ready and result["modules"]["sherpa_onnx"] and result["models"]["wake_word"]
    )
    edge_ready = bool(
        result["executables"]["edge-tts"]
        and any(result["executables"][name] for name in ("mpg123", "ffplay", "mplayer"))
    )
    local_tts_ready = bool(
        (result["modules"]["piper"] and result["models"]["piper"])
        or result["executables"]["espeak-ng"]
    )
    result["tts_ready"] = edge_ready or local_tts_ready
    result["groq_key_configured"] = bool(os.getenv("GROQ_API_KEY", "").strip())
    result["assistant_ready"] = bool(result["modules"]["groq"] and result["groq_key_configured"])
    print(json.dumps(result, indent=2))
    core_ok = all(
        result["modules"][name] for name in ("flask", "flask_socketio", "numpy", "cv2")
    )
    return 0 if core_ok else 1


if __name__ == "__main__":
    sys.exit(main())
