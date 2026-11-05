"""Single-owner ultrasonic sampling service."""

from __future__ import annotations

import math
import threading
import time
from dataclasses import replace

from src.core.models import SensorReading, SensorSnapshot


class SensorSampler:
    KEYS = ("fl", "f", "fr", "rl", "rr")
    METHODS = ("read_front_left", "read_front", "read_front_right", "read_rear_left", "read_rear_right")

    def __init__(self, sensor, rate_hz: float = 7.0, front_sensor_enabled=True):
        self.sensor = sensor
        self.period = 1.0 / max(1.0, rate_hz)
        self._states = {key: SensorReading(enabled=(key != "f" or front_sensor_enabled)) for key in self.KEYS}
        self._versions = {key: 0 for key in self.KEYS}
        self._snapshot = SensorSnapshot(states=dict(self._states))
        self._lock = threading.Lock()
        self._sample_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="sensor-sampler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def get_snapshot(self) -> SensorSnapshot:
        with self._lock:
            return self._snapshot

    def _publish(self):
        enabled = [state for state in self._states.values() if state.enabled]
        self._snapshot = SensorSnapshot(
            timestamp=time.monotonic(),
            **{key: state.distance_cm if state.distance_cm is not None else float("nan") for key, state in self._states.items()},
            healthy=bool(enabled) and all(state.status() == "Healthy" for state in enabled),
            error="; ".join(f"{key}: {state.error}" for key, state in self._states.items() if state.enabled and state.error),
            states=dict(self._states),
        )
        return self._snapshot

    def set_enabled(self, key: str, enabled: bool):
        if key not in self.KEYS or type(enabled) is not bool:
            raise ValueError("Expected sensor fl/f/fr/rl/rr and a boolean enabled value")
        with self._lock:
            # Invalidates even an in-flight pre-configuration measurement.
            if self._states[key].enabled != enabled:
                self._versions[key] += 1
                self._states[key] = SensorReading(enabled=enabled, error="awaiting fresh reading" if enabled else "")
            return self._publish()

    def restore_defaults(self, front_sensor_enabled=True):
        with self._lock:
            for key in self.KEYS:
                self._versions[key] += 1
                enabled = key != "f" or front_sensor_enabled
                self._states[key] = SensorReading(enabled=enabled, error="awaiting fresh reading" if enabled else "")
            return self._publish()

    def sample_once(self) -> SensorSnapshot:
        with self._sample_lock:
            aggregate = None
            aggregate_error = None
            individual = all(callable(getattr(self.sensor, method, None)) for method in self.METHODS)
            if not individual:
                try:
                    aggregate = self.sensor.read_all()
                except Exception as exc:
                    aggregate_error = exc
            for key, method in zip(self.KEYS, self.METHODS):
                with self._lock:
                    previous = self._states[key]
                    version = self._versions[key]
                if not previous.enabled:
                    continue
                started = time.monotonic()
                inconsistent = previous.inconsistent_readings
                try:
                    if aggregate_error:
                        raise aggregate_error
                    value = float(getattr(self.sensor, method)() if individual else aggregate[key])
                    if not math.isfinite(value) or not 0 <= value <= 400:
                        raise ValueError("non-finite or out-of-range distance")
                    # Repeated large changes are diagnostic faults. Decreasing
                    # readings still reach the arbiter immediately for stopping.
                    if previous.distance_cm is not None and abs(value - previous.distance_cm) > 100:
                        inconsistent += 1
                    else:
                        inconsistent = 0
                    if inconsistent >= 3:
                        raise ValueError("repeated inconsistent readings")
                    state = SensorReading(distance_cm=value, sampled_at=started, error="", inconsistent_readings=inconsistent)
                except Exception as exc:
                    state = replace(previous, distance_cm=None, error=str(exc)[:160], failures=previous.failures + 1, inconsistent_readings=inconsistent)
                with self._lock:
                    if version == self._versions[key]:
                        self._states[key] = state
                        self._publish()
            with self._lock:
                return self._publish()

    def _run(self) -> None:
        while not self._stop.is_set():
            started = time.monotonic()
            self.sample_once()
            remaining = self.period - (time.monotonic() - started)
            self._stop.wait(max(0.0, remaining))
