"""Explicit composition root and application service for AURUS."""

from __future__ import annotations

import os
from pathlib import Path
import re
import threading
import time
from typing import Callable

from src.core.models import RobotMode, camera_session_current
from src.hardware.motors import MecanumDriver
from src.hardware.sensors import ProximitySensor
from src.memory.repository import MemoryRepository
from src.services.behavior_controller import BehaviorController
from src.services.identity_service import IdentityService
from src.services.gesture_service import GestureService
from src.services.local_information import answer_local_question
from src.services.motion_arbiter import MotionArbiter
from src.services.sensor_sampler import SensorSampler
from src.services.speech_service import SpeechService
from src.services.tts_service import TTSService
from src import config
from src.services.vision_service import VisionService
from src.services.mcp_agent_service import MCPAgentService, matches_object_scan_command
from src.services.groq_vision_service import GroqVisionService
from src.services.exhibition_demo import ExhibitionDemo


INTRODUCTION_REQUEST = re.compile(
    r"(?:(?:hey\s+)?aurus[\s,]+)?(?:(?:can|could|would|will) you\s+)?(?:please\s+)?"
    r"introduce yourself(?:\s+to\s+[^.!?]+?)?(?:[\s,]+please)?[.!?]*",
    re.IGNORECASE,
)


class RobotRuntime:
    def __init__(self, project_root: str | Path | None = None):
        self.project_root = Path(project_root or Path(__file__).resolve().parents[1])
        self.driver = MecanumDriver()
        self.sensor = ProximitySensor(self.driver)
        self.repository = MemoryRepository(self.project_root / "aurus_memory.db")
        self.identity = IdentityService(self.repository)
        front_sensor_enabled = os.getenv("AURUS_FRONT_SENSOR_ENABLED", "true").lower() == "true"
        self._default_front_sensor_enabled = front_sensor_enabled
        self.sensor_sampler = SensorSampler(self.sensor, front_sensor_enabled=front_sensor_enabled)
        self.arbiter = MotionArbiter(
            self.driver,
            self.sensor_sampler,
            front_sensor_enabled=front_sensor_enabled,
            require_full_coverage=config.REQUIRE_FULL_SENSOR_COVERAGE,
        )
        self.vision = VisionService(
            None, self.project_root / "models",
            enabled=os.getenv("AURUS_CAMERA_ENABLED", "true").strip().lower() == "true",
        )
        self.gestures = GestureService(self.vision, self.project_root / "models" / "gesture_recognizer.task")

        microphone_index = os.getenv("MIC_INDEX")
        try:
            microphone_index_value = (
                int(microphone_index)
                if microphone_index and microphone_index.strip().lower() not in ("none", "null")
                else None
            )
        except ValueError:
            microphone_index_value = None

        def model_path(variable: str, default: Path) -> Path:
            raw = os.getenv(variable, "").strip()
            configured = Path(raw) if raw else default
            return configured if configured.is_absolute() else self.project_root / configured

        def float_setting(variable: str, default: float) -> float:
            try:
                return float(os.getenv(variable, str(default)))
            except ValueError:
                return default

        audio_playback = threading.Event()
        keyword_model = model_path(
            "KWS_MODEL_PATH",
            Path("models") / "sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01",
        )
        self.speech = SpeechService(
            model_path=model_path(
                "VOSK_MODEL_PATH", Path("models") / "vosk-model-small-en-us-0.15"
            ),
            keyword_model_path=keyword_model,
            keywords_path=model_path("KWS_KEYWORDS_PATH", keyword_model / "aurus_keywords.txt"),
            vad_model_path=model_path("VAD_MODEL_PATH", Path("models") / "silero_vad.onnx"),
            microphone_index=microphone_index_value,
            record_seconds=config.AURUS_RECORD_SECONDS,
            speech_start_timeout=float_setting("AURUS_SPEECH_START_TIMEOUT", 10.0),
            wake_enabled=os.getenv("AURUS_WAKE_ENABLED", "true").strip().lower() == "true",
            mic_gain_db=float_setting("AURUS_MIC_GAIN_DB", SpeechService.DEFAULT_MIC_GAIN_DB),
            playback_active=audio_playback,
        )
        self.tts = TTSService(
            model_path("PIPER_MODEL_PATH", Path("models") / "en_US-lessac-medium.onnx"),
            playback_active=audio_playback,
            voice=os.getenv("AURUS_TTS_VOICE", "en-US-GuyNeural"),
            playback_device=os.getenv("ALSA_PLAYBACK_DEVICE", "").strip(),
            audio_output=os.getenv("AURUS_AUDIO_OUTPUT", "auto"),
            audio_emit=self.emit,
        )
        self.mcp_agent = MCPAgentService(self)
        self.groq_vision = GroqVisionService()
        self.behavior = BehaviorController(
            self.arbiter, self.sensor_sampler, self.vision,
            self._handle_behavior_event, self.groq_vision,
            playback_active=audio_playback,
        )
        self._event_sink: Callable[[str, dict], None] = lambda event, payload: None
        self._started = False
        self._lock = threading.RLock()
        self._object_scan = threading.Lock()
        self._last_manual_sequence = -1
        self._demo_dashboard_sid = None
        self._demo_dashboard_clients: set[str] = set()
        self._browser_listening_sid: str | None = None
        self.demo = ExhibitionDemo(self)
        self.behavior.showcase_start = self.start_exhibition_demo
        self.behavior.demo_active = lambda: self.demo_active

    @property
    def demo_active(self):
        demo = getattr(self, "demo", None)
        return bool(demo and demo.active)

    def face_expression(self, operator_busy: bool = False) -> str:
        if self.arbiter.estopped:
            return "estop"
        if self.tts.local_playback_active.is_set() or self.speech.browser_playback_active.is_set():
            return "speaking"
        if (operator_busy or self.speech.processing or self.mcp_agent.busy
                or self._object_scan.locked() or self.tts.playback_active.is_set()):
            return "thinking"
        if self.arbiter.mode == RobotMode.LISTENING:
            return "listening"
        if self.demo_active:
            return "performing"
        if self.arbiter.mode == RobotMode.EXPLORING:
            return "exploring"
        return "idle"

    def detect_objects(self, speak_result: bool = False) -> dict:
        result = {"status": "error", "objects": []}
        with self._lock:
            if self.demo_active:
                return {**result, "message": "Stop the FIT Expo demo before scanning for objects."}
            if not self._object_scan.acquire(blocking=False):
                return {**result, "message": "An object scan is already running. Try again in a moment."}
        try:
            snapshot = self.vision.get_snapshot()
            if not snapshot.enabled:
                return {**result, "message": "Camera is switched off. Turn it on in the dashboard to scan objects."}
            frame = self.vision.get_fresh_jpeg()
            if not frame:
                return {**result, "message": "There is no fresh camera frame to scan. Check the live feed and try again."}
            if not self.groq_vision.ready:
                return {**result, "message": "Groq vision is unavailable. Check GROQ_API_KEY and the Groq SDK."}
            objects = self.groq_vision.detect_objects(frame)
            if not camera_session_current(snapshot, self.vision.get_snapshot()):
                return {**result, "message": "The camera changed during the scan. Please enable it and scan again."}
            if objects is None:
                return {**result, "message": "Object detection failed. Check the connection and Groq vision model access, then retry."}
            if objects:
                message = f"I see {', '.join(objects)}."
            else:
                message = "I couldn't identify any clear objects. Hold a prop near the center of the camera and try again."
            response = {"status": "ok", "objects": objects, "message": message}
            if speak_result:
                names = objects[:3]
                if names:
                    spoken_names = names[0] if len(names) == 1 else ', '.join(names[:-1]) + ' and ' + names[-1]
                    spoken = f"I see {', '.join(names)}, and more." if len(objects) > 3 else f"I see {spoken_names}."
                else:
                    spoken = "I couldn't identify a clear object. Please try again."
                with self._lock:
                    if not camera_session_current(snapshot, self.vision.get_snapshot()):
                        return {**result, "message": "The camera changed during the scan. Please scan again."}
                    response["speech_queued"] = bool(self.tts.healthy and self.tts.speak(
                        spoken, valid_when=lambda: camera_session_current(snapshot, self.vision.get_snapshot()),
                    ))
            return response
        finally:
            self._object_scan.release()

    def start_exhibition_demo(self, owner_sid=None):
        with self._lock:
            if self._object_scan.locked() or (owner_sid and owner_sid not in self._demo_dashboard_clients):
                return False
            return self.demo.start(owner_sid or self._demo_dashboard_sid)

    def set_demo_dashboard_client(self, sid: str, connected: bool) -> None:
        with self._lock:
            self.mcp_agent.set_chat_connected(sid, connected)
            if connected:
                self._demo_dashboard_clients.add(sid)
                self._demo_dashboard_sid = sid
                return
            self._demo_dashboard_clients.discard(sid)
            if self._demo_dashboard_sid == sid:
                self._demo_dashboard_sid = next(iter(self._demo_dashboard_clients), None)
            self.browser_listening_finished(sid)
            self.demo.owner_disconnected(sid)

    def stop_exhibition_demo(self, reason="Stopped by operator"):
        demo = getattr(self, "demo", None)
        if demo is not None:
            demo.cancel(reason)

    def set_event_sink(self, callback: Callable[[str, dict], None]) -> None:
        self._event_sink = callback
        self.tts.set_audio_emit(self.emit)

    def emit(self, event: str, payload: dict) -> None:
        try:
            self._event_sink(event, payload)
        except Exception:
            pass

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            self._started = True
        try:
            self.sensor_sampler.sample_once()
            self.sensor_sampler.start()
            self.arbiter.start()
            self.vision.start()
            self.tts.start()
            self.speech.start(self._wake_detected)
            self.behavior.start()
            self.repository.log_event("runtime", "AURUS evaluation runtime started")
        except Exception:
            self.stop()
            raise

    def stop(self) -> None:
        with self._lock:
            if not self._started:
                return
            self._started = False
        errors = []
        shutdown_steps = (
            ("emergency stop", lambda: self.arbiter.emergency_stop("runtime shutdown")),
            ("exhibition demo", lambda: self.stop_exhibition_demo("Runtime shutdown")),
            ("behavior", self.behavior.stop),
            ("speech", self.speech.stop),
            ("vision", self.vision.stop),
            ("arbiter", self.arbiter.stop),
            ("sensor sampler", self.sensor_sampler.stop),
            ("tts", self.tts.stop),
            ("motor driver", self.driver.cleanup),
        )
        for component, action in shutdown_steps:
            try:
                action()
            except Exception as exc:
                errors.append(f"{component}: {str(exc)[:120]}")
        description = "AURUS evaluation runtime stopped"
        if errors:
            description += "; shutdown warnings: " + " | ".join(errors)
        try:
            self.repository.log_event("runtime", description)
        except Exception:
            pass

    def dashboard_connected(self, connected: bool) -> None:
        if connected:
            with self._lock:
                self._last_manual_sequence = -1
        self.arbiter.set_dashboard_connected(connected)
        if not connected:
            self.stop_exhibition_demo("Dashboard disconnected")

    def set_mode(self, value: str) -> bool:
        if self.demo_active:
            if value.lower() == "idle":
                self.stop_exhibition_demo()
            else:
                return False
        aliases = {
            "explore": RobotMode.EXPLORING,
            "exploring": RobotMode.EXPLORING,
            "manual": RobotMode.MANUAL,
            "idle": RobotMode.IDLE,
        }
        mode = aliases.get(value.lower())
        if mode is None:
            return False
        if mode == RobotMode.EXPLORING and not self.camera_control()["enabled"]:
            return False
        previous = self.arbiter.mode
        accepted = self.arbiter.set_mode(mode)
        behavior = getattr(self, "behavior", None)
        if accepted and behavior is not None:
            if mode == RobotMode.EXPLORING:
                behavior.begin_exploration()
            elif previous == RobotMode.EXPLORING:
                behavior.finish_exploration()
        return accepted

    def camera_control(self) -> dict:
        vision = getattr(self, "vision", None)
        if vision is None:
            return {"enabled": True, "state": "starting", "generation": 0, "error": ""}
        if hasattr(vision, "control_state"):
            return vision.control_state()
        snapshot = vision.get_snapshot()
        return {"enabled": snapshot.enabled, "state": "on" if snapshot.healthy else "starting",
                "generation": snapshot.generation, "error": snapshot.error}

    def set_camera_enabled(self, enabled: bool) -> dict:
        if type(enabled) is not bool:
            raise ValueError("Camera enabled must be true or false")
        with self._lock:
            if not enabled and self.arbiter.mode == RobotMode.EXPLORING:
                # Halt before waiting on the camera/identity lock. Resuming
                # capture never resumes movement without a new Explore command.
                self.arbiter.halt("camera-disabled")
                self.set_mode("idle")
            state = self.vision.set_enabled(enabled)
        self.emit("enrollment_progress", self.identity.get_enrollment().as_dict())
        return state

    def set_audio_output(self, mode: str) -> bool:
        if self.demo_active:
            self.emit("command_error", {"message": "Stop the FIT Expo demo before changing the speaker output."})
            return False
        accepted = self.tts.set_audio_output(mode)
        if accepted:
            self.emit("system_health", self.health())
        return accepted

    def manual_drive(self, vx: float, vy: float, omega: float, sequence: int) -> bool:
        if self.demo_active:
            return False
        with self._lock:
            if sequence <= self._last_manual_sequence:
                return False
            self._last_manual_sequence = sequence
        if self.arbiter.mode != RobotMode.MANUAL:
            return False
        return self.arbiter.command("dashboard-manual", vx, vy, omega, ttl=0.25, priority=50)

    def configure_sensor(self, key=None, enabled=None, restore=False) -> dict:
        # Called only by the authenticated adapter; no GPIO numbers accepted.
        if restore:
            self.sensor_sampler.restore_defaults(self._default_front_sensor_enabled)
        else:
            self.sensor_sampler.set_enabled(key, enabled)
        # Configuration invalidation is synchronous. No AI/audio on this path.
        self.arbiter.tick()
        data = self.sensor_telemetry()
        self.emit("sensor_configuration", data)
        return data

    def sensor_telemetry(self):
        data = self.sensor_sampler.get_snapshot().as_dict()
        data["coverage_required"] = self.arbiter.require_full_coverage
        return data

    def emergency_stop(self) -> None:
        self.arbiter.emergency_stop()
        self.stop_exhibition_demo("Emergency stop")
        behavior = getattr(self, "behavior", None)
        summary = behavior.finish_exploration(emit=False) if behavior is not None else None
        self.repository.log_event("estop", "Emergency stop activated")
        if summary:
            self.emit("mission_summary", summary)
        self.emit("system_health", self.health())

    def clear_estop(self) -> None:
        self.arbiter.clear_estop()
        self.repository.log_event("estop", "Emergency stop cleared")

    def start_enrollment(self, name: str):
        with self._lock:
            return self._start_enrollment(name)

    def _start_enrollment(self, name: str):
        if self.demo_active:
            raise ValueError("Stop the FIT Expo demo before enrollment.")
        if getattr(self.vision, "identity_service", self.identity) is None:
            status = self.identity.begin_session_identity(name, camera_identity=False)
            self.emit("enrollment_progress", status.as_dict())
            return status
        snapshot = self.vision.get_snapshot()
        if not snapshot.enabled:
            raise ValueError("Camera is switched off. Turn it on before enrolling a face.")
        if not snapshot.healthy or snapshot.age > 1.0:
            raise ValueError("Wait for a fresh camera image before enrolling a face.")
        if "sface" in snapshot.backend:
            status = self.identity.begin_enrollment(name)
        elif snapshot.backend in ("haar-session-fallback", "video-only-fallback"):
            status = self.identity.begin_session_identity(name)
        else:
            raise ValueError("Vision is not ready for enrollment yet.")
        self.emit("enrollment_progress", status.as_dict())
        return status

    def remember_fact(self, text: str) -> tuple[bool, str]:
        if self.demo_active:
            return False, "Stop the FIT Expo demo before storing a fact."
        identity = self._current_person()
        if identity.status != "known" or identity.user_id is None:
            return False, "Tell me your name before storing a memory."
        self.repository.remember(identity.user_id, text)
        return True, f"I will remember that {text}."

    def start_listening(self) -> bool:
        if self.demo_active:
            return False
        if not self.speech.healthy:
            self.emit("conversation", {"source": "voice", "transcript": "", "response": "Microphone recognition is unavailable. Please use text input.", "provider": "local", "fallback_reason": self.speech.error})
            return False
        with self._lock:
            if self.demo_active or self.arbiter.mode == RobotMode.LISTENING:
                return False
            listening = self.arbiter.set_mode(RobotMode.LISTENING)
            if listening:
                self._browser_listening_sid = None
        if not listening:
            self.emit("conversation", {
                "source": "voice",
                "transcript": "",
                "response": "Listening is unavailable while the emergency stop is latched.",
                "provider": "local",
                "fallback_reason": "emergency stop latched",
            })
            return False
        started = self.speech.listen_once(self._speech_result)
        if not started:
            self.arbiter.set_mode(RobotMode.IDLE)
        return started

    def _wake_detected(self, _keyword: str = "AURUS") -> None:
        """Acknowledge a local wake phrase, then open the command capture window."""
        with self._lock:
            listening = (not self.demo_active and self.arbiter.mode != RobotMode.LISTENING
                         and self.arbiter.set_mode(RobotMode.LISTENING))
            if listening:
                self._browser_listening_sid = None
        if not listening:
            self.speech.cancel_wake()
            return
        response = "I'm listening."
        self.emit("conversation", {
            "source": "wake",
            "transcript": "Hey AURUS",
            "response": response,
            "provider": "local",
            "fallback_reason": "",
        })

        def begin_capture() -> None:
            if not self.speech.listen_once(self._speech_result):
                self.speech.cancel_wake()
                if self.arbiter.mode == RobotMode.LISTENING:
                    self.arbiter.set_mode(RobotMode.IDLE)
                self.emit("conversation", {
                    "source": "wake",
                    "transcript": "",
                    "response": "I could not open the microphone command window.",
                    "provider": "local",
                    "fallback_reason": self.speech.error or "speech pipeline busy",
                })

        if not self.tts.speak(response, on_complete=begin_capture):
            begin_capture()

    def browser_wake_detected(self, owner_sid: str | None = None) -> bool:
        """Acknowledge the browser wake phrase before arming its command window."""
        if not owner_sid:
            return False
        with self._lock:
            if (owner_sid not in self._demo_dashboard_clients or self.demo_active
                    or self.arbiter.mode == RobotMode.LISTENING or self.speech.processing
                    or self.speech.listening
                    or not self.arbiter.set_mode(RobotMode.LISTENING)):
                return False
            self._browser_listening_sid = owner_sid
        response = "I'm listening."
        self.emit("conversation", {
            "source": "browser-mic",
            "transcript": "Hey AURUS",
            "response": response,
            "provider": "local",
            "fallback_reason": "",
        })

        def ready() -> None:
            with self._lock:
                if self._browser_listening_sid == owner_sid and self.arbiter.mode == RobotMode.LISTENING:
                    self.emit("browser_listen_ready", {"seconds": 10, "_recipient": owner_sid})

        if not self.tts.speak(response, on_complete=ready):
            ready()
        return True

    def browser_listening_finished(self, owner_sid: str | None) -> None:
        with self._lock:
            if owner_sid is not None and self._browser_listening_sid == owner_sid:
                self._browser_listening_sid = None
                if self.arbiter.mode == RobotMode.LISTENING:
                    self.arbiter.set_mode(RobotMode.IDLE)

    def handle_browser_speech(self, text: str, owner_sid: str | None = None) -> dict:
        try:
            return self.handle_text(text, source="browser-mic", owner_sid=owner_sid)
        finally:
            self.browser_listening_finished(owner_sid)

    def _speech_result(self, text: str, error: str | None) -> None:
        if error or not text:
            response = f"I could not understand that. {error or 'Please try again.'}"
            self._announce(response, "voice", text, "local", error or "empty transcript")
            self.arbiter.set_mode(RobotMode.IDLE)
            return
        self.handle_text(text, source="voice")
        if self.arbiter.mode == RobotMode.LISTENING:
            self.arbiter.set_mode(RobotMode.IDLE)

    def _current_person(self):
        identity = self.vision.get_snapshot().identity
        if identity.status == "known" and identity.user_id is not None:
            return identity
        current = self.identity.current()
        return current if current.status == "known" else identity

    def _announce(
        self,
        response: str,
        source: str,
        transcript: str,
        provider: str,
        fallback_reason: str = "",
    ) -> dict:
        if self.demo_active:
            # A cloud response/greeting already in flight must not enter the
            # speech queue between the demo's live narration segments.
            return {"status": "error", "response": "FIT Expo demo is running.", "source": source}
        self.tts.speak(response)
        payload = {
            "source": source,
            "transcript": transcript,
            "response": response,
            "provider": provider,
            "fallback_reason": fallback_reason,
        }
        self.emit("conversation", payload)
        person = self._current_person()
        try:
            self.repository.log_interaction(person.user_id, source, transcript, response, provider)
        except Exception:
            pass
        return payload

    def _start_requested_demo(self, source: str, owner_sid: str | None) -> dict:
        with self._lock:
            if source == "browser-mic":
                self.browser_listening_finished(owner_sid)
            elif source == "voice" and self.arbiter.mode == RobotMode.LISTENING:
                self.arbiter.set_mode(RobotMode.IDLE)
            started = self.start_exhibition_demo(owner_sid)
        if started:
            return {"status": "ok", "message": "FIT Expo demo started"}
        message = "FIT Expo demo unavailable. Stop other activity, check E-STOP, and check that Piper or eSpeak is ready."
        self.emit("command_error", {"message": message})
        return {"status": "error", "message": message}

    def handle_text(self, text: str, source: str = "text", *, owner_sid: str | None = None) -> dict:
        clean = " ".join((text or "").strip().split())[:1000]
        if not clean:
            return self._announce("I did not receive a command.", source, clean, "local")
        lower = clean.lower()

        if re.search(r"\bclear(?:\s+the)?\s+(?:emergency\s+stop|e[\s-]?stop)\b", lower):
            self.clear_estop()
            return self._announce("Emergency stop cleared. I am idle.", source, clean, "local")
        if "emergency stop" in lower or re.search(r"\be[\s-]?stop\b", lower):
            self.emergency_stop()
            return self._announce("Emergency stop activated and latched.", source, clean, "local")
        if (
            lower.rstrip(".!?,;: ") in ("stop", "halt", "freeze", "stop demo", "stop showcase")
            or "stop exploring" in lower
            or "stop and summarize" in lower
            or "stop and summarise" in lower
        ):
            self.stop_exhibition_demo()
            was_exploring = self.arbiter.mode == RobotMode.EXPLORING
            self.arbiter.halt("voice-stop")
            if not self.arbiter.estopped:
                self.arbiter.set_mode(RobotMode.IDLE)
            behavior = getattr(self, "behavior", None)
            summary = (
                behavior.finish_exploration(emit=False)
                if was_exploring and behavior is not None else None
            )
            if summary:
                self.emit("mission_summary", summary)
                return self._announce(summary["text"], source, clean, "local")
            return self._announce("Stopped safely.", source, clean, "local")

        if self.demo_active:
            message = "FIT Expo demo is running. Use Stop Demo or E-STOP to interrupt."
            self.emit("command_error", {"message": message})
            return {"status": "error", "message": message}

        if INTRODUCTION_REQUEST.fullmatch(clean):
            return self._start_requested_demo(source, owner_sid)

        if answer := answer_local_question(clean):
            message, provider = answer
            return self._announce(message, source, clean, provider)

        if any(phrase in lower for phrase in (
            "what have you found", "mission status", "exploration status",
            "summarize the mission", "summarise the mission",
        )):
            behavior = getattr(self, "behavior", None)
            mission = behavior.mission_snapshot() if behavior is not None else {}
            if not mission.get("mission_id"):
                return self._announce("I have not started an exploration mission yet.", source, clean, "local")
            state = "So far" if mission.get("active") else "In my latest mission"
            observations = mission.get("observations", 0)
            people = mission.get("people", 0)
            routes = mission.get("route_changes", 0)
            response = (
                f"{state}, I recorded {observations} "
                f"{'discovery' if observations == 1 else 'discoveries'}, encountered "
                f"{people} {'person' if people == 1 else 'people'}, and changed route "
                f"{routes} {'time' if routes == 1 else 'times'}."
            )
            return self._announce(response, source, clean, "local")

        name_match = re.search(r"(?:my name is|call me)\s+([a-z][a-z .'-]{0,60})", clean, re.IGNORECASE)
        if name_match:
            name = name_match.group(1).strip(" .")
            try:
                status = self.start_enrollment(name)
            except ValueError as exc:
                return self._announce(str(exc), source, clean, "local")
            response = (
                f"Hello {name}. Hold still while I learn your face."
                if status.active
                else f"Hello {name}. I have linked your name to this session."
            )
            return self._announce(response, source, clean, "local")

        if any(phrase in lower for phrase in ("what do you remember", "what do you know about me", "remember about me", "do you remember me")):
            person = self._current_person()
            if person.user_id is None:
                return self._announce("I do not know who you are yet. Please tell me your name.", source, clean, "local")
            facts = self.repository.memories_for(person.user_id)
            if facts:
                response = f"{person.name}, I remember: " + "; ".join(facts[:4])
            else:
                response = f"You introduced yourself as {person.name}. You have not taught me a fact yet."
            return self._announce(response, source, clean, "local")

        if matches_object_scan_command(clean):
            return self.mcp_agent.execute_command(clean, source=source)

        visual_questions = (
            "what do you see",
            "what can you see",
            "describe the environment",
            "describe my surroundings",
            "describe your surroundings",
            "what is in front of you",
            "look at the environment",
            "look around and describe",
        )
        if any(phrase in lower for phrase in visual_questions):
            snapshot = self.vision.get_snapshot()
            if not snapshot.enabled:
                return self._announce("My camera is switched off. Turn it on in the dashboard to describe the scene.", source, clean, "local")
            if not snapshot.healthy or snapshot.age > 1.0:
                return self._announce(
                    "I do not have a fresh camera image to describe.",
                    source, clean, "local", snapshot.error or "camera frame unavailable",
                )
            vision_provider = getattr(self, "groq_vision", None)
            if vision_provider is None or not vision_provider.ready:
                return self._announce(
                    "Groq vision is currently unavailable.",
                    source, clean, "local", getattr(vision_provider, "error", "Groq unavailable"),
                )
            description = vision_provider.describe(self.vision.get_jpeg())
            if not camera_session_current(snapshot, self.vision.get_snapshot()):
                return self._announce("The camera changed while I was looking. Please turn it on and ask again.", source, clean, "local")
            if not description:
                return self._announce(
                    "I could not analyze the current camera image.",
                    source, clean, "groq-vision", vision_provider.error or "vision request failed",
                )
            return self._announce(description, source, clean, "groq-vision")

        remember_match = re.search(r"(?:remember that|remember)\s+(.+)", clean, re.IGNORECASE)
        if remember_match:
            ok, response = self.remember_fact(remember_match.group(1).strip())
            return self._announce(response, source, clean, "local", "" if ok else "identity required")

        if "explore" in lower:
            if not self.camera_control()["enabled"]:
                return self._announce("Turn my camera on in the dashboard before starting Explore.", source, clean, "local")
            if self.set_mode("explore"):
                return self._announce(
                    "Curiosity mission started. I will explore safely, explain route changes, and report meaningful discoveries.",
                    source, clean, "local",
                )
        if "manual mode" in lower or "take control" in lower:
            self.set_mode("manual")
            return self._announce("Manual mode ready.", source, clean, "local")
        if any(phrase in lower for phrase in ("show me what you can do", "showcase", "dance")):
            return self._start_requested_demo(source, owner_sid)
        if "status" in lower or "how are your systems" in lower:
            sensors = self.sensor_sampler.get_snapshot()
            clearance = sensors.available_clearance("front", center_default=self.arbiter.front_sensor_enabled)
            distance = f"{clearance:.0f} centimetres" if clearance is not None else "unavailable"
            response = f"Systems checked. Available front-sensor clearance is {distance}. Current mode is {self.arbiter.mode.value}."
            return self._announce(response, source, clean, "local")
        if lower.rstrip(".!?,;: ") in ("hello", "hello aurus", "hi aurus", "hey aurus"):
            person = self._current_person()
            name = f", {person.name}" if person.name else ""
            return self._announce(f"Hello{name}! AURUS is ready.", source, clean, "local")

        # Agentic Router: Decide whether this requires the MCP Agent or just conversational chat
        person = self._current_person()
        memories = self.repository.memories_for(person.user_id) if person.user_id else []

        if getattr(self, "mcp_agent", None) and getattr(self.mcp_agent, "ready", False):
            intent = self.mcp_agent.route_intent(clean)
            if intent == "ACTION":
                # Full tool-calling loop (moves robot, controls sensors, etc.)
                return self.mcp_agent.execute_command(clean)
            else:
                # Fast Groq-powered chat without tool latency
                response_text = self.mcp_agent.chat(clean, person.name, memories, owner_sid=owner_sid)
                return self._announce(response_text, source, clean, "groq-chat")


        return self._announce("My cloud intelligence is currently offline.", source, clean, "local")

    def _handle_behavior_event(self, event: str, payload: dict) -> None:
        if self.demo_active and event in ("greeting", "greeting_unknown", "behavior_notice", "mission_summary"):
            return
        if event in ("greeting", "greeting_unknown"):
            with self._lock:
                camera = self.camera_control()
                if not camera["enabled"] or payload.get("camera_generation", camera["generation"]) != camera["generation"]:
                    return
                message = (f"Welcome back, {payload['name']}! I recognize you." if event == "greeting" else
                           "Hello there! I see you, but I don't know your name yet. You can say 'my name is' to introduce yourself.")
                self._announce(message, "vision", "", "local")
            return
        if event == "behavior_notice":
            self._announce(
                payload.get("text", ""), "behavior", "",
                payload.get("provider", "local"),
            )
        elif event == "mission_summary":
            self.emit(event, payload)
            if not self.arbiter.estopped:
                self._announce(payload.get("text", "Exploration complete."), "behavior", "", "local")
        elif event in ("discovery", "mission_update"):
            self.emit(event, payload)
        else:
            self.emit(event, payload)

    def health(self) -> dict:
        sensors = self.sensor_sampler.get_snapshot()
        vision = self.vision.get_snapshot()
        camera = self.camera_control()
        return {
            "runtime": self._started,
            "motors": True,
            "motor_vector_multiplier": self.driver.speed_multiplier,
            "motor_output_scale": self.driver.output_scale,
            "sensors": sensors.healthy and not sensors.is_stale(),
            "camera": vision.healthy and camera["enabled"] and vision.generation == camera["generation"],
            "camera_enabled": camera["enabled"],
            "camera_state": camera["state"],
            "camera_generation": camera["generation"],
            "vision_backend": vision.backend,
            "camera_error": vision.error,
            "microphone": self.speech.healthy,
            "microphone_error": self.speech.error,
            "microphone_name": self.speech.selected_microphone_name,
            "microphone_index": self.speech.selected_microphone_index,
            "stt_backend": self.speech.stt_backend,
            "vad_backend": self.speech.vad_backend,
            "vad_error": self.speech.vad_error,
            "wake_phrase": self.speech.wake_active,
            "wake_backend": self.speech.wake_backend,
            "wake_error": self.speech.wake_error,
            "wake_gate_passed": self.speech.wake_gate_passed,
            "tts": self.tts.healthy,
            "tts_backend": self.tts.backend,
            "tts_error": self.tts.error,
            "tts_voice": self.tts.voice,
            "tts_playback_device": self.tts.playback_device or "default",
            "tts_volume_percent": self.tts.volume_percent,
            "tts_gain_db": self.tts.gain_db,
            "tts_audio_output": self.tts.audio_output,
            "tts_active_output": self.tts.active_output,
            "audio_playback": self.tts.playback_active.is_set(),
            "database": True,
            "llm": self.mcp_agent.ready,
            "llm_loading": False,
            "llm_backend": f"groq:{self.mcp_agent.model}" if self.mcp_agent.ready else "unavailable",
            "llm_error": self.mcp_agent.error,
            "cloud": self.mcp_agent.ready,
            "cloud_error": self.mcp_agent.error,
            "mcp_agent": self.mcp_agent.ready,
            "groq_vision": self.groq_vision.ready,
            "groq_vision_model": self.groq_vision.model,
            "groq_vision_error": self.groq_vision.error,
            "estop": self.arbiter.estopped,
        }

    def telemetry(self) -> dict:
        return {
            "timestamp": time.time(),
            "mode": self.arbiter.mode.value,
            "sensors": self.sensor_telemetry(),
            "motion": self.arbiter.get_decision().as_dict(),
            "vision": self.vision.get_snapshot().as_dict(),
            "enrollment": self.identity.get_enrollment().as_dict(),
            "mission": self.behavior.mission_snapshot(),
            "demo": self.demo.snapshot(),
            "health": self.health(),
        }
