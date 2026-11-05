"""Safety contracts using fake drivers, transducers, cloud replies and speech."""

import json
import threading
import time
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.core.models import RobotMode, SensorReading
from src.hardware.sensors import ProximitySensor
from src.runtime import RobotRuntime
from src.services.distance_motion import prepare_movement
from src.services.mcp_agent_service import MCPAgentService
from src.services.motion_arbiter import MotionArbiter
from src.services.sensor_sampler import SensorSampler
from src.web.app import create_app


class Sensors:
    def __init__(self):
        self.values = dict.fromkeys(("fl", "f", "fr", "rl", "rr"), 100.0)
        self.calls = []

    def read(self, key):
        self.calls.append(key)
        value = self.values[key]
        if isinstance(value, Exception):
            raise value
        return value

    def read_front_left(self): return self.read("fl")
    def read_front(self): return self.read("f")
    def read_front_right(self): return self.read("fr")
    def read_rear_left(self): return self.read("rl")
    def read_rear_right(self): return self.read("rr")


class Driver:
    is_simulation = True

    def __init__(self):
        self.commands = []

    def drive(self, *vector): self.commands.append(vector)
    def stop(self): self.commands.append((0, 0, 0))
    def get_simulation_state(self): return dict(x=0, y=0, theta=0)


def build():
    sensor = Sensors()
    sampler = SensorSampler(sensor)
    sampler.sample_once()
    driver = Driver()
    arbiter = MotionArbiter(driver, sampler)
    arbiter.set_dashboard_connected(True)
    runtime = SimpleNamespace(
        arbiter=arbiter, sensor_sampler=sampler, driver=driver,
        emit=Mock(), tts=SimpleNamespace(speak=Mock(return_value=True)),
    )
    service = MCPAgentService(runtime, api_key="")
    service._client = None
    return sensor, sampler, driver, arbiter, runtime, service


@pytest.mark.parametrize("key,direction,vector", [
    ("f", "front", (1, 0, 0)), ("rl", "rear", (-1, 0, 0)),
    ("fl", "left", (0, -1, 0)), ("fr", "right", (0, 1, 0)),
])
def test_block_is_structured_and_latched(key, direction, vector):
    sensor, sampler, driver, arbiter, _, _ = build()
    sensor.values[key] = 18.2
    sampler.sample_once()
    arbiter.set_mode(RobotMode.MANUAL)
    assert arbiter.command("manual", *vector)
    decision = arbiter.tick()
    assert not decision.allowed
    assert decision.obstacle_direction == direction
    assert decision.obstacle_distance_cm == 18.2
    assert decision.movement_cancelled and decision.motors_stopped
    assert driver.commands[-1] == (0, 0, 0)
    sensor.values[key] = 100
    sampler.sample_once()
    assert not arbiter.command("manual", *vector)
    assert not arbiter.tick().allowed
    arbiter.set_mode(RobotMode.MANUAL)  # Explicit fresh operator request.
    assert arbiter.command("manual", *vector)
    assert arbiter.tick().allowed


@pytest.mark.parametrize("key,blocked,allowed", [
    ("fl", (1, 0, 0), (-1, 0, 0)), ("fr", (0, 1, 0), (-1, 0, 0)),
    ("rl", (-1, 0, 0), (1, 0, 0)), ("rr", (0, 1, 0), (1, 0, 0)),
])
def test_disabled_sensor_invalidates_and_restricts_affected_direction(key, blocked, allowed):
    sensor, sampler, _, arbiter, _, _ = build()
    sampler.set_enabled(key, False)
    sensor.calls.clear()
    data = sampler.sample_once().as_dict()
    assert key not in sensor.calls
    assert data[key] is None
    assert data["states"][key]["status"] == "Disabled"
    arbiter.set_mode(RobotMode.MANUAL)
    arbiter.command("manual", *blocked)
    assert not arbiter.tick().allowed
    arbiter.set_mode(RobotMode.MANUAL)
    arbiter.command("manual", *allowed)
    assert arbiter.tick().allowed


