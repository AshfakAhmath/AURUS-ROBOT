"""Play a short phrase through the configured AURUS TTS path."""

from __future__ import annotations

from pathlib import Path
import os
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from src.services.tts_service import TTSService


def main() -> int:
    configured = Path(os.getenv("PIPER_MODEL_PATH", "models/en_US-lessac-medium.onnx"))
    model = configured if configured.is_absolute() else ROOT / configured
    service = TTSService(model, playback_device=os.getenv("ALSA_PLAYBACK_DEVICE", "").strip())
    print(f"Piper model: {model}")
    print(f"Backend: {service.backend}")
    print(f"Voice: {service.voice}")
    print(f"Software volume: {service.volume_percent:+d}%")
    print(f"Additional gain: {service.gain_db:+.1f} dB")
    print(f"Playback device: {service.playback_device or 'system default'}")
    if not service.healthy:
        print(f"TTS unavailable: {service.error}")
        return 1
    try:
        service._speak("AURUS text to speech volume test.")
        print("TTS playback completed.")
        return 0
    except Exception as exc:
        print(f"TTS playback failed: {str(exc)[:240]}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
