"""Exclusive motor owner with deadman and proximity safety enforcement."""

from __future__ import annotations

import math
import threading
import time

from src.core.models import MotionCommand, MotionDecision, RobotMode


class MotionArbiter:
    def __init__(
        self,
        driver,
        sensor_sampler,
        rate_hz: float = 20.0,
        hard_stop_cm: float = 20.0,
        caution_cm: float = 35.0,
        stale_after: float = 0.3,
        front_sensor_enabled: bool = True,
        degraded_hard_stop_cm: float = 45.0,
        degraded_caution_cm: float = 75.0,
    ):
        self.driver = driver
        self.sensor_sampler = sensor_sampler
        self.period = 1.0 / max(5.0, rate_hz)
        self.hard_stop_cm = hard_stop_cm
        self.caution_cm = max(caution_cm, hard_stop_cm)
        self.stale_after = stale_after
        self.front_sensor_enabled = bool(front_sensor_enabled)
        self.degraded_hard_stop_cm = max(degraded_hard_stop_cm, hard_stop_cm)
        self.degraded_caution_cm = max(degraded_caution_cm, self.degraded_hard_stop_cm)
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._command = MotionCommand.stop("startup")
        self._decision = MotionDecision(time.time(), self._command, 0.0, 0.0, 0.0, False, "startup")
        self._mode = RobotMode.IDLE
        self._estop = False
        self._dashboard_connected = False

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="motion-arbiter", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self.driver.stop()

    @property
    def mode(self) -> RobotMode:
        with self._lock:
            return self._mode

    @property
    def estopped(self) -> bool:
        with self._lock:
            return self._estop

    def set_dashboard_connected(self, connected: bool) -> None:
        with self._lock:
            self._dashboard_connected = connected
            if not connected:
                self._command = MotionCommand.stop("dashboard-disconnected")
        if not connected:
            self.driver.stop()

    def set_mode(self, mode: RobotMode) -> bool:
        with self._lock:
            if self._estop and mode != RobotMode.ESTOP:
                return False
            self._mode = mode
            self._command = MotionCommand.stop("mode-change")
        self.driver.stop()
        return True

    def emergency_stop(self, reason: str = "operator emergency stop") -> None:
        with self._lock:
            self._estop = True
            self._mode = RobotMode.ESTOP
            self._command = MotionCommand.stop("estop", priority=1000)
        self.driver.stop()

    def clear_estop(self) -> None:
        with self._lock:
            self._estop = False
            self._mode = RobotMode.IDLE
            self._command = MotionCommand.stop("estop-cleared")
        self.driver.stop()

    def submit(self, command: MotionCommand) -> bool:
        values = (command.vx, command.vy, command.omega)
        if not all(math.isfinite(value) for value in values):
            return False
        bounded = MotionCommand(
            source=command.source[:80],
            vx=max(-1.0, min(1.0, command.vx)),
            vy=max(-1.0, min(1.0, command.vy)),
            omega=max(-1.0, min(1.0, command.omega)),
            priority=command.priority,
            expires_at=command.expires_at,
        )
        with self._lock:
            if self._estop:
                return False
            if any((bounded.vx, bounded.vy, bounded.omega)) and not self._dashboard_connected:
                return False
            current_is_moving = any((self._command.vx, self._command.vy, self._command.omega))
            if current_is_moving and not self._command.expired and bounded.priority < self._command.priority:
                return False
            self._command = bounded
            return True

    def command(self, source: str, vx: float, vy: float, omega: float, ttl: float = 0.3, priority: int = 10) -> bool:
        return self.submit(
            MotionCommand(
                source=source,
                vx=vx,
                vy=vy,
                omega=omega,
                priority=priority,
                expires_at=time.monotonic() + max(0.05, ttl),
            )
        )

    def halt(self, source: str = "system") -> None:
        with self._lock:
            self._command = MotionCommand.stop(source, priority=999)
        self.driver.stop()

    def get_decision(self) -> MotionDecision:
        with self._lock:
            return self._decision

    def _evaluate(self, command: MotionCommand) -> MotionDecision:
        now = time.time()
        sensors = self.sensor_sampler.get_snapshot()
        with self._lock:
            estop = self._estop
            connected = self._dashboard_connected

        if estop:
            return MotionDecision(now, command, 0.0, 0.0, 0.0, False, "emergency stop latched")
        if not connected:
            return MotionDecision(now, command, 0.0, 0.0, 0.0, False, "dashboard disconnected")
        if command.expired:
            return MotionDecision(now, command, 0.0, 0.0, 0.0, False, "command expired (deadman stop)")
        if sensors.is_stale(self.stale_after):
            return MotionDecision(now, command, 0.0, 0.0, 0.0, False, "sensor data stale or unhealthy")
        distances = (sensors.fl, sensors.f, sensors.fr, sensors.rl, sensors.rr)
        if not all(math.isfinite(value) and value >= 0.0 for value in distances):
            return MotionDecision(now, command, 0.0, 0.0, 0.0, False, "sensor data invalid")

        vx, vy, omega = command.vx, command.vy, command.omega
        reason = "allowed"

        front_clearance = sensors.navigation_front_min(self.front_sensor_enabled)
        front_hard_stop = self.hard_stop_cm if self.front_sensor_enabled else self.degraded_hard_stop_cm
        front_caution = self.caution_cm if self.front_sensor_enabled else self.degraded_caution_cm

        if vx > 0 and front_clearance < front_hard_stop:
            return MotionDecision(now, command, 0.0, 0.0, 0.0, False, f"front obstacle {front_clearance:.1f} cm")
        if vx < 0 and sensors.rear_min < self.hard_stop_cm:
            return MotionDecision(now, command, 0.0, 0.0, 0.0, False, f"rear obstacle {sensors.rear_min:.1f} cm")
        if vy < 0 and min(sensors.fl, sensors.rl) < self.hard_stop_cm:
            return MotionDecision(now, command, 0.0, 0.0, 0.0, False, "left-side obstacle")
        if vy > 0 and min(sensors.fr, sensors.rr) < self.hard_stop_cm:
            return MotionDecision(now, command, 0.0, 0.0, 0.0, False, "right-side obstacle")

        if vx > 0 and front_clearance < front_caution:
            vx *= 0.35
            reason = f"forward speed limited at {front_clearance:.1f} cm"
        elif vx < 0 and sensors.rear_min < self.caution_cm:
            vx *= 0.35
            reason = f"reverse speed limited at {sensors.rear_min:.1f} cm"

        return MotionDecision(now, command, vx, vy, omega, True, reason)

    def tick(self) -> MotionDecision:
        with self._lock:
            command = self._command
        decision = self._evaluate(command)
        try:
            self.driver.drive(decision.final_vx, decision.final_vy, decision.final_omega)
        except Exception as exc:
            self.driver.stop()
            decision = MotionDecision(
                time.time(), command, 0.0, 0.0, 0.0, False, f"motor driver error: {str(exc)[:120]}"
            )
        with self._lock:
            self._decision = decision
        return decision

    def _run(self) -> None:
        while not self._stop.is_set():
            started = time.monotonic()
            self.tick()
            remaining = self.period - (time.monotonic() - started)
            self._stop.wait(max(0.0, remaining))
