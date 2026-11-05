"""Live FIT Expo speech and bounded demo tests; never play real audio."""

from pathlib import Path
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock
import wave

import numpy as np
import pytest

from src.core.models import RobotMode
from src.runtime import RobotRuntime
from src.services.demo_script import DEMO_CLIPS, DEMO_ORDER
from src.services.exhibition_demo import ExhibitionDemo
from src.services.tts_service import TTSService, DemoSpeech
from src.web.app import create_app
from tests.test_final_safety import build

class FastEvent(threading.Event):
    def wait(self, timeout=None):
        return self.is_set() if timeout == 0.75 else super().wait(timeout)


def demo_harness():
    sensor, sampler, driver, arbiter, runtime, _ = build()
    spoken = []
    def speak_demo(text, completed, owner, cancelled):
        spoken.append(next(key for key, value in DEMO_CLIPS.items() if value == text))
        completed(True)
        return True
    runtime.tts = SimpleNamespace(busy=False, speak_demo=Mock(side_effect=speak_demo), prepare_demo_speech=Mock(return_value=True), demo_error="")
    runtime.speech = SimpleNamespace(listening=False, browser_playback_active=threading.Event())
    runtime.mcp_agent = SimpleNamespace(busy=False)
    demo = ExhibitionDemo(runtime)
    runtime.demo = demo
    demo._cancelled = FastEvent()
    demo._state.update(active=True, motion_enabled=True, status="running")
    arbiter.set_mode(RobotMode.PERFORMING)
    original = demo._motion
    requested = []
    def short_motion(vx, omega, duration):
        requested.append((vx, omega, duration))
        return original(vx, omega, min(duration, 0.003))
    demo._motion = short_motion
    return sensor, sampler, driver, arbiter, runtime, demo, spoken, requested


def test_predefined_demo_script_keeps_reviewed_introduction_and_closing():
    assert "Please ask them" not in DEMO_CLIPS["closing"]
    assert "FIT Expo" in DEMO_CLIPS["closing"]
    assert "Faculty of Information Technology Expo" in DEMO_CLIPS["introduction"]
    assert "exhibition" not in " ".join(DEMO_CLIPS.values()).lower()
    assert "prototype" in DEMO_CLIPS["introduction"]


def test_complete_script_and_movements_have_no_strafe():
    _, _, driver, arbiter, runtime, demo, spoken, requested = demo_harness()
    demo._run()
    assert spoken == list(DEMO_ORDER)
    assert requested == [(0.2, 0, 0.2), (-0.2, 0, 0.2), (0, -0.2, 0.15), (0, 0.2, 0.15)]
    assert all(vy == 0 for vx, vy, omega in driver.commands)
    assert any(vx > 0 for vx, vy, omega in driver.commands)
    assert driver.commands[-1] == (0, 0, 0)
    assert arbiter.mode == RobotMode.IDLE
    assert demo.snapshot()["status"] == "completed" and not demo.active
    assert runtime.tts.speak_demo.call_count == 6


@pytest.mark.parametrize("fault", ["obstacle", "disabled", "stale"])
def test_blocked_movement_cancels_later_gestures_but_finishes_speech(fault):
    sensor, sampler, driver, _, _, demo, spoken, requested = demo_harness()
    if fault == "obstacle":
        sensor.values["f"] = 40
        sampler.sample_once()
    elif fault == "disabled": sampler.set_enabled("fl", False)
    else:
        from dataclasses import replace
        with sampler._lock:
            sampler._states["fl"] = replace(sampler._states["fl"], sampled_at=time.monotonic() - 1)
            sampler._publish()
    demo._run()
    assert requested == [(0.2, 0, 0.2)]
    assert spoken == ["introduction", "movement", "blocked", "capabilities", "closing"]
    assert all(vector == (0, 0, 0) for vector in driver.commands)
    assert demo.snapshot()["status"] == "completed"


def test_disabled_front_center_skips_turns_without_reenabling_it():
    _, sampler, driver, _, _, demo, spoken, requested = demo_harness()
    sampler.set_enabled("f", False)
    demo._run()
    assert len(requested) == 2
    assert "turn_left" not in spoken and spoken.count("blocked") == 1
    assert all(omega == 0 for vx, vy, omega in driver.commands)
    assert not sampler.get_snapshot().states["f"].enabled


