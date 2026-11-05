"""Exclusive motor owner with deadman and proximity safety enforcement."""

from __future__ import annotations

import math
import threading
import time
from dataclasses import replace

from src.core.models import MotionCommand, MotionDecision, RobotMode

MCP_HARD_STOP_CM = 20.0
MCP_CAUTION_CM = 25.0


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
        require_full_coverage: bool = True,
    ):
        self.driver = driver
        self.sensor_sampler = sensor_sampler
        self.period = 1.0 / max(5.0, rate_hz)
        self.hard_stop_cm = hard_stop_cm
        self.caution_cm = max(caution_cm, hard_stop_cm)
        self.stale_after = stale_after
        self.front_sensor_enabled = bool(front_sensor_enabled)
        self.require_full_coverage = bool(require_full_coverage)
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
        self._cancelled = False
        self._last_blocked: MotionDecision | None = None
        self._demo_sensor_bypass = False

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
                self._demo_sensor_bypass = False
        if not connected:
            self.driver.stop()

    def set_mode(self, mode: RobotMode) -> bool:
        with self._lock:
            if self._estop and mode != RobotMode.ESTOP:
                return False
            self._mode = mode
            self._command = MotionCommand.stop("mode-change")
            self._cancelled = False
            self._demo_sensor_bypass = False
        self.driver.stop()
        return True

    def emergency_stop(self, reason: str = "operator emergency stop") -> None:
        with self._lock:
            self._estop = True
            self._demo_sensor_bypass = False
            self._mode = RobotMode.ESTOP
            self._command = MotionCommand.stop("estop", priority=1000)
        self.driver.stop()

    def clear_estop(self) -> None:
        with self._lock:
            self._estop = False
            self._demo_sensor_bypass = False
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
            if bounded.source in ("explore-forward", "curiosity-scan") and self._mode != RobotMode.EXPLORING:
                return False
            if self._cancelled and any(values):
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

    def get_last_blocked(self) -> MotionDecision | None:
        """Retain cancellation evidence after idle ticks and mode changes."""
        with self._lock:
            return self._last_blocked

    def set_demo_sensor_bypass(self, enabled: bool) -> bool:
        """Scope the operator-requested proximity bypass to scripted demo motion."""
        with self._lock:
            self._demo_sensor_bypass = bool(
                enabled and self._mode == RobotMode.PERFORMING
                and not self._estop and self._dashboard_connected and not self._cancelled
            )
            return self._demo_sensor_bypass

    def _evaluate(self, command: MotionCommand) -> MotionDecision:
        now = time.time()
        with self._lock:
            estop = self._estop
            connected = self._dashboard_connected
            demo_bypass = (self._demo_sensor_bypass and self._mode == RobotMode.PERFORMING
                           and command.source == "fit-expo-demo"
                           and max(abs(command.vx), abs(command.vy), abs(command.omega)) <= 0.20)

        if estop:
            return MotionDecision(now, command, 0.0, 0.0, 0.0, False, "emergency stop latched")
        if not connected:
            return MotionDecision(now, command, 0.0, 0.0, 0.0, False, "dashboard disconnected")
        if command.expired:
            return MotionDecision(now, command, 0.0, 0.0, 0.0, False, "command expired (deadman stop)")
        if not any((command.vx, command.vy, command.omega)):
            return MotionDecision(now, command, 0.0, 0.0, 0.0, True, "stopped")
        if demo_bypass:
            # E-STOP, disconnect, command expiry and cancellation remain enforced.
            return MotionDecision(now, command, command.vx, command.vy, command.omega,
                                  True, "scripted demo: ultrasonic restrictions bypassed")
        sensors = self.sensor_sampler.get_snapshot()
        if self.require_full_coverage and not sensors.states and sensors.is_stale(self.stale_after):
            return MotionDecision(now, command, 0.0, 0.0, 0.0, False, "sensor data stale or unhealthy")
        distances = (sensors.fl, sensors.f, sensors.fr, sensors.rl, sensors.rr)
        if self.require_full_coverage and not sensors.states and not all(math.isfinite(value) and value >= 0.0 for value in distances):
            return MotionDecision(now, command, 0.0, 0.0, 0.0, False, "sensor data invalid")

        vx, vy, omega = command.vx, command.vy, command.omega
        reason = "allowed"
        valid = sensors.valid_distances(self.stale_after)
        if not valid:
            return MotionDecision(now, command, 0.0, 0.0, 0.0, False, "no usable ultrasonic sensor readings")

        coverage = sensors.coverage(self.stale_after, self.front_sensor_enabled)
        required = []
        if vx > 0:
            required.append("front")
        if vx < 0:
            required.append("rear")
        if vy < 0:
            required.append("left")
        if vy > 0:
            required.append("right")
        if omega:
            required.append("rotation")
        for direction in required:
            if not coverage[direction] and self.require_full_coverage:
                return MotionDecision(now, command, 0.0, 0.0, 0.0, False, f"{direction} sensor coverage insufficient (disabled, initializing, invalid or stale)")
        if any(not coverage[direction] for direction in required):
            reason = "allowed with reduced coverage; unavailable sensors ignored"

        # MCP exhibition commands use the operator's 20/25 cm margins. Other
        # modes retain their configured thresholds and degraded-front policy.
        mcp_motion = command.source in ("mcp-agent", "standalone-mcp")
        hard_stop = MCP_HARD_STOP_CM if mcp_motion else self.hard_stop_cm
        caution = MCP_CAUTION_CM if mcp_motion else self.caution_cm

        front_clearance = sensors.available_clearance("front", self.stale_after, self.front_sensor_enabled)
        rear_clearance = sensors.available_clearance("rear", self.stale_after)
        left_clearance = sensors.available_clearance("left", self.stale_after)
        right_clearance = sensors.available_clearance("right", self.stale_after)
        center_enabled = sensors.center_enabled(self.front_sensor_enabled)
        front_hard_stop = hard_stop if mcp_motion or center_enabled or not self.require_full_coverage else self.degraded_hard_stop_cm
        front_caution = caution if mcp_motion or center_enabled or not self.require_full_coverage else self.degraded_caution_cm

        if vx > 0 and front_clearance is not None and front_clearance < front_hard_stop:
            return MotionDecision(now, command, 0.0, 0.0, 0.0, False, f"front obstacle {front_clearance:.1f} cm", "front", front_clearance)
        if vx < 0 and rear_clearance is not None and rear_clearance < hard_stop:
            return MotionDecision(now, command, 0.0, 0.0, 0.0, False, f"rear obstacle {rear_clearance:.1f} cm", "rear", rear_clearance)
        if vy < 0 and left_clearance is not None and left_clearance < hard_stop:
            return MotionDecision(now, command, 0.0, 0.0, 0.0, False, "left-side obstacle", "left", left_clearance)
        if vy > 0 and right_clearance is not None and right_clearance < hard_stop:
            return MotionDecision(now, command, 0.0, 0.0, 0.0, False, "right-side obstacle", "right", right_clearance)
        if omega:
            key = min(valid.items(), key=lambda item: item[1])
            if key[1] < hard_stop:
                direction = {"fl": "left", "f": "front", "fr": "right", "rl": "rear", "rr": "rear"}[key[0]]
                return MotionDecision(now, command, 0.0, 0.0, 0.0, False, f"{direction} obstacle {key[1]:.1f} cm during rotation", direction, key[1])

        if vx > 0 and front_clearance is not None and front_clearance < front_caution:
            vx *= 0.35
            reason = f"forward speed limited at {front_clearance:.1f} cm"
        elif vx < 0 and rear_clearance is not None and rear_clearance < caution:
            vx *= 0.35
            reason = f"reverse speed limited at {rear_clearance:.1f} cm"

        if mcp_motion:
            side_clearance = left_clearance if vy < 0 else right_clearance
            if vy and side_clearance is not None and side_clearance < caution:
                vy *= 0.35
                reason = f"sideways speed limited at {side_clearance:.1f} cm"
            if omega and min(valid.values()) < caution:
                omega *= 0.35
                reason = f"rotation speed limited at {min(valid.values()):.1f} cm"

        return MotionDecision(now, command, vx, vy, omega, True, reason)

    def tick(self) -> MotionDecision:
        # Serialize evaluation/output with emergency stop and configuration changes.
        with self._lock:
            command = self._command
            if self._cancelled and not self._estop:
                # Retry failed zero output while keeping cancellation latched.
                if not self._decision.motors_stopped:
                    try:
                        self.driver.stop()
                        self._decision = replace(self._decision, motors_stopped=True)
                        self._last_blocked = self._decision
                    except Exception:
                        pass
                return self._decision
            try:
                decision = self._evaluate(command)
            except Exception as exc:
                decision = MotionDecision(
                    time.time(), command, 0.0, 0.0, 0.0, False,
                    f"safety evaluation failed: {str(exc)[:120]}",
                )
            stopped = False
            try:
                if not decision.allowed or not any((decision.final_vx, decision.final_vy, decision.final_omega)):
                    self.driver.stop()
                    stopped = True
                else:
                    self.driver.drive(decision.final_vx, decision.final_vy, decision.final_omega)
            except Exception as exc:
                try:
                    self.driver.stop()
                    stopped = True
                except Exception:
                    pass
                decision = MotionDecision(
                    time.time(), command, 0.0, 0.0, 0.0, False, f"motor driver error: {str(exc)[:120]}"
                )
            cancelled = not decision.allowed and any((command.vx, command.vy, command.omega))
            decision = replace(decision, movement_cancelled=bool(cancelled), motors_stopped=stopped)
            if cancelled:
                self._demo_sensor_bypass = False
                self._command = MotionCommand.stop("safety-cancelled")
                self._cancelled = True
                self._last_blocked = decision
                if not self._estop:
                    self._mode = RobotMode.IDLE
            self._decision = decision
            return decision

    def _run(self) -> None:
        while not self._stop.is_set():
            started = time.monotonic()
            self.tick()
            remaining = self.period - (time.monotonic() - started)
            self._stop.wait(max(0.0, remaining))
