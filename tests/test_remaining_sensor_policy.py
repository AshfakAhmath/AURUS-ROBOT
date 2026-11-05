"""Operator-selected remaining-sensor policy, with real obstacle stops retained."""

from dataclasses import replace
import json
import time

import pytest

from src import config
from src.core.models import RobotMode
from tests.test_final_safety import build
from tests.test_exhibition_demo import demo_harness


def fault(sampler, sensor, key, kind):
    if kind == "disabled": sampler.set_enabled(key, False)
    elif kind == "timeout":
        sensor.values[key] = TimeoutError("missing echo")
        sampler.sample_once()
    else:
        with sampler._lock:
            sampler._states[key] = replace(sampler._states[key], sampled_at=time.monotonic() - 1)
            sampler._publish()


@pytest.mark.parametrize("kind", ["disabled", "timeout", "stale"])
@pytest.mark.parametrize("vector", [(1, 0, 0), (-1, 0, 0), (0, -1, 0), (0, 0, 1)])
def test_missing_sensors_do_not_block_when_other_readings_remain(kind, vector):
    sensor, sampler, _, arbiter, _, _ = build()
    arbiter.require_full_coverage = False
    fault(sampler, sensor, "fl", kind)
    arbiter.set_mode(RobotMode.MANUAL)
    arbiter.command("operator", *vector)
    assert arbiter.tick().allowed
    assert sampler.get_snapshot().as_dict()["fl"] is None


@pytest.mark.parametrize("direction,vector,key", [
    ("front", (1, 0, 0), "fr"), ("rear", (-1, 0, 0), "rr"),
    ("left", (0, -1, 0), "rl"), ("right", (0, 1, 0), "rr"),
    ("rear", (0, 0, 1), "rr"),
])
def test_obstacle_on_remaining_sensor_still_cancels(direction, vector, key):
    sensor, sampler, driver, arbiter, _, _ = build()
    arbiter.require_full_coverage = False
    sampler.set_enabled("fl", False)
    sensor.values[key] = 18
    sampler.sample_once()
    arbiter.set_mode(RobotMode.MANUAL)
    arbiter.command("operator", *vector)
    result = arbiter.tick()
    assert not result.allowed and result.movement_cancelled
    assert result.obstacle_distance_cm == 18 and result.obstacle_direction == direction
    assert driver.commands[-1] == (0, 0, 0)
    sensor.values[key] = 100
    sampler.sample_once()
    assert not arbiter.command("operator", *vector)


def test_screenshot_front_faults_use_fr_and_front_center_off_keeps_normal_threshold():
    sensor, sampler, _, arbiter, _, service = build()
    arbiter.require_full_coverage = False
    sensor.values["fl"] = sensor.values["f"] = TimeoutError("echo high before trigger")
    sensor.values["fr"] = 231
    sampler.sample_once()
    arbiter.set_mode(RobotMode.MANUAL)
    arbiter.command("operator", 1, 0, 0)
    assert arbiter.tick().allowed
    data = json.loads(service._dispatch_tool("read_sensors", {}))
    assert data["front_clear"] and data["front_min_cm"] == 231
    assert data["distances_cm"]["fl"] is None and data["distances_cm"]["f"] is None
    assert data["sensors"]["coverage_required"] is False
    sampler.set_enabled("f", False)
    sensor.values["fr"] = 40
    sampler.sample_once()
    arbiter.set_mode(RobotMode.MANUAL)
    arbiter.command("operator", 1, 0, 0)
    assert arbiter.tick().allowed  # 45 cm degraded restriction is strict-mode only.


def test_unobserved_front_is_not_fabricated_clear_but_rear_sensors_remain_active():
    _, sampler, _, arbiter, _, service = build()
    arbiter.require_full_coverage = False
    for key in ("fl", "f", "fr"): sampler.set_enabled(key, False)
    data = json.loads(service._dispatch_tool("read_sensors", {}))
    assert data["front_min_cm"] is None and data["front_clear"] is False
    arbiter.set_mode(RobotMode.MANUAL)
    arbiter.command("operator", 1, 0, 0)
    assert arbiter.tick().allowed
    assert "reduced coverage" in arbiter.get_decision().reason
    arbiter.emergency_stop()
    assert not arbiter.command("operator", 1, 0, 0)


def test_complete_sensor_loss_still_stops():
    _, sampler, _, arbiter, _, _ = build()
    arbiter.require_full_coverage = False
    for key in sampler.KEYS: sampler.set_enabled(key, False)
    arbiter.set_mode(RobotMode.MANUAL)
    arbiter.command("operator", 1, 0, 0)
    result = arbiter.tick()
    assert not result.allowed and "no usable" in result.reason


def test_strict_mode_is_still_available():
    _, sampler, _, arbiter, _, _ = build()
    sampler.set_enabled("fl", False)
    assert arbiter.require_full_coverage is True
    arbiter.set_mode(RobotMode.MANUAL)
    arbiter.command("operator", 1, 0, 0)
    assert not arbiter.tick().allowed


def test_demo_uses_remaining_sensors_and_can_turn_with_center_off():
    _, sampler, driver, arbiter, _, demo, spoken, requested = demo_harness()
    arbiter.require_full_coverage = False
    sampler.set_enabled("f", False)
    demo._run()
    assert len(requested) == 6 and "blocked" not in spoken
    assert any(omega for vx, vy, omega in driver.commands)
    assert any(vy for vx, vy, omega in driver.commands)


