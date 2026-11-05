"""Deterministic high-level rover behavior state machine."""

from __future__ import annotations

import threading
import time
import random
import base64
from collections import deque
from difflib import SequenceMatcher
from typing import Callable

from src.core.models import RobotMode


class BehaviorController:
    def __init__(
        self, arbiter, sensor_sampler, vision_service,
        event_callback: Callable[[str, dict], None], vision_analyzer,
        playback_active: threading.Event | None = None,
    ):
        self.arbiter = arbiter
        self.sensor_sampler = sensor_sampler
        self.vision_service = vision_service
        self.event_callback = event_callback
        self.vision_analyzer = vision_analyzer
        self.playback_active = playback_active or threading.Event()
        self._stop = threading.Event()
        self._next_observation_time = time.monotonic() + 5.0
        self._observation_lock = threading.Lock()
        self._scan_lock = threading.Lock()
        self._explore_turn_until = 0.0
        self._explore_scan_at = time.monotonic() + random.uniform(8.0, 12.0)
        self._explore_turn_direction = 1.0
        self._thread: threading.Thread | None = None
        self._showcase_thread: threading.Thread | None = None
        self.showcase_start = None
        self.demo_active = lambda: False
        self._lost_since: float | None = None
        self._last_greeted: dict[int, float] = {}
        self._mission_lock = threading.RLock()
        self._mission_active = False
        self._mission_started_at = 0.0
        self._mission_started_wall = 0.0
        self._mission_elapsed_seconds = 0
        self._mission_number = 0
        self._mission_observations = 0
        self._mission_obstacles = 0
        self._mission_people = 0
        self._mission_route_changes = 0
        self._discoveries: deque[dict] = deque(maxlen=8)
        self._last_visual_observation = ""
        self._obstacle_active = False
        self._last_obstacle_notice = 0.0
        self._previous_mode = RobotMode.IDLE

    def begin_exploration(self) -> dict:
        with self._mission_lock:
            if self._mission_active:
                return self.mission_snapshot()
            self._mission_active = True
            self._mission_number += 1
            self._mission_started_at = time.monotonic()
            self._mission_started_wall = time.time()
            self._mission_elapsed_seconds = 0
            self._mission_observations = 0
            self._mission_obstacles = 0
            self._mission_people = 0
            self._mission_route_changes = 0
            self._discoveries.clear()
            self._last_visual_observation = ""
            self._obstacle_active = False
            now = time.monotonic()
            self._next_observation_time = now + 4.0
            self._explore_scan_at = now + random.uniform(7.0, 10.0)
        snapshot = self.mission_snapshot()
        self.event_callback("mission_update", snapshot)
        return snapshot

    def finish_exploration(self, emit: bool = True) -> dict | None:
        with self._mission_lock:
            if not self._mission_active:
                return None
            elapsed = max(0, round(time.monotonic() - self._mission_started_at))
            self._mission_elapsed_seconds = elapsed
            summary = {
                "mission_id": self._mission_number,
                "elapsed_seconds": elapsed,
                "observations": self._mission_observations,
                "obstacles": self._mission_obstacles,
                "people": self._mission_people,
                "route_changes": self._mission_route_changes,
            }
            self._mission_active = False
            self._obstacle_active = False
        discovery_word = "discovery" if summary["observations"] == 1 else "discoveries"
        people_word = "person" if summary["people"] == 1 else "people"
        route_word = "time" if summary["route_changes"] == 1 else "times"
        summary["text"] = (
            f"Exploration ended. In {elapsed} seconds I recorded "
            f"{summary['observations']} {discovery_word}, encountered "
            f"{summary['people']} {people_word}, and safely changed route "
            f"{summary['route_changes']} {route_word}."
        )
        if emit:
            self.event_callback("mission_summary", summary)
        return summary

    def mission_snapshot(self) -> dict:
        with self._mission_lock:
            elapsed = (
                max(0, round(time.monotonic() - self._mission_started_at))
                if self._mission_active else self._mission_elapsed_seconds
            )
            return {
                "active": self._mission_active,
                "mission_id": self._mission_number,
                "started_at": self._mission_started_wall,
                "elapsed_seconds": elapsed,
                "observations": self._mission_observations,
                "obstacles": self._mission_obstacles,
                "people": self._mission_people,
                "route_changes": self._mission_route_changes,
                "discoveries": list(self._discoveries),
            }

    def _add_discovery(
        self,
        category: str,
        title: str,
        text: str,
        confidence: float = 1.0,
        jpeg: bytes | None = None,
        speak: bool = False,
        provider: str = "local",
    ) -> dict:
        clean = " ".join((text or "").split())[:240]
        discovery = {
            "id": f"{self._mission_number}-{time.time_ns()}",
            "timestamp": time.time(),
            "category": category[:24],
            "title": title[:60],
            "text": clean,
            "confidence": round(max(0.0, min(1.0, confidence)), 2),
            "image": (
                "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")
                if jpeg else ""
            ),
        }
        with self._mission_lock:
            active = self._mission_active
            if active:
                self._discoveries.appendleft(discovery)
                if category in ("person", "object", "route", "scene"):
                    self._mission_observations += 1
        if active:
            self.event_callback("discovery", discovery)
        if speak and clean:
            self.event_callback("behavior_notice", {"text": clean, "provider": provider})
        return discovery

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="behavior-controller", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self.arbiter.halt("behavior-shutdown")

    def start_showcase(self) -> bool:
        # Retain the existing entry point, but never run the old strafe routine.
        return bool(self.showcase_start and self.showcase_start())

    def _emit_greeting_if_needed(self, vision) -> None:
        if self.demo_active() or not vision.faces:
            return

        now = time.monotonic()
        identity = vision.identity

        if identity.status == "known" and identity.user_id is not None and identity.name:
            if now - self._last_greeted.get(identity.user_id, 0.0) < 60.0:
                return
            self._last_greeted[identity.user_id] = now
            with self._mission_lock:
                if self._mission_active:
                    self._mission_people += 1
            self._add_discovery(
                "person", "Known person", f"Recognized {identity.name}.", identity.confidence,
                self.vision_service.get_jpeg(), speak=False,
            )
            self.event_callback(
                "greeting",
                {"user_id": identity.user_id, "name": identity.name, "confidence": identity.confidence},
            )
        else:
            if now - self._last_greeted.get(-1, 0.0) < 60.0:
                return
            self._last_greeted[-1] = now
            with self._mission_lock:
                if self._mission_active:
                    self._mission_people += 1
            self._add_discovery(
                "person", "New person", "A person entered the camera view.", 0.7,
                self.vision_service.get_jpeg(), speak=False,
            )
            self.event_callback("greeting_unknown", {})

    def _explore(self) -> None:
        if self.arbiter.mode != RobotMode.EXPLORING:
            return
        sensors = self.sensor_sampler.get_snapshot()
        center_enabled = sensors.center_enabled(getattr(self.arbiter, "front_sensor_enabled", True))
        clearance = sensors.available_clearance("front", center_default=center_enabled)
        strict = getattr(self.arbiter, "require_full_coverage", True)
        caution_cm = (
            self.arbiter.caution_cm
            if center_enabled or not strict
            else getattr(self.arbiter, "degraded_caution_cm", 75.0)
        )
        now = time.monotonic()

        if self.playback_active.is_set():
            self.arbiter.command(
                "explore-speaking-pause", 0.0, 0.0, 0.0, ttl=0.2, priority=30,
            )
            return

        # Cancel the mission before any notice/TTS/cloud work; never reroute.
        coverage = sensors.coverage(center_default=center_enabled)
        if (strict and not coverage["front"]) or (clearance is not None and clearance < caution_cm):
            self.arbiter.halt("explore-obstacle-stop")
            self.arbiter.set_mode(RobotMode.IDLE)
            with self._mission_lock:
                if self._mission_active:
                    self._mission_obstacles += 1
            self._add_discovery(
                "route", "Exploration cancelled",
                "I stopped exploring because the path is blocked or sensor coverage is insufficient. A fresh command is required.",
                speak=True,
            )
            return

        if self._scan_lock.locked():
            return
        elif now >= self._explore_scan_at:
            self._explore_scan_at = now + random.uniform(14.0, 20.0)
            threading.Thread(target=self._curiosity_scan, name="curiosity-scan", daemon=True).start()
        else:
            speed = 0.12 if not center_enabled else 0.20
            self.arbiter.command("explore-forward", speed, 0.0, 0.0, ttl=0.2, priority=20)

        if now >= self._next_observation_time and not self._observation_lock.locked() and not self._scan_lock.locked():
            self._next_observation_time = now + random.uniform(14.0, 20.0)
            threading.Thread(target=self._stationary_observation, name="explore-observation", daemon=True).start()

    def _stationary_observation(self) -> None:
        if not self._scan_lock.acquire(blocking=False):
            return
        try:
            self.arbiter.command(
                "curiosity-observe", 0.0, 0.0, 0.0, ttl=0.3, priority=25,
            )
            self._observe_environment()
        finally:
            self._scan_lock.release()

    def _curiosity_scan(self) -> None:
        if not self._scan_lock.acquire(blocking=False):
            return
        try:
            # A bounded left-centre-right sweep creates an intentional visual
            # inspection moment. Every command still expires through the arbiter.
            for omega, duration in ((-0.24, 0.40), (0.24, 0.80), (-0.24, 0.40)):
                end = time.monotonic() + duration
                while time.monotonic() < end and not self._stop.is_set():
                    if (
                        self.arbiter.mode != RobotMode.EXPLORING
                        or self.arbiter.estopped
                        or self.playback_active.is_set()
                    ):
                        return
                    self.arbiter.command("curiosity-scan", 0.0, 0.0, omega, ttl=0.16, priority=25)
                    time.sleep(0.08)
                self.arbiter.command("curiosity-pause", 0.0, 0.0, 0.0, ttl=0.18, priority=25)
                time.sleep(0.12)
            self._observe_environment()
        finally:
            self._scan_lock.release()

    def _observe_environment(self) -> None:
        if not self._observation_lock.acquire(blocking=False):
            return
        try:
            vision = self.vision_service.get_snapshot()
            # Never ask the model to interpret the generated CAMERA OFFLINE
            # placeholder or a frame left over from an earlier capture.
            if not vision.healthy or vision.age > 1.0:
                return
            jpeg = self.vision_service.get_jpeg()
            if not jpeg:
                return
            if hasattr(self.vision_analyzer, "analyze_discovery"):
                result = self.vision_analyzer.analyze_discovery(jpeg, self._last_visual_observation)
                obs = " ".join(str(result.get("observation", "")).split())
                if not obs or not result.get("interesting"):
                    return
                similarity = SequenceMatcher(None, obs.lower(), self._last_visual_observation.lower()).ratio()
                if self._last_visual_observation and similarity >= 0.82:
                    return
                self._last_visual_observation = obs
                category = result.get("category", "scene")
                self._add_discovery(
                    category,
                    "Visual discovery",
                    obs,
                    float(result.get("confidence", 0.5)),
                    jpeg,
                    speak=True,
                    provider="groq-vision",
                )
            elif hasattr(self.vision_analyzer, "describe"):
                obs = self.vision_analyzer.describe(jpeg)
                if obs:
                    self._last_visual_observation = obs
                    self._add_discovery(
                        "scene", "Visual discovery", obs, 0.5, jpeg,
                        speak=True, provider="groq-vision",
                    )
        finally:
            self._observation_lock.release()

    def _run(self) -> None:
        while not self._stop.wait(0.1):
            try:
                vision = self.vision_service.get_snapshot()
                self._emit_greeting_if_needed(vision)
                mode = self.arbiter.mode
                if mode == RobotMode.EXPLORING and self._previous_mode != RobotMode.EXPLORING:
                    self.begin_exploration()
                elif mode != RobotMode.EXPLORING and self._previous_mode == RobotMode.EXPLORING:
                    self.finish_exploration()
                self._previous_mode = mode
                if mode == RobotMode.EXPLORING:
                    self._explore()
            except Exception as exc:
                self.arbiter.halt("behavior-error")
                self.event_callback("system_error", {"component": "behavior", "error": str(exc)[:200]})