def test_center_disabled_uses_existing_wider_margins():
    sensor, sampler, _, arbiter, _, _ = build()
    sampler.set_enabled("f", False)
    sensor.values["fl"] = 40
    sampler.sample_once()
    arbiter.set_mode(RobotMode.MANUAL)
    arbiter.command("manual", 1, 0, 0)
    assert arbiter.tick().obstacle_distance_cm == 40
    assert sampler.get_snapshot().as_dict()["reduced_coverage"]


def test_reenabled_requires_fresh_valid_data():
    _, sampler, _, arbiter, _, _ = build()
    sampler.set_enabled("fl", False)
    sampler.set_enabled("fl", True)
    state = sampler.get_snapshot().as_dict()["states"]["fl"]
    assert state["distance_cm"] is None and state["status"] == "Initializing"
    arbiter.set_mode(RobotMode.MANUAL)
    arbiter.command("manual", 1, 0, 0)
    assert not arbiter.tick().allowed
    sampler.sample_once()
    arbiter.set_mode(RobotMode.MANUAL)
    arbiter.command("manual", 1, 0, 0)
    assert arbiter.tick().allowed


def test_inflight_read_cannot_survive_disable_reenable():
    sensor, sampler, _, _, _, _ = build()
    entered, release = threading.Event(), threading.Event()
    def delayed():
        entered.set()
        assert release.wait(1)
        return 150
    sensor.read_front_left = delayed
    worker = threading.Thread(target=sampler.sample_once)
    worker.start()
    assert entered.wait(1)
    sampler.set_enabled("fl", False)
    sampler.set_enabled("fl", True)
    release.set()
    worker.join(1)
    assert not worker.is_alive()
    assert sampler.get_snapshot().as_dict()["states"]["fl"]["status"] == "Initializing"


@pytest.mark.parametrize("bad", [TimeoutError("missing echo"), float("nan"), -1, 401, None])
def test_health_fault_never_becomes_clear_distance(bad):
    sensor, sampler, _, arbiter, _, service = build()
    sensor.values["fl"] = bad
    sampler.sample_once()
    sampler.sample_once()
    data = sampler.get_snapshot().as_dict()
    assert data["states"]["fl"]["status"] == "Unhealthy"
    assert data["states"]["fl"]["failures"] == 2
    assert data["fl"] is None
    assert data["states"]["fl"]["enabled"]
    result = json.loads(service._dispatch_tool("read_sensors", {}))
    assert result["distances_cm"]["fl"] is None
    assert not result["front_clear"]
    json.dumps(data, allow_nan=False)
    arbiter.set_mode(RobotMode.MANUAL)
    arbiter.command("manual", 1, 0, 0)
    decision = arbiter.tick()
    assert not decision.allowed
    assert decision.obstacle_distance_cm is None


def test_stale_and_inconsistent_health():
    sensor, sampler, _, _, _, _ = build()
    sensor.values["f"] = 250
    sampler.sample_once()
    sensor.values["f"] = 50
    sampler.sample_once()
    sensor.values["f"] = 250
    sampler.sample_once()
    data = sampler.get_snapshot().as_dict()
    assert "inconsistent" in data["states"]["f"]["error"]
    stale = SensorReading(distance_cm=100, sampled_at=time.monotonic() - 1, error="")
    assert stale.as_dict()["status"] == "Stale"
    assert stale.as_dict()["distance_cm"] is None


def test_gpio_missing_echo_raises_instead_of_fabricating_400(monkeypatch):
    from src.hardware import sensors as module
    fake_gpio = SimpleNamespace(HIGH=1, LOW=0, output=Mock(), input=Mock(return_value=0))
    monkeypatch.setattr(module, "GPIO", fake_gpio)
    transducer = object.__new__(ProximitySensor)
    transducer._ping_lock = threading.Lock()
    transducer._last_ping_at = None
    transducer._last_sensor_ping = {}
    with pytest.raises(TimeoutError, match="missing echo"):
        transducer._read_physical_sensor(4, 7)


