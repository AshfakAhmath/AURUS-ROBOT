import json
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
import wave
from pathlib import Path

import numpy as np
import pytest

from run import ProcessOwner, is_reclaimable_owner
from src.core.models import IdentityResult, MotionCommand, RobotMode, SensorSnapshot, VisionSnapshot
from src.hardware.motors import MecanumDriver
from src.memory.repository import MemoryRepository
from src.runtime import RobotRuntime
from src.services.behavior_controller import BehaviorController
from src.services.identity_service import IdentityService
from src.services.mcp_agent_service import MCPAgentService
from src.services.motion_arbiter import MotionArbiter
from src.services.sensor_sampler import SensorSampler
from src.services.speech_service import AdaptiveEnergyVad, _parse_arecord_devices
from src.services.speech_service import SpeechService
from src.services.tts_service import TTSService
from src.services.vision_service import VisionService, cv2
from src.web.app import create_app


class FakeDriver:
    def __init__(self):
        self.commands = []

    def drive(self, vx, vy, omega):
        self.commands.append((vx, vy, omega))

    def stop(self):
        self.commands.append((0.0, 0.0, 0.0))


class FakeSensor:
    def __init__(self, values=None):
        self.values = values or {"fl": 100, "f": 100, "fr": 100, "rl": 100, "rr": 100}

    def read_all(self):
        return dict(self.values)


class FakeSampler:
    def __init__(self, snapshot):
        self.snapshot = snapshot

    def get_snapshot(self):
        return self.snapshot


class MotorOutputScaleTests(unittest.TestCase):
    def test_output_scale_is_final_and_uniform_for_active_wheels(self):
        driver = object.__new__(MecanumDriver)
        driver.output_scale = 0.8
        self.assertEqual(driver._pwm_duty(0), 0)
        self.assertEqual(driver._pwm_duty(0.1), 69)
        self.assertEqual(driver._pwm_duty(-0.1), 69)
        self.assertEqual(driver._pwm_duty(1.0), 80)

    def test_default_output_scale_preserves_original_working_duty(self):
        driver = object.__new__(MecanumDriver)
        driver.output_scale = 1.0
        self.assertEqual(driver._pwm_duty(0.1), 86)
        self.assertEqual(driver._pwm_duty(1.0), 100)


def snapshot(**changes):
    values = dict(timestamp=time.monotonic(), fl=100, f=100, fr=100, rl=100, rr=100, healthy=True, error="")
    values.update(changes)
    return SensorSnapshot(**values)


class SensorSamplerTests(unittest.TestCase):
    def test_sample_once_publishes_all_five_values(self):
        sampler = SensorSampler(FakeSensor())
        result = sampler.sample_once()
        self.assertTrue(result.healthy)
        self.assertEqual(result.front_min, 100)
        self.assertEqual(result.rear_min, 100)

    def test_non_finite_sensor_value_marks_snapshot_unhealthy(self):
        sampler = SensorSampler(FakeSensor({"fl": 100, "f": float("nan"), "fr": 100, "rl": 100, "rr": 100}))
        result = sampler.sample_once()
        self.assertFalse(result.healthy)
        self.assertIn("non-finite", result.error)


class MotionArbiterTests(unittest.TestCase):
    def build(self, sensor_snapshot):
        driver = FakeDriver()
        arbiter = MotionArbiter(driver, FakeSampler(sensor_snapshot))
        arbiter.set_dashboard_connected(True)
        arbiter.set_mode(RobotMode.MANUAL)
        return driver, arbiter

    def test_front_hard_stop_rejects_forward_motion(self):
        _, arbiter = self.build(snapshot(f=15))
        arbiter.command("test", 0.5, 0, 0)
        decision = arbiter.tick()
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.final_vx, 0)
        self.assertIn("front obstacle", decision.reason)

    def test_caution_zone_reduces_forward_speed(self):
        _, arbiter = self.build(snapshot(f=30, fl=50, fr=50))
        arbiter.command("test", 0.5, 0, 0)
        decision = arbiter.tick()
        self.assertTrue(decision.allowed)
        self.assertAlmostEqual(decision.final_vx, 0.175)

    def test_stale_sensor_data_stops_motion(self):
        _, arbiter = self.build(snapshot(timestamp=time.monotonic() - 1.0))
        arbiter.command("test", 0.5, 0, 0)
        self.assertFalse(arbiter.tick().allowed)

    def test_expired_command_is_deadman_stopped(self):
        _, arbiter = self.build(snapshot())
        arbiter.submit(MotionCommand("test", 0.5, 0, 0, expires_at=time.monotonic() - 0.01))
        decision = arbiter.tick()
        self.assertFalse(decision.allowed)
        self.assertIn("expired", decision.reason)

    def test_estop_latches_until_explicit_clear(self):
        _, arbiter = self.build(snapshot())
        arbiter.emergency_stop()
        self.assertFalse(arbiter.command("test", 0.5, 0, 0))
        self.assertEqual(arbiter.mode, RobotMode.ESTOP)
        arbiter.clear_estop()
        self.assertEqual(arbiter.mode, RobotMode.IDLE)

    def test_dashboard_disconnect_stops_motion(self):
        _, arbiter = self.build(snapshot())
        arbiter.command("test", 0.5, 0, 0)
        arbiter.set_dashboard_connected(False)
        self.assertFalse(arbiter.tick().allowed)

    def test_non_finite_sensor_data_stops_motion(self):
        _, arbiter = self.build(snapshot(f=float("nan")))
        arbiter.command("test", 0.5, 0, 0)
        decision = arbiter.tick()
        self.assertFalse(decision.allowed)
        self.assertIn("invalid", decision.reason)

    def test_dashboard_disconnect_rejects_new_motion(self):
        _, arbiter = self.build(snapshot())
        arbiter.set_dashboard_connected(False)
        self.assertFalse(arbiter.command("test", 0.5, 0, 0))

    def test_degraded_front_sensor_mode_uses_wider_diagonal_stop_margin(self):
        driver = FakeDriver()
        arbiter = MotionArbiter(
            driver,
            FakeSampler(snapshot(fl=40, f=400, fr=100)),
            front_sensor_enabled=False,
        )
        arbiter.set_dashboard_connected(True)
        arbiter.set_mode(RobotMode.EXPLORING)
        arbiter.command("explore", 0.12, 0, 0)
        decision = arbiter.tick()
        self.assertFalse(decision.allowed)
        self.assertIn("front obstacle", decision.reason)

    def test_degraded_front_sensor_mode_ignores_failed_center_reading(self):
        driver = FakeDriver()
        arbiter = MotionArbiter(
            driver,
            FakeSampler(snapshot(fl=100, f=0, fr=100)),
            front_sensor_enabled=False,
        )
        arbiter.set_dashboard_connected(True)
        arbiter.set_mode(RobotMode.EXPLORING)
        arbiter.command("explore", 0.12, 0, 0)
        self.assertTrue(arbiter.tick().allowed)


class MemoryAndIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = MemoryRepository(Path(self.temp.name) / "memory.db")

    def tearDown(self):
        self.temp.cleanup()

    def test_name_fact_and_embedding_survive_repository_reload(self):
        user_id = self.repo.ensure_user("Humaidh")
        self.repo.remember(user_id, "likes robotics")
        self.repo.save_embedding(user_id, np.array([1.0, 0.0], dtype=np.float32), 20)
        second = MemoryRepository(Path(self.temp.name) / "memory.db")
        self.assertEqual(second.memories_for(user_id), ["likes robotics"])
        embeddings = second.load_embeddings()
        self.assertEqual(embeddings[0][1], "Humaidh")

    def test_enrollment_requires_samples_and_confirmation_frames(self):
        identity = IdentityService(self.repo, threshold=0.4, required_samples=3, confirmation_frames=2)
        identity.begin_enrollment("Evaluator")
        for _ in range(3):
            result = identity.process_embedding(np.array([1.0, 0.0, 0.0], dtype=np.float32))
        self.assertEqual(result.status, "known")
        identity.clear_current()
        first = identity.process_embedding(np.array([1.0, 0.0, 0.0], dtype=np.float32))
        second = identity.process_embedding(np.array([1.0, 0.0, 0.0], dtype=np.float32))
        self.assertEqual(first.status, "uncertain")
        self.assertEqual(second.name, "Evaluator")


class VisionPipelineTests(unittest.TestCase):
    def test_picamera_rgb888_is_already_opencv_bgr(self):
        frame = np.array([[[11, 22, 233]]], dtype=np.uint8)
        converted = VisionService._picamera_array_to_bgr(frame, "RGB888")
        np.testing.assert_array_equal(converted, frame)

    @unittest.skipIf(cv2 is None, "OpenCV is unavailable")
    def test_picamera_bgr888_memory_is_converted_from_rgb(self):
        frame = np.array([[[233, 22, 11]]], dtype=np.uint8)
        converted = VisionService._picamera_array_to_bgr(frame, "BGR888")
        np.testing.assert_array_equal(converted, np.array([[[11, 22, 233]]], dtype=np.uint8))

    def test_face_detection_threshold_is_clamped(self):
        service = VisionService(None, Path("models"), detection_threshold=1.5)
        self.assertEqual(service.detection_threshold, 0.95)

    @unittest.skipIf(cv2 is None, "OpenCV is unavailable")
    def test_haar_face_crop_can_still_produce_sface_embedding(self):
        class FakeSFace:
            def feature(self, aligned):
                self.aligned_shape = aligned.shape
                return np.array([[0.1, 0.2, 0.3]], dtype=np.float32)

        service = VisionService(None, Path("models"))
        service._recognizer = FakeSFace()
        frame = np.full((256, 320, 3), 127, dtype=np.uint8)
        embedding = service._embedding(frame, None, (80, 40, 90, 120))
        self.assertEqual(service._recognizer.aligned_shape, (112, 112, 3))
        np.testing.assert_array_equal(embedding, np.array([0.1, 0.2, 0.3], dtype=np.float32))


class EnvironmentObservationTests(unittest.TestCase):
    class FakeVision:
        def __init__(self, vision_snapshot):
            self.vision_snapshot = vision_snapshot

        def get_snapshot(self):
            return self.vision_snapshot

        def get_jpeg(self):
            return b"jpeg"

    class FakeAgent:
        def __init__(self):
            self.calls = 0

        def describe(self, _jpeg):
            self.calls += 1
            return "A person is visible."

    def test_offline_frame_is_not_sent_to_vision_model(self):
        agent = self.FakeAgent()
        events = []
        controller = BehaviorController(
            None,
            None,
            self.FakeVision(VisionSnapshot(timestamp=time.monotonic(), healthy=False)),
            lambda event, payload: events.append((event, payload)),
            agent,
        )
        controller._observe_environment()
        self.assertEqual(agent.calls, 0)
        self.assertEqual(events, [])

    def test_fresh_camera_frame_is_sent_to_vision_model(self):
        agent = self.FakeAgent()
        events = []
        controller = BehaviorController(
            None,
            None,
            self.FakeVision(VisionSnapshot(timestamp=time.monotonic(), healthy=True)),
            lambda event, payload: events.append((event, payload)),
            agent,
        )
        controller._observe_environment()
        self.assertEqual(agent.calls, 1)
        self.assertEqual(events[0][0], "behavior_notice")


