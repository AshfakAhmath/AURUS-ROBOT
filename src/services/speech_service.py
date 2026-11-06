"""Single-owner, offline wake-word and speech-recognition pipeline.

The microphone is opened by one worker only. While idle it runs sherpa-onnx
keyword spotting; after a wake or push-to-talk request it uses Silero VAD to
collect one utterance and sends the result to Vosk. Speaker playback pauses and
resets all microphone-side inference so the robot cannot trigger on itself.
"""

from __future__ import annotations

from collections import deque
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time
from typing import Callable

import numpy as np

try:
    import pyaudio
except ImportError:  # pragma: no cover - dependency is hardware-specific
    pyaudio = None

try:
    import sherpa_onnx
except ImportError:  # pragma: no cover - graceful fallback is tested instead
    sherpa_onnx = None

try:
    from vosk import KaldiRecognizer, Model
except ImportError:  # pragma: no cover - dependency is optional
    KaldiRecognizer = None
    Model = None


ResultCallback = Callable[[str, str | None], None]
ARECORD_DEVICE = re.compile(
    r"card\s+(?P<card>\d+):.*?device\s+(?P<device>\d+):",
    re.IGNORECASE,
)


def _pcm_to_float(data: bytes) -> np.ndarray:
    return np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0


def _parse_arecord_devices(output: str) -> list[tuple[str, str]]:
    """Return ALSA plughw device names, preferring lines that identify USB."""
    devices: list[tuple[int, str, str]] = []
    for position, line in enumerate(output.splitlines()):
        match = ARECORD_DEVICE.search(line)
        if not match:
            continue
        device = f"plughw:{match.group('card')},{match.group('device')}"
        priority = 0 if "usb" in line.lower() else 1
        devices.append((priority, position, device))
    devices.sort()
    return [(device, "USB ALSA capture" if priority == 0 else "ALSA capture") for priority, _, device in devices]