def test_new_obstacle_mid_burst_stops_without_reverse_retry():
    sensor, sampler, driver, arbiter, _, demo, spoken, requested = demo_harness()
    drive = driver.drive
    def obstacle(*vector):
        drive(*vector)
        if vector[0] > 0:
            sensor.values["f"] = 18
            sampler.sample_once()
            arbiter.tick()
    driver.drive = obstacle
    demo._run()
    assert len(requested) == 1 and spoken.count("blocked") == 1
    assert driver.commands[-1] == (0, 0, 0)


@pytest.mark.parametrize("kind", ["stop", "estop", "disconnect", "audio"])
def test_interrupts_prevent_all_later_steps(kind):
    _, _, driver, arbiter, runtime, demo, spoken, _ = demo_harness()
    def speak(text, completed, owner, cancelled):
        spoken.append(next(key for key, value in DEMO_CLIPS.items() if value == text))
        if kind == "stop": demo.cancel()
        if kind == "estop": arbiter.emergency_stop()
        if kind == "disconnect":
            demo._owner_sid = "operator"
            demo.owner_disconnected("operator")
        completed(kind != "audio")
        return True
    runtime.tts.speak_demo.side_effect = speak
    demo._run()
    assert spoken == ["introduction"]
    assert all(vector == (0, 0, 0) for vector in driver.commands)
    assert demo.snapshot()["status"] == "cancelled" and not demo.active
    if kind == "estop": assert arbiter.estopped


def test_start_rejects_busy_and_repeated_runs(monkeypatch):
    _, _, _, arbiter, runtime, demo, _, _ = demo_harness()
    demo._state["active"] = False
    arbiter.set_mode(RobotMode.IDLE)
    runtime.tts.busy = True
    assert not demo.start("operator")
    runtime.tts.busy = False
    runtime.speech.listening = True
    assert not demo.start("operator")
    runtime.speech.listening = False
    runtime.mcp_agent.busy = True
    assert not demo.start("operator")
    runtime.mcp_agent.busy = False
    monkeypatch.setattr(demo, "_run", lambda: demo._cancelled.wait(1))
    assert demo.start("operator")
    assert not demo.start("operator")
    demo.cancel()
    demo._thread.join(1)