def test_configuration_reassesses_active_movement_immediately():
    _, sampler, driver, arbiter, runtime, _ = build()
    runtime._default_front_sensor_enabled = True
    runtime.sensor_telemetry = lambda: RobotRuntime.sensor_telemetry(runtime)
    arbiter.set_mode(RobotMode.MANUAL)
    arbiter.command("manual", 1, 0, 0)
    arbiter.tick()
    RobotRuntime.configure_sensor(runtime, "fl", False)
    assert driver.commands[-1] == (0, 0, 0)
    assert arbiter.get_decision().movement_cancelled
    assert runtime.emit.call_args.args[0] == "sensor_configuration"
    sampler.restore_defaults()
    assert all(s["status"] == "Initializing" for s in sampler.get_snapshot().as_dict()["states"].values())


def calibration(monkeypatch, rate=20):
    from src import config
    for direction in ("FORWARD", "BACKWARD", "LEFT", "RIGHT"):
        monkeypatch.setenv(f"AURUS_DISTANCE_{direction}_CM_S", str(rate))
    monkeypatch.setenv("AURUS_DISTANCE_MOTOR_SPEED", "1")
    monkeypatch.setenv("AURUS_DISTANCE_OUTPUT_SCALE", str(config.MOTOR_OUTPUT_SCALE))
    monkeypatch.setenv("AURUS_DISTANCE_VECTOR_MULTIPLIER", str(config.MOTOR_SPEED_MULTIPLIER))


@pytest.mark.parametrize("direction", ["forward", "backward", "left", "right"])
def test_distance_conversion_and_duration_compatibility(monkeypatch, direction):
    calibration(monkeypatch)
    assert prepare_movement(direction, distance_cm=50) == (1, 2.5, 50)
    assert prepare_movement(direction, duration=30) == (1, 10, None)


def test_calibration_missing_or_mismatched_rejects(monkeypatch):
    monkeypatch.delenv("AURUS_DISTANCE_FORWARD_CM_S", raising=False)
    with pytest.raises(ValueError, match="not calibrated"):
        prepare_movement("forward", distance_cm=50)
    calibration(monkeypatch)
    with pytest.raises(ValueError, match="does not match"):
        prepare_movement("forward", speed=0.5, distance_cm=50)
    monkeypatch.setenv("AURUS_DISTANCE_OUTPUT_SCALE", "0.1")
    with pytest.raises(ValueError, match="does not match"):
        prepare_movement("forward", distance_cm=50)


@pytest.mark.parametrize("distance", [0, -1, float("nan"), float("inf"), 201, 0.1])
def test_distance_limits_reject_without_truncation(monkeypatch, distance):
    calibration(monkeypatch)
    with pytest.raises(ValueError):
        prepare_movement("forward", distance_cm=distance)


def test_estimated_movement_success_and_obstacle_interrupt(monkeypatch):
    calibration(monkeypatch)
    sensor, sampler, driver, arbiter, _, service = build()
    result = json.loads(service._dispatch_tool("move_rover", {"direction": "forward", "distance_cm": 2}))
    assert result["status"] == "ok" and result["estimated_distance_cm"] == 2
    assert result["actual_distance_cm"] is None and result["motors_stopped"]
    assert driver.commands[-1] == (0, 0, 0)
    # Inject a real fake measurement after motion has started; tick is local.
    original_sleep = time.sleep
    def obstacle_after_start(seconds):
        original_sleep(min(seconds, 0.001))
        sensor.values["f"] = 18.2
        sampler.sample_once()
        arbiter.tick()
    monkeypatch.setattr("src.services.motion_execution.time.sleep", obstacle_after_start)
    result = json.loads(service._dispatch_tool("move_rover", {"direction": "forward", "distance_cm": 50}))
    assert result["status"] == "blocked"
    assert result["obstacle_direction"] == "front" and result["obstacle_distance_cm"] == 18.2
    assert result["movement_cancelled"] and result["motors_stopped"]
    assert result["estimated_distance_cm"] is None and result["actual_distance_cm"] is None


