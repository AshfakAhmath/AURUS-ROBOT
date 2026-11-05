"""Live Groq synthesis for the predefined demo only; no persistent audio cache."""

import os
from pathlib import Path
import re
import tempfile
import wave

try:
    from groq import Groq
except ImportError:
    Groq = None


MODEL = "canopylabs/orpheus-v1-english"
VOICES = {"autumn", "diana", "hannah", "austin", "daniel", "troy"}


def speech_chunks(text, limit=200):
    """Respect Orpheus's character limit without truncating the spoken reply."""
    text = " ".join(text.split())
    if limit < 1:
        raise ValueError("Speech chunk limit must be positive")
    chunks = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
        window = text[:limit]
        boundaries = [match.end() for match in re.finditer(r"[.!?](?=\s)", window)]
        split = boundaries[-1] if boundaries and boundaries[-1] >= limit // 3 else window.rfind(" ")
        if split <= 0:
            split = limit
        chunks.append(text[:split].strip())
        text = text[split:].strip()
    return chunks


class GroqSpeechSynthesizer:
    def __init__(self, api_key=None, model=None, voice=None, client=None):
        self.model = model or os.getenv("AURUS_DEMO_TTS_MODEL", MODEL)
        self.voice = (voice or os.getenv("AURUS_DEMO_TTS_VOICE", "troy")).strip().lower()
        self.error = ""
        self._client = None
        if self.model != MODEL:
            self.error = f"Groq English TTS requires model {MODEL}"
        elif self.voice not in VOICES:
            self.error = "Invalid Groq English voice; choose autumn, diana, hannah, austin, daniel or troy"
        elif client is not None:
            self._client = client
        elif not (api_key or os.getenv("GROQ_API_KEY", "")).strip():
            self.error = "GROQ_API_KEY is not configured for speech"
        elif Groq is None:
            self.error = "Groq SDK is not installed; install the project requirements"
        else:
            try:
                self._client = Groq(api_key=api_key or os.getenv("GROQ_API_KEY", ""), timeout=15.0, max_retries=1)
            except Exception:
                self.error = "Groq speech client initialization failed; check SDK and key configuration"

    @property
    def ready(self):
        return self._client is not None

    def synthesize(self, text, destination, cancelled=None):
        if not self.ready:
            raise RuntimeError(self.error)
        chunks = speech_chunks(text)
        if not chunks:
            raise ValueError("Speech text is empty")
        parts, audio_format = [], None
        try:
            with tempfile.TemporaryDirectory(prefix="aurus-groq-speech-") as scratch:
                for index, chunk in enumerate(chunks):
                    if cancelled is not None and cancelled.is_set():
                        raise InterruptedError("Demo speech cancelled")
                    path = Path(scratch) / f"part-{index}.wav"
                    response = self._client.audio.speech.create(
                        model=self.model, voice=self.voice, input=chunk, response_format="wav",
                    )
                    response.write_to_file(str(path))
                    if cancelled is not None and cancelled.is_set():
                        raise InterruptedError("Demo speech cancelled")
                    with wave.open(str(path), "rb") as source:
                        fmt = (source.getnchannels(), source.getsampwidth(), source.getframerate())
                        frames = source.readframes(source.getnframes())
                    if fmt[0] not in (1, 2) or fmt[1] != 2 or fmt[2] <= 0 or not frames:
                        raise ValueError("Speech response is not usable 16-bit PCM WAV")
                    if audio_format is not None and audio_format != fmt:
                        raise ValueError("Groq speech chunks have incompatible audio formats")
                    audio_format = fmt
                    parts.append(frames)
                channels, width, rate = audio_format
                pause = bytes(int(rate * 0.08) * channels * width)
                with wave.open(str(destination), "wb") as output:
                    output.setnchannels(channels)
                    output.setsampwidth(width)
                    output.setframerate(rate)
                    output.writeframes(pause.join(parts))
            self.error = ""
        except InterruptedError:
            raise
        except Exception as exc:
            # Do not expose SDK request bodies/headers, the API key or user text.
            status = getattr(exc, "status_code", None)
            details = getattr(exc, "body", {})
            if isinstance(details, dict):
                details = details.get("error", details)
            terms_required = isinstance(details, dict) and (
                details.get("code") == "model_terms_required"
                or "requires terms acceptance" in str(details.get("message", "")).lower()
            )
            if status == 400 and terms_required:
                self.error = "Groq demo speech HTTP 400: model terms required; the API key's organization admin must review/accept Orpheus terms in Groq Console"
            elif status == 400:
                self.error = "Groq demo speech HTTP 400: request rejected; check Orpheus model terms/access and demo model/voice settings in Groq Console"
            elif status in (401, 403):
                self.error = "Groq speech access denied; check the replacement API key and Orpheus model permissions/terms in Groq Console"
            elif status == 429:
                self.error = "Groq speech rate limit or quota reached; check account limits"
            elif status == 404:
                self.error = "Groq demo speech HTTP 404: model or endpoint unavailable; check Orpheus availability and SDK"
            elif type(exc).__name__ == "APITimeoutError":
                self.error = "Groq demo speech timed out; check internet connection and retry"
            elif type(exc).__name__ == "APIConnectionError":
                self.error = "Groq demo speech connection failed; check internet, DNS and TLS"
            elif isinstance(exc, (ValueError, wave.Error, EOFError)):
                self.error = "Groq speech returned invalid or incompatible WAV audio"
            else:
                self.error = "Groq speech request failed; check network, model access and SDK installation"
            raise RuntimeError(self.error) from None

    def close(self):
        if self._client is not None:
            close = getattr(self._client, "close", None)
            if close:
                close()
