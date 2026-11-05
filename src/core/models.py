"""Immutable data contracts for the AURUS runtime."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
import time
import math
from typing import Any


class RobotMode(str, Enum):
    IDLE = "idle"
    MANUAL = "manual"
    LISTENING = "listening"
    EXPLORING = "exploring"
    PERFORMING = "performing"
    ESTOP = "estop"
    DEGRADED = "degraded"


@dataclass(frozen=True)
class SensorReading:
    enabled: bool = True
    distance_cm: float | None = None
    sampled_at: float = 0.0
    error: str = "awaiting fresh reading"
    failures: int = 0
    inconsistent_readings: int = 0

    @property
    def age(self) -> float:
        return max(0.0, time.monotonic() - self.sampled_at) if self.sampled_at else float("inf")

    def status(self, max_age=0.3) -> str:
        if not self.enabled:
            return "Disabled"
        if self.error and self.failures:
            return "Unhealthy"
        if self.distance_cm is None:
            return "Initializing"
        return "Stale" if self.age > max_age else "Healthy"

    def as_dict(self) -> dict:
        status = self.status()
        return {
            "enabled": self.enabled, "status": status,
            "distance_cm": self.distance_cm if status == "Healthy" else None,
            "age_ms": round(self.age * 1000, 1) if math.isfinite(self.age) else None,
            "error": "reading stale" if status == "Stale" else self.error,
            "failures": self.failures, "inconsistent_readings": self.inconsistent_readings,
        }


@dataclass(frozen=True)
class SensorSnapshot:
    timestamp: float = 0.0
    fl: float = 400.0
    f: float = 400.0
    fr: float = 400.0
    rl: float = 400.0
    rr: float = 400.0
    healthy: bool = False
    error: str = "not sampled"
    states: dict[str, SensorReading] = field(default_factory=dict)

    def usable(self, key: str, max_age=0.3) -> bool:
        if self.states:
            return self.states[key].status(max_age) == "Healthy"
        value = getattr(self, key)
        return not self.is_stale(max_age) and math.isfinite(value) and value >= 0

    def center_enabled(self, default=True) -> bool:
        return self.states["f"].enabled if self.states else default

    def valid_distances(self, max_age=0.3) -> dict[str, float]:
        """Only current, enabled measurements; missing values stay absent."""
        return {key: getattr(self, key) for key in ("fl", "f", "fr", "rl", "rr")
                if self.usable(key, max_age) and math.isfinite(getattr(self, key)) and getattr(self, key) >= 0}

    def available_clearance(self, direction: str, max_age=0.3, center_default=True) -> float | None:
        keys = {"front": ("fl", "f", "fr") if self.center_enabled(center_default) else ("fl", "fr"),
                "rear": ("rl", "rr"), "left": ("fl", "rl"), "right": ("fr", "rr"),
                "rotation": ("fl", "f", "fr", "rl", "rr")}[direction]
        valid = self.valid_distances(max_age)
        values = [valid[key] for key in keys if key in valid]
        return min(values) if values else None

    def coverage(self, max_age=0.3, center_default=True) -> dict[str, bool]:
        front = ("fl", "f", "fr") if self.center_enabled(center_default) else ("fl", "fr")
        return {direction: all(self.usable(k, max_age) for k in keys) for direction, keys in {
            "front": front, "rear": ("rl", "rr"), "left": ("fl", "rl"),
            "right": ("fr", "rr"), "rotation": ("fl", "f", "fr", "rl", "rr"),
        }.items()}

    @property
    def age(self) -> float:
        if self.timestamp <= 0:
            return float("inf")
        return max(0.0, time.monotonic() - self.timestamp)

    def is_stale(self, max_age: float = 0.3) -> bool:
        return not self.healthy or self.age > max_age

    @property
    def front_min(self) -> float:
        return self._minimum(self.fl, self.f, self.fr)

    @staticmethod
    def _minimum(*values) -> float:
        return min(values) if all(math.isfinite(v) for v in values) else float("nan")

    def navigation_front_min(self, center_enabled: bool = True) -> float:
        """Return the clearance used for motion safety.

        The physical rover can explicitly disable a failed center transducer,
        while simulation and healthy hardware continue to use all three front
        readings.  Keeping ``front_min`` truthful avoids silently weakening the
        global safety contract.
        """
        return self.front_min if self.center_enabled(center_enabled) else self._minimum(self.fl, self.fr)

    @property
    def rear_min(self) -> float:
        return self._minimum(self.rl, self.rr)

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["age_ms"] = None if self.age == float("inf") else round(self.age * 1000, 1)
        data["front_min"] = self.front_min
        data["rear_min"] = self.rear_min
        data["states"] = {key: state.as_dict() for key, state in self.states.items()}
        for key in ("fl", "f", "fr", "rl", "rr"):
            value = getattr(self, key)
            data[key] = value if self.usable(key) else None
        for key in ("front_min", "rear_min"):
            keys = ("fl", "f", "fr") if key == "front_min" else ("rl", "rr")
            if not all(self.usable(sensor) for sensor in keys):
                data[key] = None
        data["coverage"] = self.coverage()
        data["reduced_coverage"] = not all(data["coverage"].values()) or not self.center_enabled()
        return data


@dataclass(frozen=True)
class MotionCommand:
    source: str
    vx: float
    vy: float
    omega: float
    priority: int = 10
    expires_at: float = field(default_factory=lambda: time.monotonic() + 0.3)

    @classmethod
    def stop(cls, source: str = "system", priority: int = 100) -> "MotionCommand":
        return cls(source=source, vx=0.0, vy=0.0, omega=0.0, priority=priority)

    @property
    def expired(self) -> bool:
        return time.monotonic() >= self.expires_at


@dataclass(frozen=True)
class MotionDecision:
    timestamp: float
    requested: MotionCommand
    final_vx: float
    final_vy: float
    final_omega: float
    allowed: bool
    reason: str
    obstacle_direction: str | None = None
    obstacle_distance_cm: float | None = None
    movement_cancelled: bool = False
    motors_stopped: bool = False  # Driver accepted zero output; no physical feedback sensor.

    def as_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "requested": {
                "source": self.requested.source,
                "vx": self.requested.vx,
                "vy": self.requested.vy,
                "omega": self.requested.omega,
                "priority": self.requested.priority,
            },
            "final": {"vx": self.final_vx, "vy": self.final_vy, "omega": self.final_omega},
            "allowed": self.allowed,
            "reason": self.reason,
            "obstacle_direction": self.obstacle_direction,
            "obstacle_distance_cm": self.obstacle_distance_cm,
            "movement_cancelled": self.movement_cancelled,
            "motors_stopped": self.motors_stopped,
        }


@dataclass(frozen=True)
class IdentityResult:
    status: str = "unknown"
    user_id: int | None = None
    name: str | None = None
    confidence: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EnrollmentStatus:
    active: bool = False
    name: str | None = None
    accepted: int = 0
    required: int = 20
    complete: bool = False
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class VisionSnapshot:
    timestamp: float = 0.0
    healthy: bool = False
    faces: tuple[dict[str, Any], ...] = ()
    primary_center_x: float | None = None
    primary_width: float | None = None
    identity: IdentityResult = field(default_factory=IdentityResult)
    backend: str = "unavailable"
    error: str = "not started"

    @property
    def age(self) -> float:
        if self.timestamp <= 0:
            return float("inf")
        return max(0.0, time.monotonic() - self.timestamp)

    def as_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "healthy": self.healthy,
            "face_count": len(self.faces),
            "faces": list(self.faces),
            "primary_center_x": self.primary_center_x,
            "primary_width": self.primary_width,
            "identity": self.identity.as_dict(),
            "backend": self.backend,
            "error": self.error,
            "age_ms": None if self.age == float("inf") else round(self.age * 1000, 1),
        }