def test_speed_limited_distance_not_reported_as_completed_estimate(monkeypatch):
    calibration(monkeypatch)
    sensor, sampler, _, _, _, service = build()
    sensor.values["f"] = 30
    sampler.sample_once()
    result = json.loads(service._dispatch_tool("move_rover", {"direction": "forward", "distance_cm": 2}))
    assert result["status"] == "ok" and result["speed_limited"]
    assert result["estimated_distance_cm"] is None


def test_estop_and_unconfirmed_stop():
    _, _, driver, arbiter, _, service = build()
    arbiter.emergency_stop()
    assert not arbiter.command("manual", 1, 0, 0)
    result = json.loads(service._dispatch_tool("move_rover", {"direction": "forward"}))
    assert result["status"] == "blocked" and result["motors_stopped"] is False
    assert arbiter.estopped
    arbiter.clear_estop()
    arbiter.set_mode(RobotMode.MANUAL)
    driver.drive = Mock(side_effect=RuntimeError("drive failed"))
    driver.stop = Mock(side_effect=RuntimeError("stop failed"))
    arbiter.command("manual", 1, 0, 0)
    decision = arbiter.tick()
    assert not decision.allowed and decision.motors_stopped is False


def call(name, arguments, identifier):
    return SimpleNamespace(id=identifier, function=SimpleNamespace(name=name, arguments=json.dumps(arguments)))


def reply(content="", calls=None):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=calls))])


@pytest.mark.parametrize("cloud_failure", [False, True])
def test_groq_blocked_explanation_once_and_no_further_actions(cloud_failure):
    sensor, sampler, _, _, runtime, service = build()
    sensor.values["f"] = 18.2
    sampler.sample_once()
    outcomes = [reply(calls=[
        call("move_rover", {"direction": "forward"}, "1"),
        call("move_rover", {"direction": "backward"}, "2"),
        call("speak_text", {"text": "duplicate"}, "3"),
    ])]
    outcomes.append(RuntimeError("offline") if cloud_failure else reply("I stopped because an obstacle is approximately 18 centimeters ahead."))
    create = Mock(side_effect=outcomes)
    service._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    dispatch = Mock(wraps=service._dispatch_tool)
    service._dispatch_tool = dispatch
    result = service.execute_command("Move forward")
    assert result["status"] == "blocked"
    assert dispatch.call_count == 1
    assert runtime.tts.speak.call_count == 1
    assert "18" in runtime.tts.speak.call_args.args[0]
    assert create.call_args.kwargs["tool_choice"] == "none"
    messages = create.call_args.kwargs["messages"]
    block = json.loads(next(m["content"] for m in messages if isinstance(m, dict) and m.get("role") == "tool"))
    assert block["obstacle_direction"] == "front" and block["movement_cancelled"]


def test_web_authorization_validation_and_telemetry(monkeypatch):
    monkeypatch.setenv("AURUS_SENSOR_OPERATOR_TOKEN", "test-operator")
    _, sampler, _, _, runtime, _ = build()
    runtime.set_event_sink = Mock()
    runtime.dashboard_connected = Mock()
    runtime.health = lambda: {}
    runtime.telemetry = lambda: {"sensors": sampler.get_snapshot().as_dict()}
    runtime.configure_sensor = Mock()
    app, socketio, _, _ = create_app(runtime)
    client = socketio.test_client(app)
    assert any(e["name"] == "telemetry" and e["args"][0]["sensors"]["states"]["f"]["status"] == "Healthy" for e in client.get_received())
    client.emit("set_sensor_enabled", {"sensor": "f", "enabled": False})
    assert not runtime.configure_sensor.called
    assert any(e["name"] == "command_error" for e in client.get_received())
    client.emit("authorize_sensor_control", {"token": "wrong"})
    client.emit("set_sensor_enabled", {"sensor": "f", "enabled": False})
    assert not runtime.configure_sensor.called
    client.emit("authorize_sensor_control", {"token": "test-operator"})
    client.emit("set_sensor_enabled", {"sensor": "f", "enabled": False})
    runtime.configure_sensor.assert_called_once_with("f", False)
    client.emit("set_sensor_enabled", {"restore_defaults": True})
    runtime.configure_sensor.assert_called_with(restore=True)
    html = app.test_client().get("/").data.decode()
    assert "ULTRASONIC SENSOR CONTROL" in html
    assert html.count('role="switch"') == 5
    client.disconnect()
    other = socketio.test_client(app)
    runtime.configure_sensor.reset_mock()
    other.emit("set_sensor_enabled", {"sensor": "f", "enabled": False})
    assert not runtime.configure_sensor.called
    other.disconnect()


