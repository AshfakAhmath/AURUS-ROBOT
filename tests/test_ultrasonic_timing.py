"""Synthetic echo waveforms and clock: no GPIO, real delays, or motor output."""

from types import SimpleNamespace

import pytest

from src import config
from src.core.models import RobotMode
from src.hardware import sensors as hardware
from src.services.sensor_sampler import SensorSampler
from tests.test_final_safety import build


PAIRS = (
    (config.SENSOR_FL_TRIG_PIN, config.SENSOR_FL_ECHO_PIN),
    (config.SENSOR_FRONT_TRIG_PIN, config.SENSOR_FRONT_ECHO_PIN),
    (config.SENSOR_FR_TRIG_PIN, config.SENSOR_FR_ECHO_PIN),
    (config.SENSOR_RL_TRIG_PIN, config.SENSOR_RL_ECHO_PIN),
    (config.SENSOR_RR_TRIG_PIN, config.SENSOR_RR_ECHO_PIN),
)


class Clock:
    def __init__(self):
        self.now = 1.0
        self.sleeps = []

    def monotonic(self):
        self.now += 0.000001
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class EchoGPIO:
    HIGH, LOW = 1, 0

    def __init__(self, clock):
        self.clock = clock
        self.triggered = {}
        self.pings = []
        self.behavior = {echo: 100.0 for _, echo in PAIRS}
        self.busy_until = {}

    def output(self, trig, value):
        if value == self.HIGH:
            echo = dict(PAIRS)[trig]
            self.triggered[echo] = self.clock.now
            self.pings.append((trig, self.clock.now))

    def input(self, echo):
        behavior = self.behavior[echo]
        if behavior == "high_before" or self.clock.now < self.busy_until.get(echo, 0):
            return self.HIGH
        start = self.triggered.get(echo)
        if start is None or behavior == "missing":
            return self.LOW
        elapsed = self.clock.now - start
        if elapsed < 0.0002:
            return self.LOW
        if behavior == "high_after":
            return self.HIGH
        return int(elapsed < 0.0002 + behavior / 17150)


@pytest.fixture
def physical(monkeypatch):
    clock = Clock()
    gpio = EchoGPIO(clock)
    monkeypatch.setattr(hardware, "GPIO", gpio)
    monkeypatch.setattr(hardware, "time", SimpleNamespace(monotonic=clock.monotonic, sleep=clock.sleep))
    # Autouse simulation guard prevents any setup_hardware call. All subsequent
    # physical reads refer only to the fake GPIO object installed above.
    sensor = hardware.ProximitySensor()
    sensor.is_simulation = False
    return sensor, clock, gpio


@pytest.mark.parametrize("distance", [10, 18, 100, 231, 350, 390])
def test_valid_new_echo_including_beyond_old_20ms_limit(physical, distance):
    sensor, _, gpio = physical
    trig, echo = PAIRS[0]
    gpio.behavior[echo] = distance
    assert sensor._read_physical_sensor(trig, echo) == pytest.approx(distance, abs=0.1)


def test_already_high_echo_never_gets_triggered_or_reported_clear(physical):
    sensor, clock, gpio = physical
    trig, echo = PAIRS[0]
    gpio.behavior[echo] = "high_before"
    started = clock.now
    with pytest.raises(TimeoutError, match="high before trigger"):
        sensor._read_physical_sensor(trig, echo)
    assert not gpio.pings
    assert clock.now - started < 0.006


def test_busy_old_pulse_must_end_before_new_obstacle_measurement(physical):
    sensor, clock, gpio = physical
    trig, echo = PAIRS[0]
    gpio.busy_until[echo] = clock.now + 0.002
    gpio.behavior[echo] = 18
    value = sensor._read_physical_sensor(trig, echo)
    assert value == pytest.approx(18, abs=0.1)
    assert gpio.pings[0][1] >= gpio.busy_until[echo]


@pytest.mark.parametrize("behavior,error", [("missing", "rise timeout"), ("high_after", "fall timeout")])
def test_timeouts_remain_invalid_not_fabricated_max_range(physical, behavior, error):
    sensor, clock, gpio = physical
    trig, echo = PAIRS[0]
    gpio.behavior[echo] = behavior
    started = clock.now
    with pytest.raises(TimeoutError, match=error): sensor._read_physical_sensor(trig, echo)
    assert clock.now - started < 0.055


def test_out_of_range_completed_pulse_is_rejected(physical):
    sensor, _, gpio = physical
    trig, echo = PAIRS[0]
    gpio.behavior[echo] = 500
    with pytest.raises(ValueError, match="outside valid"):
        sensor._read_physical_sensor(trig, echo)


def test_single_ping_per_sample_and_explicit_median_respects_cycle(physical):
    sensor, _, gpio = physical
    trig, echo = PAIRS[0]
    assert sensor._read_physical_filtered(trig, echo) == pytest.approx(100, abs=0.1)
    assert len(gpio.pings) == 1
    sensor._read_physical_filtered(trig, echo, samples=3)
    assert len(gpio.pings) == 4
    assert all(b[1] - a[1] >= 0.065 for a, b in zip(gpio.pings, gpio.pings[1:]))


def test_five_sensor_round_is_staggered_and_within_existing_freshness(physical):
    sensor, clock, gpio = physical
    # 390 cm would fail the old 20 ms limit, but must fit a complete new round.
    gpio.behavior = {echo: 390 for _, echo in PAIRS}
    started = clock.now
    values = sensor.read_all()
    assert len(values) == 5 and len(gpio.pings) == 5
    assert clock.now - started < 0.3
    assert all(b[1] - a[1] >= 0.04 for a, b in zip(gpio.pings, gpio.pings[1:]))
    sensor.read_front_left()
    assert gpio.pings[-1][1] - gpio.pings[0][1] >= 0.065


def test_stuck_front_inputs_do_not_stall_other_sensors_and_recover_fresh(physical):
    sensor, _, gpio = physical
    for _, echo in PAIRS[:2]: gpio.behavior[echo] = "high_before"
    sampler = SensorSampler(sensor)
    snapshot = sampler.sample_once().as_dict()
    assert snapshot["fl"] is None and snapshot["f"] is None
    assert snapshot["states"]["fl"]["status"] == "Unhealthy"
    assert all(snapshot[key] is not None for key in ("fr", "rl", "rr"))
    for _, echo in PAIRS[:2]: gpio.behavior[echo] = 120
    restored = sampler.sample_once().as_dict()
    assert all(state["status"] == "Healthy" for state in restored["states"].values())
    assert restored["fl"] == pytest.approx(120, abs=0.1)


def test_front_faults_only_restrict_required_directions_without_auto_resume():
    sensor, sampler, _, arbiter, _, _ = build()
    sensor.values["fl"] = sensor.values["f"] = TimeoutError("echo high before trigger")
    sampler.sample_once()
    for vx, vy, omega, permitted in [(1, 0, 0, False), (0, -1, 0, False), (0, 0, 1, False), (-1, 0, 0, True), (0, 1, 0, True)]:
        arbiter.set_mode(RobotMode.MANUAL)
        arbiter.command("test", vx, vy, omega)
        assert arbiter.tick().allowed is permitted
    arbiter.set_mode(RobotMode.MANUAL)
    arbiter.command("test", 1, 0, 0)
    assert not arbiter.tick().allowed
    sensor.values["fl"] = sensor.values["f"] = 100
    sampler.sample_once()
    assert not arbiter.command("test", 1, 0, 0)
    arbiter.set_mode(RobotMode.MANUAL)
    arbiter.command("test", 1, 0, 0)
    assert arbiter.tick().allowed
