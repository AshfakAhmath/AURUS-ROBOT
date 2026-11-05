"""Verify Groq demo narration without starting the robot or retaining audio."""

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
from src.services.demo_script import DEMO_CLIPS
from src.services.groq_tts_service import GroqSpeechSynthesizer
from src.services.tts_service import TTSService


def main():
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--play", action="store_true", help="Also play through ALSA on the Pi")
    parser.add_argument("--device", default="", help="Optional ALSA playback device, e.g. plughw:2,0")
    args = parser.parse_args()
    speech = GroqSpeechSynthesizer()
    try:
        if not speech.ready:
            raise RuntimeError(speech.error)
        with tempfile.TemporaryDirectory(prefix="aurus-demo-check-") as scratch:
            path = Path(scratch) / "introduction.wav"
            speech.synthesize(DEMO_CLIPS["introduction"], path)
            TTSService._boost_demo_audio(path)
            with wave.open(str(path), "rb") as source:
                duration = source.getnframes() / source.getframerate()
            print(f"SUCCESS: Groq synthesized the introduction; model={speech.model}, voice={speech.voice}, duration={duration:.1f}s")
            if args.play:
                player = shutil.which("aplay")
                if not player:
                    raise RuntimeError("ALSA aplay is unavailable; run --play on the Pi")
                device = args.device or os.getenv("ALSA_PLAYBACK_DEVICE", "").strip()
                command = [player, "-q"]
                if device:
                    command.extend(["-D", device])
                subprocess.run(command + [str(path)], check=True, timeout=45)
                print("Playback command completed. Confirm that you heard the introduction.")
        print("Temporary audio deleted. No robot runtime, GPIO or motors initialized.")
        return 0
    except Exception as exc:
        print(f"Demo speech check failed: {exc}", file=sys.stderr)
        return 1
    finally:
        speech.close()


if __name__ == "__main__":
    raise SystemExit(main())
