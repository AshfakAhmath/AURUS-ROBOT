"""Generate a Groq speech test file only; no GPIO, motors or playback."""

import argparse
from pathlib import Path
import sys
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
from src.services.groq_tts_service import GroqSpeechSynthesizer


def main():
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--text", default="Hello! I am AURUS. Welcome to the Faculty of Information Technology Expo.")
    parser.add_argument("--output", type=Path, default=ROOT / "logs" / "groq-tts-test.wav")
    args = parser.parse_args()
    speech = GroqSpeechSynthesizer()
    try:
        if not speech.ready:
            raise RuntimeError(speech.error)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        speech.synthesize(args.text, args.output)
        with wave.open(str(args.output), "rb") as source:
            duration = source.getnframes() / source.getframerate()
        print(f"Groq speech generated: {args.output.resolve()}")
        print(f"Model: {speech.model}; voice: {speech.voice}; duration: {duration:.1f} seconds")
        print("No audio played and no robot hardware initialized.")
        return 0
    except Exception as exc:
        print(f"Speech test failed: {exc}", file=sys.stderr)
        return 1
    finally:
        speech.close()


if __name__ == "__main__":
    raise SystemExit(main())