class CuriosityMissionTests(unittest.TestCase):
    class FakeArbiter:
        front_sensor_enabled = True
        caution_cm = 35.0
        degraded_caution_cm = 75.0
        mode = RobotMode.EXPLORING
        estopped = False

        def __init__(self):
            self.commands = []

        def command(self, source, vx, vy, omega, ttl=0.3, priority=10):
            self.commands.append((source, vx, vy, omega, ttl, priority))
            return True

        def halt(self, source):
            self.commands.append((source, 0, 0, 0, 0, 100))

        def set_mode(self, mode):
            self.mode = mode
            return True

    class DiscoveryAgent:
        def __init__(self, observation="A red chair is visible ahead."):
            self.observation = observation
            self.calls = 0

        def analyze_discovery(self, _jpeg, _previous):
            self.calls += 1
            return {
                "observation": self.observation,
                "category": "object",
                "interesting": True,
                "confidence": 0.9,
            }

    def build(self, sensor_snapshot=None, agent=None, playback_active=None):
        events = []
        arbiter = self.FakeArbiter()
        sampler = FakeSampler(sensor_snapshot or snapshot())
        vision = EnvironmentObservationTests.FakeVision(
            VisionSnapshot(timestamp=time.monotonic(), healthy=True)
        )
        controller = BehaviorController(
            arbiter, sampler, vision,
            lambda event, payload: events.append((event, payload)),
            agent or self.DiscoveryAgent(),
            playback_active=playback_active,
        )
        return controller, arbiter, sampler, events

    def test_mission_records_discoveries_and_returns_summary(self):
        controller, _, _, events = self.build()
        controller.begin_exploration()
        controller._add_discovery("scene", "Visual discovery", "A workbench is visible.")
        summary = controller.finish_exploration(emit=False)
        self.assertEqual(summary["observations"], 1)
        self.assertEqual(summary["route_changes"], 0)
        self.assertIn("1 discovery", summary["text"])
        self.assertFalse(controller.mission_snapshot()["active"])
        self.assertEqual(events[0][0], "mission_update")

    def test_obstacle_cancels_exploration_without_automatic_rerouting(self):
        controller, arbiter, sampler, events = self.build(snapshot(fl=22, f=18, fr=60))
        controller.begin_exploration()
        controller._explore()
        controller._explore()
        mission = controller.mission_snapshot()
        self.assertEqual(mission["obstacles"], 1)
        self.assertEqual(mission["route_changes"], 0)
        self.assertTrue(any(event == "discovery" for event, _ in events))
        self.assertFalse(any(command[0] == "explore-avoid" for command in arbiter.commands))
        self.assertEqual(arbiter.mode, RobotMode.IDLE)

        sampler.snapshot = snapshot()
        controller._explore()
        sampler.snapshot = snapshot(fl=60, f=18, fr=22)
        controller._explore()
        self.assertEqual(controller.mission_snapshot()["obstacles"], 1)

    def test_repeated_visual_observation_is_suppressed(self):
        agent = self.DiscoveryAgent()
        controller, _, _, events = self.build(agent=agent)
        controller.begin_exploration()
        controller._observe_environment()
        controller._observe_environment()
        discoveries = [payload for event, payload in events if event == "discovery"]
        notices = [payload for event, payload in events if event == "behavior_notice"]
        self.assertEqual(agent.calls, 2)
        self.assertEqual(len(discoveries), 1)
        self.assertEqual(len(notices), 1)
        self.assertEqual(controller.mission_snapshot()["observations"], 1)

    def test_explore_holds_still_while_robot_is_speaking(self):
        playback = threading.Event()
        playback.set()
        controller, arbiter, _, _ = self.build(playback_active=playback)
        controller.begin_exploration()
        controller._explore()
        source, vx, vy, omega, _, priority = arbiter.commands[-1]
        self.assertEqual(source, "explore-speaking-pause")
        self.assertEqual((vx, vy, omega), (0.0, 0.0, 0.0))
        self.assertEqual(priority, 30)


