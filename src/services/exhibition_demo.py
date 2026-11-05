"""Cancellable FIT Expo narration and bounded, arbiter-owned greeting gestures."""

import threading
import time

from src.core.models import RobotMode
from src.services.demo_script import DEMO_CLIPS


class ExhibitionDemo:
    def __init__(self, runtime):
        self.runtime = runtime
        self._lock = threading.RLock()
        self._cancelled = threading.Event()
        self._thread = None
        self._state = {"active": False, "stage": "Ready", "status": "idle", "motion_enabled": False, "reason": ""}
        self._owner_sid = None
        self._motion_block_baseline = None

    @property
    def active(self):
        with self._lock:
            return self._state["active"]

    def snapshot(self):
        with self._lock:
            return dict(self._state)

    def _update(self, **changes):
        with self._lock:
            self._state.update(changes)
            state = dict(self._state)
        self.runtime.emit("demo_status", state)

    def start(self, owner_sid=None):
        arbiter = self.runtime.arbiter
        with self._lock:
            decision = arbiter.get_decision()
            if (self.active or (self._thread and self._thread.is_alive()) or arbiter.estopped
                    or arbiter.mode not in (RobotMode.IDLE, RobotMode.MANUAL)
                    or any((decision.final_vx, decision.final_vy, decision.final_omega))
                    or self.runtime.tts.busy or self.runtime.speech.listening
                    or self.runtime.mcp_agent.busy or self.runtime.speech.browser_playback_active.is_set()):
                return False
            if not self.runtime.tts.prepare_demo_speech():
                self._update(status="error", reason=self.runtime.tts.demo_error)
                return False
            if not arbiter.set_mode(RobotMode.PERFORMING):
                return False
            self._cancelled = threading.Event()
            self._owner_sid = owner_sid
            self._motion_block_baseline = arbiter.get_last_blocked()
            self._update(active=True, stage="Introduction", status="running", motion_enabled=True, reason="")
            self._thread = threading.Thread(target=self._run, name="fit-expo-demo", daemon=True)
            self._thread.start()
            return True

    def cancel(self, reason="Stopped by operator"):
        with self._lock:
            if not self.active:
                return
            self._cancelled.set()
            self._state.update(stage="Stopping", status="cancelled", reason=reason, motion_enabled=False)
        # Stop output precedes dashboard/audio updates, and never clears E-STOP.
        self.runtime.arbiter.halt("demo-cancelled")
        self.runtime.emit("demo_stop_audio", {})
        self._update()

    def owner_disconnected(self, sid):
        if sid == self._owner_sid:
            self.cancel("Demo dashboard disconnected")

    def _alive(self):
        if self._cancelled.is_set():
            return False
        if self.runtime.arbiter.estopped:
            self.cancel("Emergency stop")
            return False
        return True

    def _speak(self, key):
        if not self._alive():
            return False
        text = DEMO_CLIPS[key]
        done = threading.Event()
        result = []
        def completed(success):
            result.append(success)
            done.set()
        self._update(stage={"introduction": "Introduction", "movement": "Greeting movements", "turn_left": "Greeting turn", "turn_right": "Greeting turn", "capabilities": "Capabilities", "closing": "Closing", "blocked": "Continuing without movement"}[key])
        self.runtime.emit("conversation", {"source": "demo", "transcript": "", "response": text, "provider": "groq-demo-tts"})
        if not self.runtime.tts.speak_demo(text, completed, self._owner_sid, self._cancelled):
            self.cancel("Demo audio could not be queued")
            return False
        deadline = time.monotonic() + 120.0
        while not done.wait(0.05):
            if not self._alive():
                return False
            if time.monotonic() >= deadline:
                self.cancel("Demo audio timed out")
                return False
        if not result or not result[0]:
            self.cancel(self.runtime.tts.demo_error or "Demo audio unavailable; check Groq access and speaker playback")
            return False
        return self._alive()

    def _coverage(self, vx, omega):
        arbiter = self.runtime.arbiter
        sensors = self.runtime.sensor_sampler.get_snapshot()
        direction = "rotation" if omega else "front" if vx > 0 else "rear"
        strict = getattr(arbiter, "require_full_coverage", True)
        if not sensors.valid_distances():
            return False, "no usable ultrasonic sensor readings"
        if strict and not sensors.coverage(center_default=arbiter.front_sensor_enabled)[direction]:
            return False, f"{direction} sensor coverage insufficient"
        distance = sensors.available_clearance(direction, center_default=arbiter.front_sensor_enabled)
        if omega:
            clearance = 60.0
        elif vx > 0:
            clearance = 60.0 if not strict or sensors.center_enabled(arbiter.front_sensor_enabled) else max(75.0, arbiter.degraded_caution_cm)
        else:
            clearance = 60.0
        clearance = max(clearance, arbiter.caution_cm, arbiter.hard_stop_cm)
        if distance is None and not strict:
            return True, "unavailable sensors ignored; direction unobserved"
        return distance >= clearance, "" if distance >= clearance else f"{direction} clearance below demo margin ({clearance:g} cm)"

    def _motion(self, vx, omega, duration):
        if not self.snapshot()["motion_enabled"] or not self._alive():
            return False
        arbiter = self.runtime.arbiter
        previous_block = self._motion_block_baseline
        deadline = time.monotonic() + duration
        reason = "Movement rejected"
        while self._alive() and time.monotonic() < deadline:
            allowed, reason = self._coverage(vx, omega)
            if not allowed:
                break
            if arbiter.get_last_blocked() is not previous_block or arbiter.mode != RobotMode.PERFORMING:
                reason = "Movement cancelled by the safety arbiter"
                break
            if not arbiter.command("fit-expo-demo", vx, 0.0, omega, ttl=0.15, priority=40):
                reason = "Movement command rejected"
                break
            decision = arbiter.tick()
            if not decision.allowed:
                reason = decision.reason
                break
            self._cancelled.wait(min(0.025, max(0, deadline - time.monotonic())))
        else:
            # Cancellation can arrive on the final control cycle; do not
            # mistake an elapsed pulse timer for successful movement.
            allowed, reason = self._coverage(vx, omega)
            if (self._alive() and allowed and arbiter.get_last_blocked() is previous_block
                    and arbiter.mode == RobotMode.PERFORMING):
                arbiter.halt("demo-step")
                return True
            reason = reason or "Movement cancelled by the safety arbiter"
        arbiter.halt("demo-movement-blocked")
        self._update(motion_enabled=False, reason=reason)
        return False

    def _run(self):
        try:
            if not self._speak("introduction") or not self._speak("movement"):
                return
            if self._motion(0.20, 0.0, 0.20):
                if self._cancelled.wait(0.75): return
                self._motion(-0.20, 0.0, 0.20)
            if not self._alive(): return
            if self.snapshot()["motion_enabled"]:
                # Check rotation before announcing a turn that cannot happen.
                allowed, reason = self._coverage(0, -0.20)
                if not allowed:
                    self.runtime.arbiter.halt("demo-turn-unavailable")
                    self._update(motion_enabled=False, reason=reason)
                else:
                    for key, omega in (("turn_left", -0.20), ("turn_right", 0.20)):
                        if self._cancelled.wait(0.75) or not self._speak(key): return
                        if not self._motion(0.0, omega, 0.15): break
            if not self._alive(): return
            if not self.snapshot()["motion_enabled"] and not self._speak("blocked"):
                return
            if not self._speak("capabilities") or not self._speak("closing"):
                return
            self._update(status="completed", stage="Complete")
        except Exception as exc:
            self.cancel(f"Demo failed: {str(exc)[:150]}")
        finally:
            try:
                self.runtime.arbiter.halt("demo-finished")
                if not self.runtime.arbiter.estopped and self.runtime.arbiter.mode == RobotMode.PERFORMING:
                    self.runtime.arbiter.set_mode(RobotMode.IDLE)
            finally:
                if self._cancelled.is_set():
                    self._update(active=False, motion_enabled=False, status="cancelled", stage="Stopped")
                else:
                    self._update(active=False, motion_enabled=False)