def test_sensor_setting_rejects_gpio_and_nonboolean():
    _, sampler, _, _, _, _ = build()
    with pytest.raises(ValueError): sampler.set_enabled("GPIO20", True)
    with pytest.raises(ValueError): sampler.set_enabled("f", "false")


def test_threaded_arbiter_cancels_active_mcp_burst():
    sensor, sampler, driver, arbiter, _, service = build()
    # Inject only after output actually starts. A wall-clock timer can fire
    # before movement or after initial data expires on a busy test machine.
    drive = driver.drive
    introduced = threading.Event()
    def drive_then_obstacle(*vector):
        drive(*vector)
        if vector[0] > 0 and not introduced.is_set():
            sensor.values["f"] = 17
            sampler.sample_once()
            introduced.set()
    driver.drive = drive_then_obstacle
    sampler.start()
    arbiter.start()
    try:
        result = json.loads(service._dispatch_tool("move_rover", {"direction": "forward", "duration": 1}))
        assert introduced.is_set()
        assert result["status"] == "blocked" and result["obstacle_distance_cm"] == 17
        assert arbiter.mode == RobotMode.IDLE
        assert driver.commands[-1] == (0, 0, 0)
    finally:
        arbiter.stop()
        sampler.stop()


def test_failed_stop_is_retried_and_never_reported_as_completed():
    from src.services.motion_execution import execute_sequence
    sensor, sampler, driver, arbiter, _, _ = build()
    sensor.values["f"] = 18
    sampler.sample_once()
    # set_mode succeeds first; subsequent stop writes fail.
    driver.stop = Mock(side_effect=[None, RuntimeError("stop"), RuntimeError("stop"), RuntimeError("stop")])
    completed, reason, feedback = execute_sequence(arbiter, [(1, 0, 0, 0.1)], "test")
    assert not completed and not feedback["motors_stopped"]
    assert feedback["movement_cancelled"]
    assert "stop output failed" in reason
    driver.stop = Mock()
    assert arbiter.tick().motors_stopped
    driver.stop.assert_called_once()


def test_no_operator_token_keeps_settings_locked(monkeypatch):
    monkeypatch.delenv("AURUS_SENSOR_OPERATOR_TOKEN", raising=False)
    _, _, _, _, runtime, _ = build()
    runtime.set_event_sink = Mock()
    runtime.dashboard_connected = Mock()
    runtime.health = lambda: {}
    runtime.telemetry = lambda: {}
    runtime.configure_sensor = Mock()
    app, socketio, _, _ = create_app(runtime)
    client = socketio.test_client(app)
    client.emit("authorize_sensor_control", {"token": ""})
    client.emit("set_sensor_enabled", {"sensor": "f", "enabled": False})
    assert not runtime.configure_sensor.called
    client.disconnect()


def test_browser_audio_gate_clears_on_disconnect():
    _, _, _, _, runtime, _ = build()
    runtime.set_event_sink = Mock()
    runtime.dashboard_connected = Mock()
    runtime.health = lambda: {}
    runtime.telemetry = lambda: {}
    runtime.speech = SimpleNamespace(browser_playback_active=threading.Event())
    app, socketio, _, _ = create_app(runtime)
    client = socketio.test_client(app)
    client.emit("browser_audio_state", {"playing": True})
    assert runtime.speech.browser_playback_active.is_set()
    client.emit("browser_audio_state", {"playing": False})
    assert not runtime.speech.browser_playback_active.is_set()
    client.emit("browser_audio_state", {"playing": True})
    client.disconnect()
    assert not runtime.speech.browser_playback_active.is_set()


