"""Prepared FIT Expo audio and bounded demo tests; never play real audio."""

import json
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
from src.services.demo_script import DemoAudioCache, DEMO_CLIPS, DEMO_ORDER
from src.services.exhibition_demo import ExhibitionDemo
from src.services.tts_service import TTSService, CachedPlayback
from src.web.app import create_app
from tests.test_final_safety import build

ASSETS = Path(__file__).resolve().parents[1] / "assets" / "fit_expo"


class FastEvent(threading.Event):
    def wait(self, timeout=None):
        return self.is_set() if timeout == 0.75 else super().wait(timeout)


def demo_harness():
    sensor, sampler, driver, arbiter, runtime, _ = build()
    spoken = []
    def speak_cached(path, completed, owner, cancelled):
        spoken.append(Path(path).stem)
        completed(True)
        return True
    runtime.tts = SimpleNamespace(busy=False, speak_cached=Mock(side_effect=speak_cached))
    runtime.speech = SimpleNamespace(listening=False, browser_playback_active=threading.Event())
    runtime.mcp_agent = SimpleNamespace(busy=False)
    demo = ExhibitionDemo(runtime, ASSETS)
    runtime.demo = demo
    demo.audio.validate()
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


def test_recording_script_and_audio_are_cached_valid_and_unclipped():
    clips = DemoAudioCache(ASSETS).validate()
    manifest = json.loads((ASSETS / "manifest.json").read_text())
    assert manifest["engine"] == "Microsoft Edge online neural TTS"
    assert manifest["voice"] == "en-US-GuyNeural"
    assert manifest["mastering"]["target_lufs"] == -12
    assert "Please ask them" not in DEMO_CLIPS["closing"]
    assert "FIT Expo" in DEMO_CLIPS["closing"]
    assert "Faculty of Information Technology Expo" in DEMO_CLIPS["introduction"]
    assert "exhibition" not in " ".join(DEMO_CLIPS.values()).lower()
    assert "prototype" in DEMO_CLIPS["introduction"]
    for key, clip in clips.items():
        with wave.open(str(clip["path"]), "rb") as source:
            assert source.getframerate() == 48000
            pcm = np.frombuffer(source.readframes(source.getnframes()), dtype="<i2").astype(np.int32)
        assert 0 < np.abs(pcm).max() < 30000
        assert not np.any(np.abs(pcm) >= 32767)
        level = manifest["clips"][key]
        assert -13.5 <= level["integrated_lufs"] <= -10.5
        assert level["true_peak_dbtp"] <= -0.8
        measured_rms = 20 * np.log10(np.sqrt(np.mean((pcm / 32768) ** 2)))
        assert measured_rms == pytest.approx(level["rms_dbfs"], abs=0.02)
        assert measured_rms > -17
    with wave.open(str(ASSETS / "fit_expo_preview.wav"), "rb") as source:
        assert 55 < source.getnframes() / source.getframerate() < 70


def test_cloud_mastering_measurement_parser_handles_ffmpeg_summary(monkeypatch):
    from scripts import prepare_demo_audio as prepare
    measurements = {"input_i": "-12.0", "input_tp": "-2.0", "input_lra": "2.0", "input_thresh": "-22", "target_offset": "0.0"}
    monkeypatch.setattr(prepare.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(stderr="FFmpeg log\n" + json.dumps(measurements) + "\nOutput summary\n"))
    assert prepare.analyse("fake-ffmpeg", "file.wav")["input_i"] == "-12.0"
    measurements["input_i"] = "-inf"
    with pytest.raises(ValueError, match="silent or invalid"):
        prepare.analyse("fake-ffmpeg", "file.wav")


@pytest.mark.parametrize("sample", [0, 32767])
def test_cloud_mastering_rejects_silent_or_clipped_pcm(tmp_path, sample):
    from scripts import prepare_demo_audio as prepare
    path = tmp_path / "bad.wav"
    prepare.write_wav(path, 48000, np.full(100, sample, dtype="<i2").tobytes())
    with pytest.raises(ValueError, match="empty, silent or clipped"):
        prepare.pcm(path)