class AudioPipelineTests(unittest.TestCase):
    def test_arecord_usb_device_uses_stable_numeric_card_address(self):
        listing = "card 3: Device [USB PnP Sound Device], device 0: USB Audio [USB Audio]"
        self.assertEqual(_parse_arecord_devices(listing)[0][0], "plughw:3,0")

    def test_tts_prefers_installed_local_piper_over_network_backend(self):
        with tempfile.TemporaryDirectory() as directory:
            model = Path(directory) / "voice.onnx"
            model.write_bytes(b"model")
            with patch("src.services.tts_service.importlib.util.find_spec", return_value=object()):
                service = TTSService(model)
        self.assertEqual(service.backend, "piper")

    def test_adaptive_vad_ends_after_voiced_audio_and_silence(self):
        vad = AdaptiveEnergyVad(sample_rate=16000, frame_samples=512, max_seconds=3.0)
        silence = np.zeros(512, dtype=np.int16).tobytes()
        voice = np.full(512, 2200, dtype=np.int16).tobytes()
        for _ in range(5):
            self.assertIsNone(vad.accept(silence))
        for _ in range(8):
            self.assertIsNone(vad.accept(voice))
        utterance = None
        for _ in range(20):
            utterance = vad.accept(silence)
            if utterance is not None:
                break
        self.assertIsNotNone(utterance)
        self.assertGreater(len(utterance), len(voice) * 8)

    def test_adaptive_vad_does_not_emit_silence(self):
        vad = AdaptiveEnergyVad(sample_rate=16000, frame_samples=512, max_seconds=1.0)
        silence = np.zeros(512, dtype=np.int16).tobytes()
        self.assertTrue(all(vad.accept(silence) is None for _ in range(40)))
        self.assertIsNone(vad.flush())

    def test_tts_marks_playback_active_while_speaking(self):
        playback = threading.Event()
        observed = threading.Event()
        completed = threading.Event()
        service = TTSService(playback_active=playback)

        def fake_speak(_text):
            if playback.is_set():
                observed.set()
            completed.set()

        service._speak = fake_speak
        service.start()
        try:
            self.assertTrue(service.speak("pipeline check"))
            self.assertTrue(completed.wait(1.0))
            self.assertTrue(observed.is_set())
            deadline = time.monotonic() + 1.0
            while playback.is_set() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertFalse(playback.is_set())
        finally:
            service.stop()

    def test_tts_completion_runs_after_playback_is_released(self):
        playback = threading.Event()
        completed = threading.Event()
        playback_state_at_completion = []
        service = TTSService(playback_active=playback)
        service._speak = lambda _text: None
        service.start()
        try:
            self.assertTrue(service.speak(
                "I'm listening.",
                on_complete=lambda: (
                    playback_state_at_completion.append(playback.is_set()),
                    completed.set(),
                ),
            ))
            self.assertTrue(completed.wait(1.0))
            self.assertEqual(playback_state_at_completion, [False])
        finally:
            service.stop()

    def test_wake_detector_latches_one_event_until_runtime_releases_it(self):
        detected = []
        service = SpeechService(model_path="missing", wake_enabled=True)
        service._wake_callback = detected.append
        service._notify_wake_detected("HEY_AURUS")
        service._notify_wake_detected("HEY_AURUS")
        self.assertEqual(detected, ["HEY_AURUS"])
        self.assertTrue(service.listening)
        service.cancel_wake()
        service._notify_wake_detected("AURUS")
        self.assertEqual(detected, ["HEY_AURUS", "AURUS"])

    def test_tts_software_volume_boosts_piper_waveform(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "volume.wav"
            samples = np.full(320, 1000, dtype=np.int16)
            with wave.open(str(path), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(16000)
                output.writeframes(samples.tobytes())
            service = TTSService(volume_percent=100, gain_db=0)
            service._amplify_wav(str(path))
            with wave.open(str(path), "rb") as source:
                boosted = np.frombuffer(source.readframes(source.getnframes()), dtype=np.int16)
            self.assertEqual(int(boosted.max()), 2000)

    def test_tts_audio_output_mode_switching(self):
        service = TTSService()
        self.assertEqual(service.audio_output, "auto")
        self.assertTrue(service.set_audio_output("browser"))
        self.assertEqual(service.audio_output, "browser")
        self.assertTrue(service.set_audio_output("pi"))
        self.assertEqual(service.audio_output, "pi")
        self.assertTrue(service.set_audio_output("auto"))
        self.assertEqual(service.audio_output, "auto")
        self.assertFalse(service.set_audio_output("invalid-mode"))
        self.assertEqual(service.audio_output, "auto")

    def test_tts_browser_mode_emits_audio(self):
        emitted = []
        def mock_emit(event, payload):
            emitted.append((event, payload))

        service = TTSService(audio_output="browser", audio_emit=mock_emit)
        with tempfile.TemporaryDirectory() as directory:
            dummy_wav = Path(directory) / "test.wav"
            dummy_wav.write_bytes(b"RIFFdummydata")
            service._play_or_stream(str(dummy_wav))

        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0][0], "play_audio")
        self.assertEqual(emitted[0][1]["format"], "wav")
        self.assertEqual(service.active_output, "browser")

    def test_tts_auto_mode_falls_back_to_browser_on_local_error(self):
        emitted = []
        def mock_emit(event, payload):
            emitted.append((event, payload))

        service = TTSService(audio_output="auto", audio_emit=mock_emit)
        with tempfile.TemporaryDirectory() as directory:
            dummy_wav = Path(directory) / "fallback.wav"
            dummy_wav.write_bytes(b"RIFFfallbackdata")
            with patch.object(service, "_play_wav", side_effect=RuntimeError("USB speaker disconnected")):
                service._play_or_stream(str(dummy_wav))

        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0][0], "play_audio")
        self.assertEqual(service.active_output, "browser")


class StartupResourceTests(unittest.TestCase):
    def test_same_user_project_python_owner_is_reclaimable(self):
        root = Path("/home/tt-11/AURUS-3.0")
        owner = ProcessOwner(
            pid=321, ppid=1, uid=1000,
            executable="/home/tt-11/AURUS-3.0/.venv/bin/python",
            command="/home/tt-11/AURUS-3.0/.venv/bin/python /home/tt-11/AURUS-3.0/run.py",
            devices=("/dev/video0",),
        )
        self.assertTrue(is_reclaimable_owner(owner, root, 1000))

    def test_unrelated_or_foreign_process_is_not_reclaimable(self):
        root = Path("/home/tt-11/AURUS-3.0")
        unrelated = ProcessOwner(
            pid=322, ppid=50, uid=1000, executable="/usr/bin/cheese",
            command="/usr/bin/cheese", devices=("/dev/video0",),
        )
        foreign = ProcessOwner(
            pid=323, ppid=1, uid=0, executable="/usr/bin/arecord",
            command="arecord -D plughw:3,0", devices=("/dev/snd/pcmC3D0c",),
        )
        self.assertFalse(is_reclaimable_owner(unrelated, root, 1000))
        self.assertFalse(is_reclaimable_owner(foreign, root, 1000))

    def test_orphaned_same_user_capture_helper_is_reclaimable(self):
        owner = ProcessOwner(
            pid=324, ppid=1, uid=1000, executable="/usr/bin/rpicam-hello",
            command="rpicam-hello -t 0", devices=("/dev/media0",),
        )
        self.assertTrue(
            is_reclaimable_owner(owner, Path("/home/tt-11/AURUS-3.0"), 1000)
        )


