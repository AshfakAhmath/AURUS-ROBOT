"""Single-owner ultrasonic sampling service."""

from __future__ import annotations

import math
import threading
import time

from src.core.models import SensorSnapshot


class SensorSampler:
    def __init__(self, sensor, rate_hz: float = 7.0):
        self.sensor = sensor
        self.period = 1.0 / max(1.0, rate_hz)
        self._snapshot = SensorSnapshot()
        self._lock = threading.Lock()
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

    def sample_once(self) -> SensorSnapshot:
        try:
            values = self.sensor.read_all()
            distances = {key: float(values[key]) for key in ("fl", "f", "fr", "rl", "rr")}
            if not all(math.isfinite(value) and value >= 0.0 for value in distances.values()):
                raise ValueError("sensor returned a non-finite or negative distance")
            snapshot = SensorSnapshot(
                timestamp=time.monotonic(),
                fl=distances["fl"],
                f=distances["f"],
                fr=distances["fr"],
                rl=distances["rl"],
                rr=distances["rr"],
                healthy=True,
                error="",
            )
        except Exception as exc:
            previous = self.get_snapshot()
            snapshot = SensorSnapshot(
                timestamp=time.monotonic(),
                fl=previous.fl,
                f=previous.f,
                fr=previous.fr,
                rl=previous.rl,
                rr=previous.rr,
                healthy=False,
                error=str(exc)[:200],
            )
        with self._lock:
            self._snapshot = snapshot
        return snapshot

    def _run(self) -> None:
        while not self._stop.is_set():
            started = time.monotonic()
            self.sample_once()
            remaining = self.period - (time.monotonic() - started)
            self._stop.wait(max(0.0, remaining))