def live_service(monkeypatch, **kwargs):
    service = TTSService(**kwargs)
    def synthesize(text, path, **kwargs):
        with wave.open(str(path), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(24000)
            output.writeframes(np.full(2400, 1000, dtype="<i2").tobytes())
    service._demo_speech = SimpleNamespace(ready=True, error="", synthesize=Mock(side_effect=synthesize))
    return service


def test_each_demo_utterance_synthesizes_and_deletes_temporary_audio(monkeypatch):
    service = live_service(monkeypatch, audio_output="pi")
    played = []
    monkeypatch.setattr(service, "_play_wav", lambda path: played.append(Path(path)))
    monkeypatch.setattr(service, "_speak", Mock(side_effect=AssertionError("Must not use normal TTS")))
    observed = []
    for _ in range(2):
        service._run_demo(DemoSpeech(DEMO_CLIPS["closing"], observed.append, "operator", threading.Event()))
    assert observed == [True, True]
    assert service._demo_speech.synthesize.call_count == 2
    assert len(played) == 2 and played[0] != played[1]
    assert all(not path.exists() for path in played)
    service._speak.assert_not_called()
    assert not service.playback_active.is_set()


def test_live_browser_ack_is_scoped_and_waits_for_playback(monkeypatch):
    service = live_service(monkeypatch, audio_output="browser")
    observed = []
    def emit(event, payload):
        assert event == "play_audio" and payload["_recipient"] == "operator"
        assert service.playback_active.is_set()
        assert not service.acknowledge_demo(payload["playback_id"], "other", True)
        assert not service.acknowledge_demo("old-id", "operator", True)
        assert service.acknowledge_demo(payload["playback_id"], "operator", True)
        assert not service.acknowledge_demo(payload["playback_id"], "operator", True)
    service.set_audio_emit(emit)
    job = DemoSpeech(DEMO_CLIPS["closing"], observed.append, "operator", threading.Event())
    service._run_demo(job)
    assert observed == [True]
    assert not service.acknowledge_demo(job.playback_id, "operator", True)


def test_live_auto_falls_back_to_browser_without_resynthesis(monkeypatch):
    service = live_service(monkeypatch, audio_output="auto")
    monkeypatch.setattr(service, "_play_wav", Mock(side_effect=RuntimeError("Speaker disconnected")))
    service.set_audio_emit(lambda event, data: service.acknowledge_demo(data["playback_id"], "operator", True))
    observed = []
    service._run_demo(DemoSpeech(DEMO_CLIPS["closing"], observed.append, "operator", threading.Event()))
    assert observed == [True] and service.active_output == "browser"
    assert service._demo_speech.synthesize.call_count == 1


@pytest.mark.parametrize("cancelled", [False, True])
def test_demo_playback_failure_or_cancel_reports_failure(monkeypatch, cancelled):
    service = live_service(monkeypatch, audio_output="pi")
    play = Mock(side_effect=RuntimeError("No speaker"))
    monkeypatch.setattr(service, "_play_wav", play)
    signal = threading.Event()
    if cancelled: signal.set()
    observed = []
    service._run_demo(DemoSpeech(DEMO_CLIPS["closing"], observed.append, "operator", signal))
    assert observed == [False]
    if cancelled:
        play.assert_not_called()
        service._demo_speech.synthesize.assert_not_called()


def test_browser_timeout_ends_live_demo_job(monkeypatch):
    from src.services import tts_service
    service = live_service(monkeypatch, audio_output="browser", audio_emit=lambda *args: None)
    clock = iter([0, 46])
    monkeypatch.setattr(tts_service.time, "monotonic", lambda: next(clock))
    observed = []
    service._run_demo(DemoSpeech(DEMO_CLIPS["closing"], observed.append, "operator", threading.Event()))
    assert observed == [False] and "timed out" in service.demo_error


def test_failed_live_generation_cancels_before_any_demo_movement():
    _, _, driver, _, runtime, demo, _, requested = demo_harness()
    runtime.tts.demo_error = "Groq demo speech HTTP 400: model terms required"
    runtime.tts.speak_demo.side_effect = lambda text, done, *args: (done(False), True)[1]
    demo._run()
    assert not requested and all(command == (0, 0, 0) for command in driver.commands)
    assert "terms required" in demo.snapshot()["reason"]


def test_missing_demo_configuration_rejects_start_without_motion():
    _, _, driver, arbiter, runtime, demo, _, _ = demo_harness()
    demo._state["active"] = False
    arbiter.set_mode(RobotMode.IDLE)
    runtime.tts.prepare_demo_speech.return_value = False
    runtime.tts.demo_error = "GROQ_API_KEY is not configured for speech"
    assert not demo.start("operator")
    assert arbiter.mode == RobotMode.IDLE
    assert all(command == (0, 0, 0) for command in driver.commands)
    assert "GROQ_API_KEY" in demo.snapshot()["reason"]


def test_demo_guards_audio_manual_and_listening():
    runtime = object.__new__(RobotRuntime)
    runtime.demo = SimpleNamespace(active=True)
    runtime.emit = Mock()
    assert not runtime.manual_drive(1, 0, 0, 1)
    assert not runtime.set_audio_output("browser")
    assert not runtime.start_listening()
    assert not runtime.browser_wake_detected()
    assert not runtime.set_mode("manual")


def test_browser_audio_is_delivered_only_to_demo_operator(monkeypatch):
    _, _, _, _, runtime, _, _, _ = demo_harness()
    runtime.set_event_sink = lambda callback: setattr(runtime, "event_sink", callback)
    runtime.dashboard_connected = Mock()
    runtime.health = lambda: {}
    runtime.telemetry = lambda: {}
    runtime.start_exhibition_demo = Mock(return_value=True)
    runtime.stop_exhibition_demo = Mock()
    app, socketio, _, _ = create_app(runtime)
    operator, visitor = socketio.test_client(app), socketio.test_client(app)
    operator.get_received(); visitor.get_received()
    operator.emit("perform_showcase", {})
    owner = runtime.start_exhibition_demo.call_args.args[0]
    runtime.event_sink("play_audio", {"data": "fake", "format": "wav", "playback_id": "123", "_recipient": owner})
    assert any(event["name"] == "play_audio" for event in operator.get_received())
    assert not visitor.get_received()
    operator.emit("stop_showcase", {})
    runtime.stop_exhibition_demo.assert_called_once()
    operator.disconnect(); visitor.disconnect()
