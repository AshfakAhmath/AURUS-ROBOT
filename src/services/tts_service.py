"""Serialized local TTS with Piper, eSpeak, and visual-only fallback.

Audio output can be routed to the Pi's local speaker or streamed over
SocketIO to the connected PC's browser.  Set ``AURUS_AUDIO_OUTPUT`` to
``pi`` (local only), ``browser`` (always stream), or ``auto`` (try
local first, fall back to browser on failure).
"""

from __future__ import annotations

import base64
import logging
import os
from pathlib import Path
import importlib.util
import queue
import shutil
import subprocess
import tempfile
import threading
import wave
from typing import Any, Callable

import numpy as np

logger = logging.getLogger("aurus.tts")

# Type alias for the callback that sends audio to the browser dashboard.
AudioEmitCallback = Callable[[str, dict[str, Any]], None]


class TTSService:
    AUDIO_MODES = ("pi", "browser", "auto")

    def __init__(
        self,
        piper_model: str | Path | None = None,
        playback_active: threading.Event | None = None,
        voice: str | None = None,
        volume_percent: int | None = None,
        gain_db: float | None = None,
        playback_device: str | None = None,
        audio_output: str | None = None,
        audio_emit: AudioEmitCallback | None = None,
    ):
        self.piper_model = Path(piper_model) if piper_model else None
        self.playback_active = playback_active or threading.Event()
        self.voice = voice or os.getenv("AURUS_TTS_VOICE", "en-US-GuyNeural")
        configured_volume = (
            volume_percent
            if volume_percent is not None
            else os.getenv("AURUS_TTS_VOLUME_PERCENT", "40")
        )
        try:
            self.volume_percent = max(-90, min(100, int(configured_volume)))
        except (TypeError, ValueError):
            self.volume_percent = 40
        configured_gain = gain_db if gain_db is not None else os.getenv("AURUS_TTS_GAIN_DB", "6")
        try:
            self.gain_db = max(-20.0, min(24.0, float(configured_gain)))
        except (TypeError, ValueError):
            self.gain_db = 6.0
        self.playback_device = playback_device or os.getenv("ALSA_PLAYBACK_DEVICE", "").strip()

        # --- Audio output routing ---
        raw_mode = (audio_output or os.getenv("AURUS_AUDIO_OUTPUT", "auto")).strip().lower()
        self.audio_output: str = raw_mode if raw_mode in self.AUDIO_MODES else "auto"
        self._audio_emit: AudioEmitCallback | None = audio_emit
        # Tracks which output was actually used for the most recent utterance.
        self.active_output: str = "pi"

        self._queue: queue.Queue[tuple[str, Callable[[], None] | None] | None] = queue.Queue(maxsize=8)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._piper_voice = None
        self.backend = self._select_backend()
        self.error = "" if self.backend != "visual" else "No local TTS executable available"

    # ------------------------------------------------------------------
    # Audio output routing
    # ------------------------------------------------------------------

    def set_audio_emit(self, callback: AudioEmitCallback | None) -> None:
        """Install or replace the SocketIO emit callback (called from app.py)."""
        self._audio_emit = callback

    def set_audio_output(self, mode: str) -> bool:
        """Switch audio output mode at runtime.  Returns True if accepted."""
        mode = mode.strip().lower()
        if mode not in self.AUDIO_MODES:
            return False
        self.audio_output = mode
        logger.info("Audio output switched to '%s'.", mode)
        return True

    def _emit_to_browser(self, audio_path: str) -> bool:
        """Send an audio file to the browser dashboard via SocketIO.

        Returns True if the audio was emitted successfully.
        """
        if self._audio_emit is None:
            return False
        try:
            with open(audio_path, "rb") as fh:
                audio_bytes = fh.read()
            if not audio_bytes:
                return False
            ext = Path(audio_path).suffix.lstrip(".").lower()
            mime_format = "wav" if ext == "wav" else "mpeg"
            payload = {
                "data": base64.b64encode(audio_bytes).decode("ascii"),
                "format": mime_format,
            }
            self._audio_emit("play_audio", payload)
            self.active_output = "browser"
            logger.info("Audio streamed to browser (%s, %d bytes).", ext, len(audio_bytes))
            return True
        except Exception as exc:
            logger.warning("Failed to stream audio to browser: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Backend selection
    # ------------------------------------------------------------------

    def _select_backend(self) -> str:
        # Prefer deterministic local synthesis. Edge TTS is retained only as a
        # fallback so chat and wake acknowledgement do not depend on a network
        # round trip before audio playback begins.
        if importlib.util.find_spec("piper") and self.piper_model and self.piper_model.exists():
            return "piper"
        if shutil.which("edge-tts") and self._edge_player():
            return "edge-tts"
        if shutil.which("espeak-ng"):
            return "espeak-ng"
        if shutil.which("espeak"):
            return "espeak"
        return "visual"

    @staticmethod
    def _edge_player() -> str | None:
        for name in ("mpg123", "ffplay", "mplayer"):
            if shutil.which(name):
                return name
        return None

    @property
    def healthy(self) -> bool:
        return self.backend != "visual"

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="tts-service", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self.playback_active.clear()

    def speak(self, text: str, on_complete: Callable[[], None] | None = None) -> bool:
        clean = " ".join(text.strip().split())[:400]
        if not clean:
            return False
        try:
            self._queue.put_nowait((clean, on_complete))
            return True
        except queue.Full:
            self.error = "TTS queue full"
            return False

    # ------------------------------------------------------------------
    # Local playback helpers
    # ------------------------------------------------------------------

    def _play_wav(self, path: str) -> None:
        if shutil.which("aplay"):
            command = ["aplay", "-q"]
            if self.playback_device:
                command.extend(["-D", self.playback_device])
            command.append(path)
            subprocess.run(command, check=True, timeout=30)
        elif os.name == "nt":
            import winsound

            winsound.PlaySound(path, winsound.SND_FILENAME)
        else:
            raise RuntimeError("no WAV player available")

    def _speak_piper(self, text: str) -> None:
        if self._piper_voice is None:
            from piper import PiperVoice

            self._piper_voice = PiperVoice.load(str(self.piper_model))
        handle = tempfile.NamedTemporaryFile(prefix="aurus-", suffix=".wav", delete=False)
        handle.close()
        try:
            with wave.open(handle.name, "wb") as wav_file:
                self._piper_voice.synthesize_wav(text, wav_file)
            self._amplify_wav(handle.name)
            self._play_or_stream(handle.name)
        finally:
            try:
                os.unlink(handle.name)
            except OSError:
                pass

    def _amplify_wav(self, path: str) -> None:
        if self.volume_percent == 0 and self.gain_db == 0:
            return
        with wave.open(path, "rb") as source:
            params = source.getparams()
            if params.sampwidth != 2:
                return
            frames = source.readframes(params.nframes)
        samples = np.frombuffer(frames, dtype=np.int16).astype(np.float32)
        gain = max(0.1, 1.0 + self.volume_percent / 100.0) * (10.0 ** (self.gain_db / 20.0))
        boosted = np.clip(samples * gain, -32768, 32767).astype(np.int16)
        with wave.open(path, "wb") as output:
            output.setparams(params)
            output.writeframes(boosted.tobytes())

    def _speak_edge_tts(self, text: str) -> None:
        handle = tempfile.NamedTemporaryFile(prefix="aurus-", suffix=".mp3", delete=False)
        handle.close()
        wav_path: str | None = None
        try:
            player = self._edge_player()
            postprocess_wav = player == "mpg123" and bool(shutil.which("aplay"))
            subprocess.run(
                [
                    "edge-tts",
                    "--voice",
                    self.voice,
                    f"--volume={0 if postprocess_wav else self.volume_percent:+d}%",
                    "--text",
                    text,
                    "--write-media",
                    handle.name,
                ],
                check=True,
                timeout=30,
            )
            if postprocess_wav:
                wav_handle = tempfile.NamedTemporaryFile(prefix="aurus-edge-", suffix=".wav", delete=False)
                wav_path = wav_handle.name
                wav_handle.close()
                subprocess.run(
                    ["mpg123", "-q", "-w", wav_path, handle.name], check=True, timeout=30
                )
                self._amplify_wav(wav_path)
                self._play_or_stream(wav_path)
            elif self.audio_output == "browser":
                # Browser mode: stream the MP3 directly without local playback.
                self._emit_to_browser(handle.name)
            elif self.audio_output == "auto":
                # Auto mode: try local first, fall back to browser on failure.
                try:
                    self._play_mp3_locally(player, handle.name)
                    self.active_output = "pi"
                except Exception:
                    if not self._emit_to_browser(handle.name):
                        raise
            else:
                # Pi-only mode.
                self._play_mp3_locally(player, handle.name)
                self.active_output = "pi"
        finally:
            try:
                os.unlink(handle.name)
            except OSError:
                pass
            if wav_path:
                try:
                    os.unlink(wav_path)
                except OSError:
                    pass

    def _play_mp3_locally(self, player: str | None, path: str) -> None:
        """Play an MP3 file through the Pi's local speaker."""
        if player == "mpg123":
            subprocess.run(["mpg123", "-q", path], check=True, timeout=30)
        elif player == "ffplay":
            subprocess.run(
                [
                    "ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet",
                    "-af", f"volume={self.gain_db}dB", path,
                ],
                check=True,
                timeout=30,
            )
        elif player == "mplayer":
            subprocess.run(
                ["mplayer", "-really-quiet", "-af", f"volume={self.gain_db}", path],
                check=True,
                timeout=30,
            )
        else:
            raise RuntimeError("edge-tts generated audio but no MP3 player is installed")

    # ------------------------------------------------------------------
    # Unified play-or-stream dispatcher
    # ------------------------------------------------------------------

    def _play_or_stream(self, audio_path: str) -> None:
        """Route a WAV/MP3 file based on the current ``audio_output`` mode.

        * ``pi``      – play locally only.
        * ``browser`` – stream to browser only.
        * ``auto``    – try local first; on failure stream to browser.
        """
        if self.audio_output == "browser":
            if not self._emit_to_browser(audio_path):
                logger.warning("Browser emit failed; attempting local playback.")
                self._play_wav(audio_path)
                self.active_output = "pi"
            return

        if self.audio_output == "pi":
            self._play_wav(audio_path)
            self.active_output = "pi"
            return

        # auto mode
        try:
            self._play_wav(audio_path)
            self.active_output = "pi"
        except Exception as exc:
            logger.warning("Local playback failed (%s); streaming to browser.", exc)
            if not self._emit_to_browser(audio_path):
                raise

    # ------------------------------------------------------------------
    # Synthesis dispatcher and worker loop
    # ------------------------------------------------------------------

    def _speak(self, text: str) -> None:
        if self.backend == "edge-tts":
            self._speak_edge_tts(text)
        elif self.backend == "piper":
            self._speak_piper(text)
        elif self.backend in ("espeak-ng", "espeak"):
            # espeak does not write a file – for browser mode we generate a WAV
            # via --stdout and stream that instead.
            if self.audio_output in ("browser", "auto"):
                self._speak_espeak_to_file(text)
            else:
                amplitude = max(0, min(200, 100 + self.volume_percent))
                subprocess.run([self.backend, "-a", str(amplitude), text], check=True, timeout=30)
                self.active_output = "pi"

    def _speak_espeak_to_file(self, text: str) -> None:
        """Run espeak/espeak-ng writing to a temp WAV so it can be streamed."""
        handle = tempfile.NamedTemporaryFile(prefix="aurus-espeak-", suffix=".wav", delete=False)
        handle.close()
        try:
            amplitude = max(0, min(200, 100 + self.volume_percent))
            subprocess.run(
                [self.backend, "-a", str(amplitude), "-w", handle.name, text],
                check=True,
                timeout=30,
            )
            self._play_or_stream(handle.name)
        finally:
            try:
                os.unlink(handle.name)
            except OSError:
                pass

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                item = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            if item is None:
                break
            text, on_complete = item
            self.playback_active.set()
            try:
                self._speak(text)
                self.error = ""
            except Exception as exc:
                self.error = str(exc)[:200]
                print(f"[TTSService] {self.backend} playback failed: {self.error}")
                if self.backend == "edge-tts" and importlib.util.find_spec("piper") and self.piper_model and self.piper_model.exists():
                    self.backend = "piper"
                else:
                    fallback = "espeak-ng" if shutil.which("espeak-ng") else "espeak" if shutil.which("espeak") else None
                    self.backend = fallback or "visual"
                    self._piper_voice = None
                if self.backend != "visual":
                    try:
                        self._speak(text)
                        self.error = ""
                        print(f"[TTSService] Recovered using {self.backend}.")
                    except Exception as fallback_exc:
                        self.error = str(fallback_exc)[:200]
                        print(f"[TTSService] Fallback playback failed: {self.error}")
                        self.backend = "visual"
            finally:
                self.playback_active.clear()
            if on_complete is not None:
                try:
                    on_complete()
                except Exception:
                    pass
