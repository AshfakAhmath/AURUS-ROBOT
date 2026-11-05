"""Reviewed FIT Expo narration and validated, prerecorded PCM audio."""

import hashlib
import json
from pathlib import Path
import wave


DEMO_CLIPS = {
    "introduction": "Hello everyone! I'm AURUS, a robotic companion built by Team TechTitans. I'm a working prototype that brings together movement, vision, voice, and artificial intelligence. Welcome to the Faculty of Information Technology Expo!",
    "movement": "Let me introduce myself with a little movement. My ultrasonic sensors help me check for nearby obstacles.",
    "turn_left": "A small turn to the left.",
    "turn_right": "And a small turn to the right. Hello, everyone!",
    "capabilities": "You can talk to me and ask questions using my Groq-powered artificial intelligence. I can use my camera to observe my surroundings, recognize people I have learned, and remember facts you choose to share.",
    "closing": "I'm still a prototype, so my team is continuing to improve me. Thank you for visiting, and enjoy the FIT Expo!",
    "blocked": "There isn't enough clear space for my movement demonstration, so I'll continue my introduction from here.",
}

DEMO_ORDER = ("introduction", "movement", "turn_left", "turn_right", "capabilities", "closing")


class DemoAudioCache:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.clips = {}

    def validate(self):
        """Reject missing, stale or corrupt assets before any demo can move."""
        manifest = json.loads((self.directory / "manifest.json").read_text(encoding="utf-8"))
        clips = {}
        for key, text in DEMO_CLIPS.items():
            entry = manifest["clips"][key]
            path = self.directory / (key + ".wav")
            if entry["text"] != text or entry["sha256"] != hashlib.sha256(path.read_bytes()).hexdigest():
                raise ValueError(f"Cached demo audio is stale or corrupt: {key}")
            with wave.open(str(path), "rb") as source:
                duration = source.getnframes() / source.getframerate()
                if source.getsampwidth() != 2 or source.getnchannels() != 1 or not 0 < duration < 40:
                    raise ValueError(f"Invalid demo PCM audio: {key}")
            clips[key] = {"path": path, "duration": duration, "text": text}
        self.clips = clips
        return clips