@pytest.mark.parametrize('source', ['mcp-agent', 'standalone-mcp'])
@pytest.mark.parametrize('vector', [(0.5, 0, 0), (-0.5, 0, 0), (0, -0.5, 0),
                                   (0, 0.5, 0), (0, 0, -0.5), (0, 0, 0.5)])
@pytest.mark.parametrize('distance', [19.9, 20, 22.5, 24.9, 25, 30])
def test_mcp_uses_20_cm_stop_and_25_cm_caution_for_all_directions(source, vector, distance):
    sensor, sampler, _, arbiter, _, _ = build()
    for key in sensor.values: sensor.values[key] = distance
    sampler.sample_once()
    arbiter.set_mode(RobotMode.PERFORMING)
    assert arbiter.command(source, *vector)
    decision = arbiter.tick()
    if distance < 20:
        assert not decision.allowed and decision.motors_stopped
    else:
        assert decision.allowed
        expected = tuple(v * (0.35 if distance < 25 else 1) for v in vector)
        assert (decision.final_vx, decision.final_vy, decision.final_omega) == pytest.approx(expected)


def test_mcp_center_disabled_does_not_restore_45_or_75_cm_margin():
    sensor, sampler, _, arbiter, _, service = build()
    sampler.set_enabled('f', False)
    for key in sensor.values: sensor.values[key] = 30
    sampler.sample_once()
    arbiter.set_mode(RobotMode.PERFORMING)
    arbiter.command('mcp-agent', .5, 0, 0)
    decision = arbiter.tick()
    assert decision.allowed and decision.final_vx == .5
    data = json.loads(service._dispatch_tool('read_sensors', {}))
    assert data['front_clear'] and data['rear_clear']
    assert data['hard_stop_cm'] == 20 and data['caution_cm'] == 25
    arbiter.set_mode(RobotMode.MANUAL)
    arbiter.command('manual', .5, 0, 0)
    assert not arbiter.tick().allowed  # Strict manual degraded-front policy stays intact.


def test_demo_with_all_sensors_off_does_not_change_manual_no_sensor_stop():
    _, sampler, driver, arbiter, _, demo, _, requested = demo_harness()
    for key in ('fl', 'f', 'fr', 'rl', 'rr'): sampler.set_enabled(key, False)
    demo._run()
    assert len(requested) == 6 and any(any(v) for v in driver.commands)
    arbiter.require_full_coverage = False
    arbiter.set_mode(RobotMode.MANUAL)
    arbiter.command('manual', .2, 0, 0)
    assert not arbiter.tick().allowed


def test_mcp_moves_with_partial_coverage_but_stops_for_actual_obstacle():
    sensor, sampler, _, arbiter, _, service = build()
    arbiter.require_full_coverage = False
    sampler.set_enabled("fl", False)
    result = json.loads(service._dispatch_tool("move_rover", {"direction": "forward", "duration": 0.1}))
    assert result["status"] == "ok"
    sensor.values["f"] = 18
    sampler.sample_once()
    result = json.loads(service._dispatch_tool("move_rover", {"direction": "forward", "duration": 0.1}))
    assert result["status"] == "blocked" and result["obstacle_distance_cm"] == 18


def test_config_defaults_to_operator_requested_policy(monkeypatch):
    import importlib
    with monkeypatch.context() as context:
        context.delenv("AURUS_REQUIRE_FULL_SENSOR_COVERAGE", raising=False)
        assert importlib.reload(config).REQUIRE_FULL_SENSOR_COVERAGE is False
        context.setenv("AURUS_REQUIRE_FULL_SENSOR_COVERAGE", "true")
        assert importlib.reload(config).REQUIRE_FULL_SENSOR_COVERAGE is True
    importlib.reload(config)


def test_disabling_sensor_during_movement_uses_remaining_measurements():
    sensor, sampler, _, arbiter, _, _ = build()
    arbiter.require_full_coverage = False
    arbiter.set_mode(RobotMode.MANUAL)
    arbiter.command("operator", 1, 0, 0)
    assert arbiter.tick().allowed
    sampler.set_enabled("fl", False)
    sampler.set_enabled("f", False)
    assert arbiter.tick().allowed
    sensor.values["fr"] = 18
    sampler.sample_once()
    assert arbiter.tick().movement_cancelled


def test_exploration_uses_remaining_front_reading():
    from src.services.behavior_controller import BehaviorController
    sensor, sampler, _, arbiter, _, _ = build()
    arbiter.require_full_coverage = False
    sampler.set_enabled("fl", False)
    sampler.set_enabled("f", False)
    arbiter.set_mode(RobotMode.EXPLORING)
    controller = BehaviorController(arbiter, sampler, None, lambda *args: None, None)
    controller._explore_scan_at = controller._next_observation_time = time.monotonic() + 100
    controller._explore()
    assert arbiter.tick().allowed
    assert arbiter.get_decision().final_vx > 0
    sensor.values["fr"] = 18
    sampler.sample_once()
    controller._explore()
    assert arbiter.mode == RobotMode.IDLE
