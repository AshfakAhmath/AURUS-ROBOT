"""Cancellable FIT Expo narration and bounded, arbiter-owned greeting gestures."""

import threading
import time

from src.core.models import RobotMode, camera_session_current
from src.services.demo_script import DEMO_CLIPS


class ExhibitionDemo:
    def __init__(self, runtime):
        self.runtime = runtime
        self._lock = threading.RLock()
        self._cancelled = threading.Event()
        self._thread = None
        self._state = {"active": False, "stage": "Ready", "status": "idle", "motion_enabled": False,
                       "reason": "", "objects": [], "vision_status": "idle", "sensor_bypass": False}
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
            self._update(active=True, stage="Introduction", status="running", motion_enabled=True, reason="",
                         objects=[], vision_status="idle", sensor_bypass=False)
            self._thread = threading.Thread(target=self._run, name="fit-expo-demo", daemon=True)
            self._thread.start()
            return True

    def cancel(self, reason="Stopped by operator"):
        with self._lock:
            if not self.active:
                return
            self._cancelled.set()
            self._state.update(stage="Stopping", status="cancelled", reason=reason, motion_enabled=False,
                               sensor_bypass=False)
        # Stop output precedes dashboard/audio updates, and never clears E-STOP.
        self.runtime.arbiter.set_demo_sensor_bypass(False)
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
        stages = {"introduction": "Introduction", "movement": "Greeting movements",
                  "strafe_left": "Sliding left", "strafe_right": "Sliding right",
                  "turn_left": "Greeting turn", "turn_right": "Greeting turn",
                  "capabilities": "Capabilities", "closing": "Closing",
                  "blocked": "Continuing without movement", "camera": "Live camera check"}
        return self._say(DEMO_CLIPS[key], stages.get(key, "Live camera check"))

    def _say(self, text, stage, valid_when=None):
        if not self._alive():
            return False
        if valid_when is not None and not valid_when():
            self._update(objects=[], vision_status="changed")
            return True  # A changed camera invalidates only this line, not the demo.
        done = threading.Event()
        result = []
        def completed(success):
            result.append(success)
            done.set()
        self._update(stage=stage)
        self.runtime.emit("conversation", {"source": "demo", "transcript": "", "response": text, "provider": "groq-demo-tts"})
        options = {"valid_when": valid_when} if valid_when is not None else {}
        if not self.runtime.tts.speak_demo(text, completed, self._owner_sid, self._cancelled, **options):
            self.cancel("Demo audio could not be queued")
            return False
        deadline = time.monotonic() + 120.0
        while not done.wait(0.05):
            if not self._alive():
                return False
            if time.monotonic() >= deadline:
                self.cancel("Demo audio timed out")
                return False
        if not self._alive():
            return False
        if not result or not result[0]:
            if valid_when is not None and not valid_when():
                self._update(objects=[], vision_status="changed")
                return self._alive()
            self.cancel(self.runtime.tts.demo_error or "Demo audio unavailable; check Groq access and speaker playback")
            return False
        return self._alive()

    @staticmethod
    def _motion_scale(elapsed, duration):
        """Smoothstep up/down with a short plateau and zero speed at both ends."""
        ramp = duration * 0.4
        fraction = max(0.0, min(1.0, elapsed / ramp, (duration - elapsed) / ramp))
        return fraction * fraction * (3.0 - 2.0 * fraction)

    def _motion(self, vx, vy, omega, duration):
        if not self.snapshot()["motion_enabled"] or not self._alive():
            return False
        arbiter = self.runtime.arbiter
        previous_block = self._motion_block_baseline
        started = time.monotonic()
        deadline = started + duration
        # Start at zero before sending the first nonzero ramp sample.
        if not arbiter.command("fit-expo-demo", 0.0, 0.0, 0.0, ttl=0.15, priority=40):
            return False
        arbiter.tick()
        reason = "Movement rejected"
        while self._alive() and time.monotonic() < deadline:
            if arbiter.get_last_blocked() is not previous_block or arbiter.mode != RobotMode.PERFORMING:
                reason = "Movement cancelled by the safety arbiter"
                break
            scale = self._motion_scale(time.monotonic() - started, duration)
            if not arbiter.command("fit-expo-demo", vx * scale, vy * scale, omega * scale, ttl=0.15, priority=40):
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
            if (self._alive() and arbiter.get_last_blocked() is previous_block
                    and arbiter.mode == RobotMode.PERFORMING):
                arbiter.halt("demo-step")
                return True
            reason = reason or "Movement cancelled by the safety arbiter"
        arbiter.halt("demo-movement-blocked")
        self._update(motion_enabled=False, reason=reason)
        return False

    def _observe(self):
        """One fresh, stationary object scan; failures skip the scan and continue."""
        self.runtime.arbiter.halt("demo-camera-check")
        if not self._alive():
            return False
        vision = getattr(self.runtime, "vision", None)
        provider = getattr(self.runtime, "groq_vision", None)
        self._update(objects=[], vision_status="scanning", stage="Live camera check")
        snapshot = vision.get_snapshot() if vision is not None else None
        if snapshot is not None and not snapshot.enabled:
            self._update(vision_status="off")
            return self._speak("camera_off")
        if snapshot is None or provider is None or not provider.ready:
            self._update(vision_status="unavailable")
            return self._speak("camera_unavailable")
        if not self._speak("camera") or self._cancelled.wait(0.2):
            return False
        snapshot = vision.get_snapshot()
        frame = vision.get_fresh_jpeg()
        if not snapshot.enabled or not frame:
            self._update(vision_status="unavailable")
            return self._speak("camera_unavailable")
        try:
            objects = provider.detect_objects(frame)
        except Exception:
            objects = None
        if not self._alive():
            return False
        def valid_when():
            current = vision.get_snapshot()
            return camera_session_current(snapshot, current) and current.age <= 1.0
        if not valid_when():
            self._update(vision_status="changed")
            return self._speak("camera_changed")
        if objects is None:
            self._update(vision_status="error")
            return self._speak("camera_unavailable")
        self._update(objects=objects, vision_status="ok")
        names = objects[:3]
        if names:
            listing = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
            text = f"Right now, I can see {listing}."
            if len(objects) > 3:
                text = f"Right now, I can see {', '.join(names)}, and more."
        else:
            text = DEMO_CLIPS["camera_empty"]
        return self._say(text, "What I can see", valid_when=valid_when)

    def _run(self):
        try:
            if not self.runtime.arbiter.set_demo_sensor_bypass(True):
                self.cancel("Demo movement could not be armed")
                return
            self._update(sensor_bypass=True)
            if not self._speak("introduction") or not self._speak("movement"):
                return
            if self._motion(0.20, 0.0, 0.0, 0.60):
                if self._cancelled.wait(0.75): return
                self._motion(-0.20, 0.0, 0.0, 0.60)
            if not self._alive(): return
            if self.snapshot()["motion_enabled"]:
                for key, vy, omega in (("strafe_left", -0.20, 0.0), ("strafe_right", 0.20, 0.0),
                                       ("turn_left", 0.0, -0.20), ("turn_right", 0.0, 0.20)):
                    if self._cancelled.wait(0.75) or not self._speak(key): return
                    if not self._motion(0.0, vy, omega, 0.60): break
            if not self._alive(): return
            if not self.snapshot()["motion_enabled"] and not self._speak("blocked"):
                return
            if not self._speak("capabilities") or not self._observe() or not self._speak("closing"):
                return
            self._update(status="completed", stage="Complete")
        except Exception as exc:
            self.cancel(f"Demo failed: {str(exc)[:150]}")
        finally:
            try:
                self.runtime.arbiter.set_demo_sensor_bypass(False)
                self.runtime.arbiter.halt("demo-finished")
                if not self.runtime.arbiter.estopped and self.runtime.arbiter.mode == RobotMode.PERFORMING:
                    self.runtime.arbiter.set_mode(RobotMode.IDLE)
            finally:
                if self._cancelled.is_set():
                    self._update(active=False, motion_enabled=False, sensor_bypass=False, status="cancelled", stage="Stopped")
                else:
                    self._update(active=False, motion_enabled=False, sensor_bypass=False)