class FakeRuntimeArbiter:
    def __init__(self, mode=RobotMode.IDLE, estopped=False):
        self.mode = mode
        self.estopped = estopped
        self.mode_changes = []
        self.halts = []
        self.commands = []

    def set_mode(self, mode):
        if self.estopped and mode != RobotMode.ESTOP:
            return False
        self.mode = mode
        self.mode_changes.append(mode)
        return True

    def command(self, source, vx, vy, omega, ttl=0.3, priority=10):
        self.commands.append((source, vx, vy, omega, ttl, priority))
        return False

    def halt(self, source="system"):
        self.halts.append(source)


class RuntimeHarness(RobotRuntime):
    def __init__(self):
        self._lock = threading.RLock()
        self.arbiter = FakeRuntimeArbiter()
        self.actions = []
        self.repository = self

    def _announce(self, response, source, transcript, provider, fallback_reason=""):
        return {
            "response": response,
            "source": source,
            "transcript": transcript,
            "provider": provider,
            "fallback_reason": fallback_reason,
        }

    def _current_person(self):
        return IdentityResult("known", 7, "Evaluator", 0.9)

    def memories_for(self, user_id, limit=8):
        return ["likes reliable robots"]

    def clear_estop(self):
        self.actions.append("clear")

    def emergency_stop(self):
        self.actions.append("estop")

    def remember_fact(self, text):
        self.actions.append(("remember", text))
        return True, f"I will remember that {text}."


class RuntimeCommandTests(unittest.TestCase):
    def test_wake_acknowledges_before_opening_command_capture(self):
        class FakeSpeech:
            error = ""

            def __init__(self):
                self.callback = None
                self.cancelled = False

            def listen_once(self, callback):
                self.callback = callback
                return True

            def cancel_wake(self):
                self.cancelled = True

        class FakeTTS:
            def __init__(self):
                self.text = ""
                self.on_complete = None

            def speak(self, text, on_complete=None):
                self.text = text
                self.on_complete = on_complete
                return True

        runtime = RuntimeHarness()
        runtime.speech = FakeSpeech()
        runtime.tts = FakeTTS()
        events = []
        runtime._event_sink = lambda event, payload: events.append((event, payload))

        runtime._wake_detected("HEY_AURUS")

        self.assertEqual(runtime.arbiter.mode, RobotMode.LISTENING)
        self.assertEqual(runtime.tts.text, "I'm listening.")
        self.assertIsNone(runtime.speech.callback)
        self.assertEqual(events[0][0], "conversation")
        self.assertEqual(events[0][1]["transcript"], "Hey AURUS")

        runtime.tts.on_complete()
        self.assertEqual(runtime.speech.callback, runtime._speech_result)

    def test_clear_estop_is_not_misread_as_estop(self):
        runtime = RuntimeHarness()
        response = runtime.handle_text("clear the e-stop")
        self.assertEqual(runtime.actions, ["clear"])
        self.assertIn("cleared", response["response"].lower())

    def test_mission_status_is_answered_locally(self):
        class FakeBehavior:
            def mission_snapshot(self):
                return {
                    "mission_id": 2, "active": True, "observations": 3,
                    "people": 1, "route_changes": 2,
                }

        runtime = RuntimeHarness()
        runtime.behavior = FakeBehavior()
        response = runtime.handle_text("What have you found?")
        self.assertEqual(response["provider"], "local")
        self.assertIn("3 discoveries", response["response"])
        self.assertIn("1 person", response["response"])

    def test_recall_phrase_is_not_stored_as_a_new_fact(self):
        runtime = RuntimeHarness()
        response = runtime.handle_text("do you remember me")
        self.assertEqual(runtime.actions, [])
        self.assertIn("likes reliable robots", response["response"])

    def test_voice_result_returns_to_idle_after_non_mode_command(self):
        runtime = RuntimeHarness()
        runtime.arbiter.mode = RobotMode.LISTENING
        runtime._speech_result("hello", None)
        self.assertEqual(runtime.arbiter.mode, RobotMode.IDLE)

    def test_visual_question_bypasses_mcp_tool_loop_for_groq_vision(self):
        class FakeVision:
            def get_snapshot(self):
                return VisionSnapshot(timestamp=time.monotonic(), healthy=True)

            def get_jpeg(self):
                return b"current-frame"

        class FakeVisionProvider:
            ready = True
            error = ""

            def __init__(self):
                self.frames = []

            def describe(self, jpeg):
                self.frames.append(jpeg)
                return "A person is standing in front of the camera."

            def execute_command(self, _text):
                raise AssertionError("visual question entered the MCP tool loop")

        runtime = RuntimeHarness()
        runtime.vision = FakeVision()
        runtime.groq_vision = FakeVisionProvider()
        response = runtime.handle_text("What do you see?")
        self.assertEqual(response["provider"], "groq-vision")
        self.assertEqual(runtime.groq_vision.frames, [b"current-frame"])


class DriverMustNotBeCalled:
    is_simulation = True

    def __getattr__(self, name):
        raise AssertionError(f"MCP animation bypassed the arbiter via driver.{name}")


