"""Serialized local TTS with Piper, eSpeak, and visual-only fallback."""

from __future__ import annotations

import os
from pathlib import Path
import importlib.util
import queue
import shutil
import subprocess
import tempfile
import threading
import wave
from typing import Callable

import numpy as np


class TTSService:
    def __init__(
        self,
        piper_model: str | Path | None = None,
        playback_active: threading.Event | None = None,
        voice: str | None = None,
        volume_percent: int | None = None,
        gain_db: float | None = None,
        playback_device: str | None = None,
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
        self._queue: queue.Queue[tuple[str, Callable[[], None] | None] | None] = queue.Queue(maxsize=8)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._piper_voice = None
        self.backend = self._select_backend()
        self.error = "" if self.backend != "visual" else "No local TTS executable available"

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
            self._play_wav(handle.name)
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
                self._play_wav(wav_path)
            elif player == "mpg123":
                subprocess.run(["mpg123", "-q", handle.name], check=True, timeout=30)
            elif player == "ffplay":
                subprocess.run(
                    [
                        "ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet",
                        "-af", f"volume={self.gain_db}dB", handle.name,
                    ],
                    check=True,
                    timeout=30,
                )
            elif player == "mplayer":
                subprocess.run(
                    ["mplayer", "-really-quiet", "-af", f"volume={self.gain_db}", handle.name],
                    check=True,
                    timeout=30,
                )
            else:
                raise RuntimeError("edge-tts generated audio but no MP3 player is installed")
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

    def _speak(self, text: str) -> None:
        if self.backend == "edge-tts":
            self._speak_edge_tts(text)
        elif self.backend == "piper":
            self._speak_piper(text)
        elif self.backend in ("espeak-ng", "espeak"):
            amplitude = max(0, min(200, 100 + self.volume_percent))
            subprocess.run([self.backend, "-a", str(amplitude), text], check=True, timeout=30)

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
