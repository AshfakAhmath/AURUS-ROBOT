"""Immutable data contracts for the AURUS runtime."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
import time
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
class SensorSnapshot:
    timestamp: float = 0.0
    fl: float = 400.0
    f: float = 400.0
    fr: float = 400.0
    rl: float = 400.0
    rr: float = 400.0
    healthy: bool = False
    error: str = "not sampled"

    @property
    def age(self) -> float:
        if self.timestamp <= 0:
            return float("inf")
        return max(0.0, time.monotonic() - self.timestamp)

    def is_stale(self, max_age: float = 0.3) -> bool:
        return not self.healthy or self.age > max_age

    @property
    def front_min(self) -> float:
        return min(self.fl, self.f, self.fr)

    def navigation_front_min(self, center_enabled: bool = True) -> float:
        """Return the clearance used for motion safety.

        The physical rover can explicitly disable a failed center transducer,
        while simulation and healthy hardware continue to use all three front
        readings.  Keeping ``front_min`` truthful avoids silently weakening the
        global safety contract.
        """
        return self.front_min if center_enabled else min(self.fl, self.fr)

    @property
    def rear_min(self) -> float:
        return min(self.rl, self.rr)

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["age_ms"] = None if self.age == float("inf") else round(self.age * 1000, 1)
        data["front_min"] = self.front_min
        data["rear_min"] = self.rear_min
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