class MissingUserRepository:
    def get_user(self, user_id):
        return None

    def remember(self, user_id, fact):
        raise AssertionError("A fact was stored for an unknown user")


class MCPRuntimeHarness:
    def __init__(self):
        self.arbiter = FakeRuntimeArbiter()
        self.driver = DriverMustNotBeCalled()
        self.repository = MissingUserRepository()

    def emit(self, event, payload):
        pass


class MCPAgentSafetyTests(unittest.TestCase):
    def test_obvious_motion_intent_routes_locally_without_cloud_round_trip(self):
        service = MCPAgentService(MCPRuntimeHarness(), api_key="")
        self.assertEqual(service.route_intent("please drive forward"), "ACTION")

    def test_environment_description_routes_to_camera_tool_loop(self):
        service = MCPAgentService(MCPRuntimeHarness(), api_key="")
        self.assertEqual(service.route_intent("what do you see?"), "ACTION")

    def test_animation_reports_blocked_when_arbiter_rejects_it(self):
        service = MCPAgentService(MCPRuntimeHarness(), api_key="")
        result = json.loads(service._dispatch_tool("perform_animation", {"name": "spin"}))
        self.assertEqual(result["status"], "blocked")
        self.assertIn("rejected", result["reason"])

    def test_invalid_direction_does_not_default_to_forward(self):
        service = MCPAgentService(MCPRuntimeHarness(), api_key="")
        result = json.loads(service._dispatch_tool("move_rover", {"direction": "sideways-ish"}))
        self.assertEqual(result["status"], "error")

    def test_stop_tool_leaves_autonomous_mode_idle(self):
        runtime = MCPRuntimeHarness()
        runtime.arbiter.mode = RobotMode.EXPLORING
        service = MCPAgentService(runtime, api_key="")
        result = json.loads(service._dispatch_tool("stop_rover", {}))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(runtime.arbiter.mode, RobotMode.IDLE)

    def test_memory_tool_rejects_unknown_user_without_writing(self):
        service = MCPAgentService(MCPRuntimeHarness(), api_key="")
        result = json.loads(service._dispatch_tool("remember_fact", {"fact": "likes robots"}))
        self.assertEqual(result["status"], "error")
        self.assertIn("enroll", result["message"])


class WebRuntimeHarness:
    def set_event_sink(self, callback):
        self.event_sink = callback

    def dashboard_connected(self, connected):
        self.connected = connected

    def health(self):
        return {}

    def telemetry(self):
        return {}

    def manual_drive(self, vx, vy, omega, sequence):
        return False


class WebAdapterTests(unittest.TestCase):
    def test_malformed_manual_payload_is_rejected_without_handler_failure(self):
        app, socketio, _, _ = create_app(WebRuntimeHarness())
        client = socketio.test_client(app)
        client.get_received()
        client.emit("manual_drive", None)
        errors = [event for event in client.get_received() if event["name"] == "command_error"]
        self.assertEqual(len(errors), 1)
        client.disconnect()


@pytest.fixture
def expo_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "")
    monkeypatch.delenv("GROQ_VISION_MODEL", raising=False)
    runtime = RobotRuntime(tmp_path)
    runtime.vision._snapshot = VisionSnapshot(timestamp=time.monotonic(), healthy=True)
    runtime.vision._jpeg = b"current-frame"
    return runtime


@pytest.mark.parametrize("source, transcript", [
    ("text", "Introduce yourself!"),
    ("browser-mic", "Could you please introduce yourself to everyone?"),
])
def test_dashboard_introduction_starts_demo_for_requesting_client(expo_runtime, monkeypatch, source, transcript):
    runtime = expo_runtime
    monkeypatch.setattr(runtime.tts, "prepare_demo_speech", lambda: True)
    monkeypatch.setattr(runtime.tts, "speak", lambda *args, **kwargs: False)
    monkeypatch.setattr(runtime.demo, "_run", lambda: None)
    monkeypatch.setattr(runtime, "_announce", Mock())
    app, socketio, _, _ = create_app(runtime)
    monkeypatch.setattr(socketio, "start_background_task", lambda run, *args: run(*args))
    operator = socketio.test_client(app)
    operator_sid = runtime._demo_dashboard_sid
    visitor = socketio.test_client(app)
    try:
        if source == "browser-mic":
            operator.emit("browser_wake_detected", {})
            assert runtime.arbiter.mode == RobotMode.LISTENING
            assert any(event["name"] == "browser_listen_ready" for event in operator.get_received())
            assert all(event["name"] != "browser_listen_ready" for event in visitor.get_received())
        operator.emit("send_text", {"text": transcript, "source": source})
        assert runtime.demo.active
        assert runtime.arbiter.mode == RobotMode.PERFORMING
        assert runtime.demo._owner_sid == operator_sid
        runtime._announce.assert_not_called()
    finally:
        operator.disconnect()
        visitor.disconnect()
        if runtime.demo._thread:
            runtime.demo._thread.join(1)


def test_local_transcription_can_start_demo(expo_runtime, monkeypatch):
    runtime = expo_runtime
    monkeypatch.setattr(runtime.tts, "prepare_demo_speech", lambda: True)
    monkeypatch.setattr(runtime.demo, "_run", lambda: None)
    monkeypatch.setattr(runtime.speech, "_transcribe", lambda _: "Please introduce yourself.")
    monkeypatch.setattr(runtime, "_announce", Mock())
    runtime.arbiter.set_mode(RobotMode.LISTENING)
    finished = threading.Event()

    def deliver(text, error):
        try:
            assert runtime.speech.processing
            assert not runtime.speech.listening
            runtime._speech_result(text, error)
        finally:
            finished.set()

    runtime.speech._complete_capture(deliver, b"pcm")
    assert finished.wait(2)
    assert runtime.demo.active
    assert runtime.arbiter.mode == RobotMode.PERFORMING
    runtime._announce.assert_not_called()
    runtime.stop_exhibition_demo()
    runtime.demo._thread.join(1)