def test_uncalibrated_groq_request_cannot_substitute_timed_motion(monkeypatch):
    monkeypatch.delenv("AURUS_DISTANCE_FORWARD_CM_S", raising=False)
    _, _, driver, _, runtime, service = build()
    create = Mock(side_effect=[
        reply(calls=[call("move_rover", {"direction": "forward", "distance_cm": 50}, "1"),
                     call("move_rover", {"direction": "forward", "duration": 2}, "2")]),
        reply("Distance movement is not calibrated yet. Please use a timed movement command."),
    ])
    service._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    result = service.execute_command("move forward 50 centimeters")
    assert result["status"] == "error"
    assert driver.commands == []
    assert runtime.tts.speak.call_count == 1


def test_tts_failure_cannot_change_blocked_safety_state():
    sensor, sampler, driver, arbiter, runtime, service = build()
    sensor.values["f"] = 18
    sampler.sample_once()
    runtime.tts.speak.side_effect = RuntimeError("speaker unavailable")
    service._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=Mock(side_effect=[
        reply(calls=[call("move_rover", {"direction": "forward"}, "1")]),
        reply("An obstacle is blocking the front."),
    ]))))
    assert service.execute_command("move forward")["status"] == "blocked"
    assert arbiter.mode == RobotMode.IDLE
    assert driver.commands[-1] == (0, 0, 0)
    assert runtime.tts.speak.call_count == 1


def test_individual_stale_reading_blocks_even_when_snapshot_was_recent():
    _, sampler, _, arbiter, _, _ = build()
    with sampler._lock:
        sampler._states["fl"] = replace(sampler._states["fl"], sampled_at=time.monotonic() - 1)
        sampler._publish()
    data = sampler.get_snapshot().as_dict()
    assert data["age_ms"] < 100
    assert data["front_min"] is None and not data["coverage"]["front"]
    arbiter.set_mode(RobotMode.MANUAL)
    arbiter.command("manual", 1, 0, 0)
    assert not arbiter.tick().allowed


def test_rotation_requires_full_coverage_and_checks_obstacles():
    sensor, sampler, _, arbiter, _, _ = build()
    sampler.set_enabled("f", False)
    arbiter.set_mode(RobotMode.MANUAL)
    arbiter.command("manual", 0, 0, 1)
    assert not arbiter.tick().allowed
    sampler.restore_defaults()
    sensor.values["rr"] = 17
    sampler.sample_once()
    arbiter.set_mode(RobotMode.MANUAL)
    arbiter.command("manual", 0, 0, 1)
    decision = arbiter.tick()
    assert not decision.allowed and decision.obstacle_direction == "rear"


def test_unexpected_sensor_exception_still_stops_and_cancels():
    _, sampler, driver, arbiter, _, _ = build()
    arbiter.set_mode(RobotMode.MANUAL)
    arbiter.command("manual", 1, 0, 0)
    arbiter.tick()
    sampler.get_snapshot = Mock(side_effect=RuntimeError("snapshot unavailable"))
    decision = arbiter.tick()
    assert decision.movement_cancelled and decision.motors_stopped
    assert driver.commands[-1] == (0, 0, 0)
    assert "safety evaluation failed" in decision.reason


def test_standalone_tools_share_safety_and_distance(monkeypatch):
    pytest.importorskip("mcp.server.fastmcp")
    from servers import aurus_mcp_server as server
    calibration(monkeypatch)
    sensor, sampler, driver, arbiter, _, _ = build()
    monkeypatch.setattr(server, "_init_hardware", lambda: None)
    monkeypatch.setattr(server, "_driver", driver)
    monkeypatch.setattr(server, "_sensor", SimpleNamespace(is_simulation=True))
    monkeypatch.setattr(server, "_sampler", sampler)
    monkeypatch.setattr(server, "_arbiter", arbiter)
    sensor.values["fl"] = 18.2
    sampler.sample_once()
    result = json.loads(server.move_rover("left", distance_cm=2))
    assert result["status"] == "blocked" and result["obstacle_direction"] == "left"
    assert result["movement_cancelled"] and result["motors_stopped"]
    assert result["estimated_distance_cm"] is None
    sampler.set_enabled("f", False)
    assert json.loads(server.read_sensors())["distances_cm"]["f"] is None