def test_cache_rejects_missing_or_outdated_text(tmp_path):
    with pytest.raises(FileNotFoundError): DemoAudioCache(tmp_path).validate()
    manifest = json.loads((ASSETS / "manifest.json").read_text())
    manifest["clips"]["introduction"]["text"] = "Old exhibition text"
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="stale"):
        DemoAudioCache(tmp_path).validate()


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
    assert runtime.tts.speak_cached.call_count == 6


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
    def speak(path, completed, owner, cancelled):
        spoken.append(Path(path).stem)
        if kind == "stop": demo.cancel()
        if kind == "estop": arbiter.emergency_stop()
        if kind == "disconnect":
            demo._owner_sid = "operator"
            demo.owner_disconnected("operator")
        completed(kind != "audio")
        return True
    runtime.tts.speak_cached.side_effect = speak
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


def test_cached_local_replay_never_synthesizes(monkeypatch):
    service = TTSService(audio_output="pi")
    monkeypatch.setattr(service, "_play_wav", Mock())
    monkeypatch.setattr(service, "_speak", Mock(side_effect=AssertionError("Must use cache")))
    observed = []
    job = CachedPlayback(ASSETS / "closing.wav", observed.append, "operator", threading.Event())
    service._run_cached(job)
    assert observed == [True]
    service._play_wav.assert_called_once()
    service._speak.assert_not_called()
    assert not service.playback_active.is_set()


def test_cached_browser_ack_is_scoped_and_waits_for_playback(monkeypatch):
    service = TTSService(audio_output="browser")
    observed = []
    def emit(event, payload):
        assert event == "play_audio" and payload["_recipient"] == "operator"
        assert service.playback_active.is_set()
        assert not service.acknowledge_cached(payload["playback_id"], "other", True)
        assert not service.acknowledge_cached("old-id", "operator", True)
        assert service.acknowledge_cached(payload["playback_id"], "operator", True)
        assert not service.acknowledge_cached(payload["playback_id"], "operator", True)
    service.set_audio_emit(emit)
    job = CachedPlayback(ASSETS / "closing.wav", observed.append, "operator", threading.Event())
    service._run_cached(job)
    assert observed == [True]
    assert not service.acknowledge_cached(job.playback_id, "operator", True)


def test_cached_auto_falls_back_to_browser_without_synthesis(monkeypatch):
    service = TTSService(audio_output="auto")
    monkeypatch.setattr(service, "_play_wav", Mock(side_effect=RuntimeError("Speaker disconnected")))
    service.set_audio_emit(lambda event, data: service.acknowledge_cached(data["playback_id"], "operator", True))
    observed = []
    service._run_cached(CachedPlayback(ASSETS / "closing.wav", observed.append, "operator", threading.Event()))
    assert observed == [True] and service.active_output == "browser"


@pytest.mark.parametrize("cancelled", [False, True])
def test_cached_failure_or_cancel_reports_failure_without_moving(monkeypatch, cancelled):
    service = TTSService(audio_output="pi")
    play = Mock(side_effect=RuntimeError("No speaker"))
    monkeypatch.setattr(service, "_play_wav", play)
    signal = threading.Event()
    if cancelled: signal.set()
    observed = []
    service._run_cached(CachedPlayback(ASSETS / "closing.wav", observed.append, "operator", signal))
    assert observed == [False]
    if cancelled: play.assert_not_called()


def test_browser_timeout_ends_cached_job(monkeypatch):
    from src.services import tts_service
    service = TTSService(audio_output="browser", audio_emit=lambda *args: None)
    clock = iter([0, 46])
    monkeypatch.setattr(tts_service.time, "monotonic", lambda: next(clock))
    observed = []
    service._run_cached(CachedPlayback(ASSETS / "closing.wav", observed.append, "operator", threading.Event()))
    assert observed == [False] and "timed out" in service.error


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