@pytest.mark.parametrize("mode", [RobotMode.ESTOP, RobotMode.LISTENING, RobotMode.IDLE])
def test_chat_introduction_keeps_demo_safety_guards(expo_runtime, monkeypatch, mode):
    runtime = expo_runtime
    monkeypatch.setattr(runtime.tts, "prepare_demo_speech", Mock(return_value=True))
    if mode == RobotMode.ESTOP:
        runtime.arbiter.emergency_stop()
    else:
        runtime.arbiter.set_mode(mode)
    if mode == RobotMode.IDLE:
        runtime.speech._pending_callback = Mock()
    monkeypatch.setattr(runtime, "_announce", Mock())
    assert runtime.handle_text("Introduce yourself.")["status"] == "error"
    assert runtime.arbiter.mode == mode
    assert not runtime.demo.active
    runtime.tts.prepare_demo_speech.assert_not_called()


def test_mentioning_an_introduction_does_not_start_the_demo(expo_runtime, monkeypatch):
    start = Mock()
    monkeypatch.setattr(expo_runtime, "start_exhibition_demo", start)
    monkeypatch.setattr(expo_runtime, "_announce", Mock())
    monkeypatch.setattr(expo_runtime.mcp_agent, "execute_command", Mock(return_value={}))
    expo_runtime.handle_text("Don't introduce yourself.")
    start.assert_not_called()


def test_browser_command_window_is_owned_by_the_waking_client(expo_runtime, monkeypatch):
    runtime = expo_runtime
    monkeypatch.setattr(runtime.tts, "speak", lambda *args, **kwargs: False)
    monkeypatch.setattr(runtime.tts, "prepare_demo_speech", Mock(return_value=True))
    monkeypatch.setattr(runtime.demo, "_run", lambda: None)
    app, socketio, _, _ = create_app(runtime)
    monkeypatch.setattr(socketio, "start_background_task", lambda run, *args: run(*args))
    operator, visitor = socketio.test_client(app), socketio.test_client(app)
    try:
        operator.emit("browser_wake_detected", {})
        visitor.emit("send_text", {"text": "Introduce yourself.", "source": "browser-mic"})
        visitor.emit("browser_listen_timeout", {})
        assert runtime.arbiter.mode == RobotMode.LISTENING
        assert not runtime.demo.active
        runtime.tts.prepare_demo_speech.assert_not_called()
        operator.disconnect()
        assert runtime.arbiter.mode == RobotMode.IDLE
    finally:
        if operator.is_connected():
            operator.disconnect()
        visitor.disconnect()


def test_voice_stop_with_punctuation_stops_demo(expo_runtime, monkeypatch):
    runtime = expo_runtime
    monkeypatch.setattr(runtime, "_announce", Mock())
    monkeypatch.setattr(runtime.tts, "prepare_demo_speech", lambda: True)
    monkeypatch.setattr(runtime.demo, "_run", lambda: None)
    assert runtime.start_exhibition_demo()
    runtime.handle_browser_speech("Stop.")
    assert runtime.demo.snapshot()["status"] == "cancelled"
    assert runtime.arbiter.mode == RobotMode.IDLE
    runtime.demo._thread.join(1)


def test_queued_intro_from_disconnected_dashboard_does_not_start_demo(expo_runtime, monkeypatch):
    runtime = expo_runtime
    monkeypatch.setattr(runtime.tts, "prepare_demo_speech", Mock(return_value=True))
    monkeypatch.setattr(runtime.demo, "_run", lambda: None)
    app, socketio, _, _ = create_app(runtime)
    jobs = []
    monkeypatch.setattr(socketio, "start_background_task", lambda run, *args: jobs.append((run, args)))
    operator, visitor = socketio.test_client(app), socketio.test_client(app)
    try:
        operator.emit("send_text", {"text": "Introduce yourself."})
        operator.disconnect()
        run, args = jobs.pop()
        run(*args)
        assert not runtime.demo.active
        runtime.tts.prepare_demo_speech.assert_not_called()
    finally:
        if operator.is_connected():
            operator.disconnect()
        visitor.disconnect()


@pytest.mark.parametrize("pipeline", ["wake", "capture"])
def test_microphone_gain_reaches_inference_without_wrapping(expo_runtime, monkeypatch, pipeline):
    from src.services import speech_service as speech_module

    service = expo_runtime.speech
    pcm = np.array([0, 500, -500, 30000, -30000], dtype=np.int16).tobytes()

    def read(*args, **kwargs):
        service._stop.set()
        return pcm

    stream = SimpleNamespace(read=read, stop_stream=Mock(), close=Mock())
    monkeypatch.setattr(speech_module, "pyaudio", SimpleNamespace(PyAudio=lambda: SimpleNamespace(terminate=Mock())))
    monkeypatch.setattr(service, "_open_stream", lambda _: stream)
    monkeypatch.setattr(SpeechService, "wake_active", property(lambda _: pipeline == "wake"))
    service._keyword_engine = SimpleNamespace(accept=Mock(return_value=""))
    service._vad_engine = SimpleNamespace(accept=Mock(return_value=None), reset=Mock())
    if pipeline == "capture":
        service._pending_callback = Mock()
    service._audio_loop()
    engine = service._keyword_engine if pipeline == "wake" else service._vad_engine
    engine.accept.assert_called_once()
    gained = np.frombuffer(engine.accept.call_args.args[0], dtype=np.int16)
    assert tuple(gained) == (0, 998, -998, 32767, -32768)