def _discover_arecord_device() -> tuple[str, str] | None:
    configured = os.getenv("ALSA_CAPTURE_DEVICE", "").strip()
    if configured:
        return configured, "configured ALSA capture"
    command = shutil.which("arecord")
    if not command:
        return None
    try:
        result = subprocess.run(
            [command, "-l"], capture_output=True, text=True, timeout=5, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    devices = _parse_arecord_devices(result.stdout)
    return devices[0] if devices else None


class ArecordStream:
    """Minimal PyAudio-compatible stream backed by ALSA's arecord utility."""

    def __init__(self, device: str, sample_rate: int):
        command = shutil.which("arecord")
        if not command:
            raise RuntimeError("arecord is not installed")
        self.device = device
        self._process = subprocess.Popen(
            [
                command,
                "-q",
                "-D",
                device,
                "-t",
                "raw",
                "-f",
                "S16_LE",
                "-c",
                "1",
                "-r",
                str(sample_rate),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        time.sleep(0.15)
        if self._process.poll() is not None:
            detail = self._stderr()
            self.close()
            raise RuntimeError(f"arecord could not open {device}: {detail or 'capture failed'}")

    def _stderr(self) -> str:
        if self._process.stderr is None:
            return ""
        try:
            return self._process.stderr.read().decode(errors="replace").strip()[:300]
        except Exception:
            return ""

    def read(self, frame_count: int, exception_on_overflow: bool = False) -> bytes:
        del exception_on_overflow
        if self._process.stdout is None:
            raise RuntimeError("arecord output is unavailable")
        required = int(frame_count) * 2  # mono signed 16-bit PCM
        chunks = bytearray()
        while len(chunks) < required:
            chunk = self._process.stdout.read(required - len(chunks))
            if not chunk:
                detail = self._stderr()
                raise RuntimeError(f"arecord capture stopped: {detail or 'no audio data'}")
            chunks.extend(chunk)
        return bytes(chunks)

    def stop_stream(self) -> None:
        process = self._process
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1.0)

    def close(self) -> None:
        self.stop_stream()
        if self._process.stdout is not None:
            self._process.stdout.close()
        if self._process.stderr is not None:
            self._process.stderr.close()


class SherpaKeywordEngine:
    """Small open-vocabulary KWS model, configured for one keyword file."""

    ENCODER = "encoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx"
    DECODER = "decoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx"
    JOINER = "joiner-epoch-12-avg-2-chunk-16-left-64.int8.onnx"

    def __init__(self, model_dir: Path, keywords_file: Path):
        if sherpa_onnx is None:
            raise RuntimeError("sherpa-onnx is not installed")
        required = {
            "tokens": model_dir / "tokens.txt",
            "encoder": model_dir / self.ENCODER,
            "decoder": model_dir / self.DECODER,
            "joiner": model_dir / self.JOINER,
            "keywords": keywords_file,
        }
        missing = [str(path) for path in required.values() if not path.is_file()]
        if missing:
            raise RuntimeError("wake model files missing: " + ", ".join(missing))
        self._spotter = sherpa_onnx.KeywordSpotter(
            tokens=str(required["tokens"]),
            encoder=str(required["encoder"]),
            decoder=str(required["decoder"]),
            joiner=str(required["joiner"]),
            num_threads=1,
            keywords_file=str(required["keywords"]),
            provider="cpu",
        )
        self._stream = self._spotter.create_stream()

    def reset(self) -> None:
        self._stream = self._spotter.create_stream()

    def accept(self, data: bytes, sample_rate: int) -> str:
        self._stream.accept_waveform(sample_rate, _pcm_to_float(data))
        detected = ""
        while self._spotter.is_ready(self._stream):
            self._spotter.decode_stream(self._stream)
            result = self._spotter.get_result(self._stream)
            if result:
                detected = str(result)
                self._spotter.reset_stream(self._stream)
                break
        return detected


class SherpaVadEngine:
    """Silero VAD adapter that returns completed 16-bit PCM utterances."""

    def __init__(self, model_path: Path, sample_rate: int, max_seconds: float):
        if sherpa_onnx is None:
            raise RuntimeError("sherpa-onnx is not installed")
        if not model_path.is_file():
            raise RuntimeError(f"Silero VAD model missing: {model_path}")
        config = sherpa_onnx.VadModelConfig()
        config.silero_vad.model = str(model_path)
        config.silero_vad.threshold = 0.25
        config.silero_vad.min_silence_duration = 0.5
        config.silero_vad.min_speech_duration = 0.25
        config.silero_vad.max_speech_duration = max_seconds
        config.sample_rate = sample_rate
        self._vad = sherpa_onnx.VoiceActivityDetector(
            config, buffer_size_in_seconds=max(20.0, max_seconds + 2.0)
        )

    @property
    def speech_started(self) -> bool:
        return len(self._vad.current_segment.samples) > 0

    def reset(self) -> None:
        self._vad.reset()

    def _take_segment(self) -> bytes | None:
        if self._vad.empty():
            return None
        samples = np.asarray(self._vad.front.samples, dtype=np.float32)
        self._vad.pop()
        if samples.size == 0:
            return None
        return (np.clip(samples, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()

    def accept(self, data: bytes) -> bytes | None:
        self._vad.accept_waveform(_pcm_to_float(data))
        return self._take_segment()

    def flush(self) -> bytes | None:
        self._vad.flush()
        return self._take_segment()


class AdaptiveEnergyVad:
    """Bounded offline fallback used only when the Silero model is unavailable."""

    def __init__(self, sample_rate: int, frame_samples: int, max_seconds: float):
        self.sample_rate = sample_rate
        self.frame_samples = frame_samples
        self.max_frames = max(1, math.ceil(max_seconds * sample_rate / frame_samples))
        self.pre_roll_frames = max(1, math.ceil(0.25 * sample_rate / frame_samples))
        self.end_silence_frames = max(1, math.ceil(0.55 * sample_rate / frame_samples))
        self.min_speech_frames = max(1, math.ceil(0.2 * sample_rate / frame_samples))
        self.reset()

    @property
    def speech_started(self) -> bool:
        return self._started

    def reset(self) -> None:
        self._pre_roll: deque[bytes] = deque(maxlen=self.pre_roll_frames)
        self._frames: list[bytes] = []
        self._noise_rms = 120.0
        self._started = False
        self._speech_frames = 0
        self._silence_frames = 0

    def accept(self, data: bytes) -> bytes | None:
        samples = np.frombuffer(data, dtype=np.int16).astype(np.float32)
        rms = float(np.sqrt(np.mean(samples * samples))) if samples.size else 0.0
        threshold = max(350.0, self._noise_rms * 3.0)
        voiced = rms >= threshold

        if not self._started:
            self._pre_roll.append(data)
            if not voiced:
                self._noise_rms = (self._noise_rms * 0.96) + (rms * 0.04)
                return None
            self._started = True
            self._frames.extend(self._pre_roll)
        else:
            self._frames.append(data)
        if voiced:
            self._speech_frames += 1
            self._silence_frames = 0
        else:
            self._silence_frames += 1

        complete = len(self._frames) >= self.max_frames or (
            self._speech_frames >= self.min_speech_frames
            and self._silence_frames >= self.end_silence_frames
        )
        if complete:
            result = b"".join(self._frames)
            self.reset()
            return result
        return None

    def flush(self) -> bytes | None:
        result = b"".join(self._frames) if self._speech_frames >= self.min_speech_frames else None
        self.reset()
        return result


class SpeechService:
    RATE = 16000
    FRAME_SAMPLES = 512  # Silero's recommended 16 kHz window (32 ms).
    DEFAULT_MIC_GAIN_DB = 6.0

    def __init__(
        self,
        model_path: str | Path,
        keyword_model_path: str | Path | None = None,
        keywords_path: str | Path | None = None,
        vad_model_path: str | Path | None = None,
        microphone_index: int | None = None,
        record_seconds: float = 10.0,
        speech_start_timeout: float = 4.0,
        wake_enabled: bool = False,
        playback_active: threading.Event | None = None,
        mic_gain_db: float = DEFAULT_MIC_GAIN_DB,
        **_legacy_wake_gate,
    ):
        self.model_path = Path(model_path)
        self.keyword_model_path = Path(keyword_model_path) if keyword_model_path else None
        self.keywords_path = Path(keywords_path) if keywords_path else None
        self.vad_model_path = Path(vad_model_path) if vad_model_path else None
        self.microphone_index = microphone_index
        self.record_seconds = min(30.0, max(2.0, float(record_seconds)))
        self.speech_start_timeout = min(15.0, max(1.0, float(speech_start_timeout)))
        self.wake_requested = bool(wake_enabled)
        if not math.isfinite(mic_gain_db):
            mic_gain_db = self.DEFAULT_MIC_GAIN_DB
        self.mic_gain_db = min(18.0, max(0.0, mic_gain_db))
        self._mic_gain = 10 ** (self.mic_gain_db / 20.0)
        self.playback_active = playback_active or threading.Event()
        self.browser_playback_active = threading.Event()

        self._model = None
        self._groq_client = None
        self._groq_api_key = os.getenv("GROQ_API_KEY", "").strip()
        self._model_lock = threading.Lock()
        self._keyword_engine: SherpaKeywordEngine | None = None
        self._vad_engine: SherpaVadEngine | AdaptiveEnergyVad | None = None
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._wake_callback: Callable[[str], None] | None = None
        self._wake_pending = False
        self._pending_callback: ResultCallback | None = None
        self._capturing = False
        self._processing = False
        self._dispatching = False
        self._audio_ready = False
        self._playback_was_active = False
        self.selected_microphone_index: int | None = None
        self.selected_microphone_name = "unavailable"

        self.error = "not initialized"
        self.wake_error = "wake word not initialized"
        self.vad_error = "VAD not initialized"
        self.vad_backend = "unavailable"
        self.stt_backend = "unavailable"

    @property
    def healthy(self) -> bool:
        return bool(
            (self._groq_client is not None or self._model is not None)
            and (pyaudio is not None or shutil.which("arecord"))
            and self._audio_ready
            and self._thread
            and self._thread.is_alive()
        )

    @property
    def listening(self) -> bool:
        with self._lock:
            return (
                self._wake_pending
                or self._capturing
                or self._processing
                or self._pending_callback is not None
            )

    @property
    def processing(self) -> bool:
        with self._lock:
            return self._processing or self._dispatching

    @property
    def wake_gate_passed(self) -> bool:
        """Compatibility health flag: model readiness replaces the old trial counter."""
        return self._keyword_engine is not None

    @property
    def wake_active(self) -> bool:
        with self._lock:
            wake_pending = self._wake_pending
        return bool(self.wake_requested and self.wake_gate_passed and self.healthy and not wake_pending)

    @property
    def wake_backend(self) -> str:
        return "sherpa-onnx-kws" if self._keyword_engine is not None else "unavailable"

    def start(self, wake_callback: Callable[[str], None] | None = None) -> None:
        if self._thread and self._thread.is_alive():
            return
        if pyaudio is None and not shutil.which("arecord"):
            self.error = "PyAudio or the ALSA arecord utility is not installed"
            return
        if not self._groq_api_key and (
            Model is None or KaldiRecognizer is None or not self.model_path.is_dir()
        ):
            self.error = "No STT backend: configure GROQ_API_KEY or install Vosk and its model"
            return

        self._wake_callback = wake_callback
        self._stop.clear()
        self.error = "loading offline speech models"
        self._thread = threading.Thread(
            target=self._initialize_and_run, name="speech-pipeline", daemon=True
        )
        self._thread.start()

    def _initialize_and_run(self) -> None:
        try:
            if self._groq_api_key:
                try:
                    from groq import Groq

                    self._groq_client = Groq(
                        api_key=self._groq_api_key, timeout=20.0, max_retries=1
                    )
                    self.stt_backend = "groq-whisper"
                except Exception as exc:
                    self.error = f"Groq Whisper initialization failed: {str(exc)[:180]}"
            if self._groq_client is None:
                self._load_vosk_model()
            if self._stop.is_set():
                return
            self._initialize_vad()
            self._initialize_keyword_engine()
            if self._stop.is_set():
                return
            self.error = ""
            self._audio_loop()
        except Exception as exc:
            self.error = f"speech model initialization failed: {exc}"

    def _load_vosk_model(self) -> None:
        if self._model is not None:
            return
        with self._model_lock:
            if self._model is not None:
                return
            if Model is None or KaldiRecognizer is None:
                raise RuntimeError("Vosk is not installed")
            if not self.model_path.is_dir():
                raise RuntimeError(f"Vosk model missing: {self.model_path}")
            self._model = Model(str(self.model_path))
            self.stt_backend = (
                "groq-whisper+vosk-fallback" if self._groq_client is not None else "vosk"
            )

    def _initialize_vad(self) -> None:
        try:
            if self.vad_model_path is None:
                raise RuntimeError("Silero VAD path is not configured")
            self._vad_engine = SherpaVadEngine(self.vad_model_path, self.RATE, self.record_seconds)
            self.vad_backend = "silero-vad"
            self.vad_error = ""
        except Exception as exc:
            self._vad_engine = AdaptiveEnergyVad(self.RATE, self.FRAME_SAMPLES, self.record_seconds)
            self.vad_backend = "adaptive-energy-fallback"
            self.vad_error = str(exc)[:200]

    def _initialize_keyword_engine(self) -> None:
        self._keyword_engine = None
        if not self.wake_requested:
            self.wake_error = "wake word disabled"
            print("[SpeechService] Local wake word disabled by AURUS_WAKE_ENABLED.")
            return
        try:
            if self.keyword_model_path is None or self.keywords_path is None:
                raise RuntimeError("wake model or keyword file path is not configured")
            self._keyword_engine = SherpaKeywordEngine(self.keyword_model_path, self.keywords_path)
            self.wake_error = ""
            print("[SpeechService] Local AURUS wake-word detector is ready.")
        except Exception as exc:
            self.wake_error = str(exc)[:240]
            print(f"[SpeechService] Local wake word unavailable: {self.wake_error}")

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        with self._lock:
            callback = self._pending_callback
            self._pending_callback = None
            self._wake_pending = False
            self._capturing = False
            self._processing = False
        if callback:
            self._safe_result(callback, "", "speech service stopped")
        self._audio_ready = False

    def listen_once(self, callback: ResultCallback) -> bool:
        if not callable(callback) or not self.healthy:
            return False
        with self._lock:
            if self._capturing or self._processing or self._dispatching or self._pending_callback is not None:
                return False
            self._pending_callback = callback
            self._wake_pending = False
        return True

    def cancel_wake(self) -> None:
        """Release a detected wake phrase when acknowledgement/listening cannot start."""
        with self._lock:
            self._wake_pending = False
        if self._keyword_engine:
            self._keyword_engine.reset()

    def _microphone_candidates(self, audio) -> list[tuple[int, str]]:
        """Prefer a physical USB microphone over fragile default/Pulse devices."""
        if self.microphone_index is not None:
            try:
                info = audio.get_device_info_by_index(self.microphone_index)
                name = str(info.get("name", f"device {self.microphone_index}"))
            except Exception:
                name = f"device {self.microphone_index}"
            return [(self.microphone_index, name)]

        candidates: list[tuple[int, int, str]] = []
        try:
            count = int(audio.get_device_count())
        except Exception:
            count = 0
        for index in range(count):
            try:
                info = audio.get_device_info_by_index(index)
                if int(info.get("maxInputChannels", 0)) < 1:
                    continue
                name = str(info.get("name", f"device {index}"))
            except Exception:
                continue
            lower = name.lower()
            score = 0
            if "usb" in lower:
                score += 100
            if "pnp" in lower:
                score += 50
            if "microphone" in lower or " mic" in lower or "input" in lower:
                score += 25
            if "hw:" in lower or "plughw:" in lower:
                score += 10
            if "pulse" in lower or lower.startswith("default"):
                score -= 100
            candidates.append((score, index, name))
        candidates.sort(key=lambda item: (-item[0], item[1]))
        return [(index, name) for _, index, name in candidates]

    def _open_stream(self, audio):
        candidates = self._microphone_candidates(audio)
        alsa_device = _discover_arecord_device() if os.name != "nt" else None
        alsa_is_configured = bool(os.getenv("ALSA_CAPTURE_DEVICE", "").strip())
        alsa_is_preferred = bool(
            alsa_device
            and self.microphone_index is None
            and (alsa_is_configured or alsa_device[1].startswith("USB"))
        )
        physical_candidates = [
            candidate
            for candidate in candidates
            if not any(alias in candidate[1].lower() for alias in ("pulse", "default"))
        ]
        pseudo_candidates = [candidate for candidate in candidates if candidate not in physical_candidates]

        if not candidates and not alsa_device:
            raise RuntimeError("no microphone input devices were reported by PyAudio or ALSA")

        errors = []

        # A configured ALSA device is an explicit override, and an automatically
        # discovered USB ALSA device is preferred over onboard/default inputs.
        # Previously either could lose to the first PortAudio device that opened.
        if alsa_device and alsa_is_preferred:
            device, description = alsa_device
            try:
                stream = ArecordStream(device, self.RATE)
                self.selected_microphone_index = None
                self.selected_microphone_name = f"{description}: {device}"
                print(f"[SpeechService] Microphone connected via {self.selected_microphone_name}.")
                return stream
            except Exception as exc:
                errors.append(f"{device}: {str(exc)[:100]}")

        initial_candidates = candidates if self.microphone_index is not None else physical_candidates
        for index, name in initial_candidates:
            try:
                stream = audio.open(
                    format=pyaudio.paInt16,
                    channels=1,
                    rate=self.RATE,
                    input=True,
                    frames_per_buffer=self.FRAME_SAMPLES,
                    input_device_index=index,
                )
                self.selected_microphone_index = index
                self.selected_microphone_name = name
                print(f"[SpeechService] Microphone connected: index {index} ({name}).")
                return stream
            except Exception as exc:
                errors.append(f"{index} ({name}): {str(exc)[:100]}")
        # ALSA plughw can resample USB microphones that reject PortAudio's
        # requested 16 kHz hardware rate, while keeping capture fully local.
        if alsa_device and not alsa_is_preferred:
            device, description = alsa_device
            try:
                stream = ArecordStream(device, self.RATE)
                self.selected_microphone_index = None
                self.selected_microphone_name = f"{description}: {device}"
                print(f"[SpeechService] Microphone connected via {self.selected_microphone_name}.")
                return stream
            except Exception as exc:
                errors.append(f"{device}: {str(exc)[:100]}")
        if self.microphone_index is None:
            for index, name in pseudo_candidates:
                try:
                    stream = audio.open(
                        format=pyaudio.paInt16,
                        channels=1,
                        rate=self.RATE,
                        input=True,
                        frames_per_buffer=self.FRAME_SAMPLES,
                        input_device_index=index,
                    )
                    self.selected_microphone_index = index
                    self.selected_microphone_name = name
                    print(f"[SpeechService] Microphone connected: index {index} ({name}).")
                    return stream
                except Exception as exc:
                    errors.append(f"{index} ({name}): {str(exc)[:100]}")
        detail = " | ".join(errors[:4])
        raise RuntimeError(
            "no usable 16 kHz microphone input; run python scripts/test_mic.py"
            + (f"; attempted {detail}" if detail else "")
        )

    def _transcribe(self, pcm: bytes) -> str:
        if not pcm:
            return ""

        # Use Groq Whisper when configured. Vosk is loaded lazily only if the
        # cloud request fails, avoiding a large model load on normal startup.
        if self._groq_client is not None:
            temp_path: str | None = None
            try:
                import tempfile
                import wave

                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                    temp_path = f.name

                with wave.open(temp_path, "wb") as wav_file:
                    wav_file.setnchannels(1)
                    wav_file.setsampwidth(2)
                    wav_file.setframerate(self.RATE)
                    wav_file.writeframes(pcm)

                with open(temp_path, "rb") as file:
                    transcription = self._groq_client.audio.transcriptions.create(
                        file=file,
                        model=os.getenv("GROQ_STT_MODEL", "whisper-large-v3-turbo"),
                        response_format="json",
                        language="en",
                        temperature=0.0,
                    )
                text = transcription.text.strip()
                self.error = ""
                return text
            except Exception as exc:
                self.error = f"Groq Whisper failed; using Vosk fallback: {str(exc)[:160]}"
            finally:
                if temp_path:
                    try:
                        os.unlink(temp_path)
                    except OSError:
                        pass

        # Fallback to local Vosk
        self._load_vosk_model()
        recognizer = KaldiRecognizer(self._model, self.RATE)
        recognizer.AcceptWaveform(pcm)
        result = json.loads(recognizer.FinalResult())
        text = " ".join(str(result.get("text", "")).strip().split())
        self.error = ""
        return text

    @staticmethod
    def _safe_result(callback: ResultCallback, text: str, error: str | None) -> None:
        try:
            callback(text, error)
        except Exception:
            pass

    def _complete_capture(self, callback: ResultCallback, pcm: bytes | None, error: str | None = None) -> None:
        with self._lock:
            self._capturing = False
            self._processing = True
        if self._vad_engine:
            self._vad_engine.reset()
        if self._keyword_engine:
            self._keyword_engine.reset()

        def worker() -> None:
            result_error = error
            try:
                text = self._transcribe(pcm or b"") if not error else ""
            except Exception as exc:
                text, result_error = "", f"speech recognition failed: {exc}"
            finally:
                with self._lock:
                    self._processing = False
                    self._dispatching = True
            # Capture is complete, but don't accept another voice request during dispatch.
            try:
                if not self._stop.is_set():
                    self._safe_result(callback, text, result_error)
            finally:
                with self._lock:
                    self._dispatching = False

        threading.Thread(target=worker, name="speech-transcriber", daemon=True).start()

    def _begin_pending_capture(self) -> ResultCallback | None:
        with self._lock:
            callback = self._pending_callback
            if callback is not None:
                self._pending_callback = None
                self._capturing = True
        if callback and self._vad_engine:
            self._vad_engine.reset()
        return callback

    def _notify_wake_detected(self, detected: str) -> None:
        """Latch one wake event until the runtime finishes its acknowledgement."""
        with self._lock:
            if self._wake_pending or self._capturing or self._processing or self._dispatching or self._pending_callback:
                return
            self._wake_pending = True
            callback = self._wake_callback
        print(f"[SpeechService] Wake phrase detected: {detected}")
        if callback is None:
            self.cancel_wake()
            return
        try:
            callback(detected)
        except Exception:
            self.cancel_wake()

    def _amplify_mic(self, data: bytes) -> bytes:
        if self._mic_gain == 1.0:
            return data
        samples = np.frombuffer(data, dtype=np.int16).astype(np.float32)
        samples *= self._mic_gain
        np.clip(samples, -32768, 32767, out=samples)
        return np.rint(samples, out=samples).astype(np.int16).tobytes()

    def _audio_loop(self) -> None:
        audio = None
        stream = None
        callback: ResultCallback | None = None
        capture_started = 0.0
        playback_started = 0.0
        resume_after = 0.0
        try:
            if pyaudio is not None:
                audio = pyaudio.PyAudio()
                stream = self._open_stream(audio)
            else:
                alsa_device = _discover_arecord_device()
                if not alsa_device:
                    raise RuntimeError("no ALSA capture device found")
                device, description = alsa_device
                stream = ArecordStream(device, self.RATE)
                self.selected_microphone_name = f"{description}: {device}"
                print(f"[SpeechService] Microphone connected via {self.selected_microphone_name}.")
            self._audio_ready = True
            while not self._stop.is_set():
                data = stream.read(self.FRAME_SAMPLES, exception_on_overflow=False)

                if self.playback_active.is_set() or self.browser_playback_active.is_set():
                    if not self._playback_was_active:
                        self._playback_was_active = True
                        playback_started = time.monotonic()
                        if self._keyword_engine:
                            self._keyword_engine.reset()
                        if self._vad_engine:
                            self._vad_engine.reset()
                    continue
                if self._playback_was_active:
                    self._playback_was_active = False
                    now = time.monotonic()
                    if callback is not None:
                        capture_started += now - playback_started
                    resume_after = now + 0.35
                    if self._keyword_engine:
                        self._keyword_engine.reset()
                    if self._vad_engine:
                        self._vad_engine.reset()
                if time.monotonic() < resume_after:
                    continue

                if self.processing:
                    continue

                if callback is None:
                    callback = self._begin_pending_capture()
                    if callback is not None:
                        capture_started = time.monotonic()

                if callback is not None:
                    utterance = self._vad_engine.accept(self._amplify_mic(data)) if self._vad_engine else None
                    elapsed = time.monotonic() - capture_started
                    if utterance is not None:
                        finished = callback
                        callback = None
                        self._complete_capture(finished, utterance)
                    elif elapsed >= self.speech_start_timeout and not self._vad_engine.speech_started:
                        finished = callback
                        callback = None
                        self._complete_capture(finished, None, "no speech detected")
                    elif elapsed >= self.record_seconds + self.speech_start_timeout:
                        finished = callback
                        callback = None
                        utterance = self._vad_engine.flush() if self._vad_engine else None
                        self._complete_capture(
                            finished, utterance, None if utterance else "speech capture timed out"
                        )
                    continue

                if self.wake_active and self._keyword_engine:
                    detected = self._keyword_engine.accept(self._amplify_mic(data), self.RATE)
                    if detected:
                        self._notify_wake_detected(detected)
        except Exception as exc:
            self.error = f"microphone pipeline failed: {exc}"
            if callback:
                self._safe_result(callback, "", self.error)
                callback = None
            with self._lock:
                pending = self._pending_callback
                self._pending_callback = None
                self._wake_pending = False
                self._capturing = False
                self._processing = False
            if pending:
                self._safe_result(pending, "", self.error)
        finally:
            self._audio_ready = False
            if callback:
                self._safe_result(callback, "", "speech service stopped")
            if stream is not None:
                try:
                    stream.stop_stream()
                    stream.close()
                except Exception:
                    pass
            if audio is not None:
                audio.terminate()