def test_face_display_is_read_only_and_does_not_claim_operator_connection(expo_runtime):
    app, _, _, _ = create_app(expo_runtime)
    client = app.test_client()
    face = client.get("/face")
    assert face.status_code == 200
    assert b"AURUS Face Display" in face.data
    assert b"face.js" in face.data and b"socket.io" not in face.data
    assert b"Emergency stop</button>" not in face.data
    assert b'href="/face"' in client.get("/").data

    state = client.get("/face/state")
    assert state.json == {"expression": "idle", "transcript": ""}
    assert state.cache_control.no_store
    assert client.post("/face/state").status_code == 405
    assert expo_runtime._demo_dashboard_sid is None


def test_face_tracks_capture_processing_and_speech_with_estop_priority(expo_runtime):
    app, _, _, _ = create_app(expo_runtime)
    client = app.test_client()

    def expression():
        return client.get("/face/state").json["expression"]

    expo_runtime.arbiter.set_mode(RobotMode.LISTENING)
    assert expression() == "listening"
    expo_runtime.speech._processing = True
    assert expression() == "thinking"
    expo_runtime.speech.browser_playback_active.set()
    assert expression() == "speaking"
    expo_runtime.arbiter.emergency_stop()
    assert expression() == "estop"


def test_local_playback_sets_speaking_and_clears_it_when_the_player_fails(expo_runtime):
    tts = expo_runtime.tts
    tts.playback_active.set()
    assert expo_runtime.face_expression() == "thinking"

    def fail_playback(*_args, **_kwargs):
        assert expo_runtime.face_expression() == "speaking"
        raise OSError("speaker unavailable")

    with patch("src.services.tts_service.shutil.which", return_value="aplay"), patch(
        "src.services.tts_service.subprocess.run", side_effect=fail_playback
    ):
        with pytest.raises(OSError, match="speaker unavailable"):
            tts._play_wav("speech.wav")
    assert expo_runtime.face_expression() == "thinking"


def mock_object_completion(runtime, content):
    create = Mock(return_value=SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))]))
    runtime.groq_vision._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    return create


def test_operator_scan_identifies_objects_from_the_current_frame(expo_runtime):
    expo_runtime.tts.backend = "piper"
    expo_runtime.tts.speak = Mock(return_value=True)
    create = mock_object_completion(expo_runtime, '{"objects":[" Red mug ","red mug","Notebook"]}')
    app, socketio, _, _ = create_app(expo_runtime)
    operator = socketio.test_client(app)
    other = socketio.test_client(app)
    other.get_received()
    result = operator.emit("detect_objects", callback=True)
    assert result == {"status": "ok", "objects": ["Red mug", "Notebook"], "message": "I see Red mug, Notebook.", "speech_queued": True}
    expo_runtime.tts.speak.assert_called_once()
    assert expo_runtime.tts.speak.call_args.args[0] == "I see Red mug and Notebook."
    assert not other.get_received()
    request = create.call_args.kwargs
    assert request["model"] == "qwen/qwen3.8-27b"
    assert request["response_format"] == {"type": "json_object"}
    assert request["messages"][0]["content"][1]["image_url"]["url"] == "data:image/jpeg;base64,Y3VycmVudC1mcmFtZQ=="
    operator.disconnect()
    other.disconnect()


@pytest.mark.parametrize("content,status", [(' {"objects": []} ', "ok"), ('{"objects":[42]}', "error")])
def test_object_scan_distinguishes_empty_detection_from_invalid_output(expo_runtime, content, status):
    mock_object_completion(expo_runtime, content)
    result = expo_runtime.detect_objects()
    assert result["status"] == status
    assert result["objects"] == []


def test_object_scan_refuses_stale_camera_data(expo_runtime):
    create = mock_object_completion(expo_runtime, '{"objects":["mug"]}')
    expo_runtime.vision._snapshot = VisionSnapshot(timestamp=time.monotonic() - 2, healthy=True)
    result = expo_runtime.detect_objects()
    assert result["status"] == "error"
    assert "fresh camera" in result["message"]
    create.assert_not_called()


def test_inflight_object_scan_blocks_a_second_scan_and_showcase(expo_runtime):
    expo_runtime.tts.prepare_demo_speech = Mock(return_value=True)
    expo_runtime.demo._run = Mock()
    create = mock_object_completion(expo_runtime, '{"objects":["mug"]}')
    completion = create.return_value

    def scan_in_progress(**_kwargs):
        assert expo_runtime.face_expression() == "thinking"
        assert "already running" in expo_runtime.detect_objects()["message"]
        assert not expo_runtime.start_exhibition_demo("operator")
        return completion

    create.side_effect = scan_in_progress
    assert expo_runtime.detect_objects()["objects"] == ["mug"]
    assert expo_runtime.face_expression() == "idle"


def test_voice_object_command_announces_detected_objects(expo_runtime):
    expo_runtime.tts.speak = Mock(return_value=True)
    mock_object_completion(expo_runtime, '{"objects":["water bottle"]}')
    result = expo_runtime.handle_text("Identify this object", source="voice")
    assert result["provider"] == "groq-vision"
    assert result["response"] == "I see water bottle."
    expo_runtime.tts.speak.assert_called_once_with("I see water bottle.")


if __name__ == "__main__":
    unittest.main()
