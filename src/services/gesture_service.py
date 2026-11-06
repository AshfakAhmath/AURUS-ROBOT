"""Recognize one hand gesture per bounded camera scan."""

from __future__ import annotations

from math import hypot
from pathlib import Path
import logging
import multiprocessing
import signal
import threading
import time

from src.services.gesture_worker import serve_gestures


log = logging.getLogger(__name__)
POSES = {"Thumb_Up": "thumbs-up", "Victory": "peace"}
GESTURES = frozenset((*POSES.values(), "wave", "open-palm", "heart"))


def _distance(a, b):
    return hypot(a.x - b.x, a.y - b.y)


def _forms_heart(hands):
    if len(hands) != 2:
        return False
    left, right = sorted(hands, key=lambda points: points[0].x)
    # A two-hand heart joins index fingertips above the joined thumbs.
    palm = (_distance(left[0], left[9]) + _distance(right[0], right[9])) / 2
    return (
        palm >= 0.04
        and _distance(left[0], right[0]) > 1.4 * palm
        and _distance(left[8], right[8]) < 0.55 * palm
        and _distance(left[4], right[4]) < 0.55 * palm
        and all(hand[4].y - hand[8].y > 0.45 * palm for hand in (left, right))
    )


class _Scan:
    def __init__(self):
        self.pose = None
        self.pose_frames = 0
        self.palm_positions = []
        self.palm_started = 0.0
        self.still_palm = False

    def observe(self, result, timestamp):
        hands = result.hand_landmarks
        pose = "heart" if _forms_heart(hands) else None
        category = None
        if len(hands) == 1 and result.gestures and result.gestures[0]:
            top = result.gestures[0][0]
            if top.score >= 0.65:
                category = top.category_name
                pose = POSES.get(category)

        self.pose_frames = self.pose_frames + 1 if pose == self.pose else 1
        self.pose = pose
        if pose and self.pose_frames >= 2:
            return pose

        if category != "Open_Palm":
            self.palm_positions.clear()
            self.still_palm = False
            return None

        wrist, palm_center = hands[0][0], hands[0][9]
        palm_size = _distance(wrist, palm_center)
        if not self.palm_positions:
            self.palm_started = timestamp
        self.palm_positions.append(wrist.x)
        low, high = min(self.palm_positions), max(self.palm_positions)
        swing = max(0.05, 0.50 * palm_size)
        self.still_palm = (
            len(self.palm_positions) >= 3
            and timestamp - self.palm_started >= 0.4
            and high - low < swing
        )
        if high - low >= swing:
            middle = (high + low) / 2
            start, end = self.palm_positions[0], wrist.x
            if (start <= middle and end < high - swing * 0.8
                    or start >= middle and end > low + swing * 0.8):
                return "wave"
        # Wait out the scan before selecting open palm so a wave can finish.
        return None


class _ExpoScan(_Scan):
    def __init__(self, after):
        super().__init__()
        self.after = after
        self.empty_since = None
        self.released = False

    def observe(self, result, timestamp):
        gesture = super().observe(result, timestamp)
        if result.hand_landmarks:
            self.empty_since = None
        elif self.empty_since is None:
            self.empty_since = timestamp
        elif self.after and timestamp - self.empty_since >= 0.4:
            self.released = True

        if gesture is None and self.still_palm and timestamp - self.palm_started >= 0.9:
            gesture = "open-palm"
        # A wave often ends with the same hand held open.
        if gesture == self.after or (self.after == "wave" and gesture == "open-palm"):
            return None
        return gesture


class GestureService:
    def __init__(self, vision, model_path: Path):
        self.vision = vision
        self.model_path = model_path
        self._lock = threading.Lock()
        self._stopping = threading.Event()
        self._worker = None
        self._connection = None
        self._unavailable = None

    def start(self):
        with self._lock:
            self._unavailable = None
            self._stopping.clear()

    def stop(self):
        self._stopping.set()
        with self._lock:
            self._close_worker()

    def _start_worker(self):
        if self._worker is not None:
            return
        context = multiprocessing.get_context("spawn")
        parent, child = context.Pipe()
        worker = context.Process(
            target=serve_gestures, args=(child, self.model_path),
            name="aurus-gesture-worker", daemon=True,
        )
        try:
            worker.start()
        except Exception:
            parent.close()
            raise
        finally:
            child.close()
        self._worker, self._connection = worker, parent
        self._worker_reply(timeout=10.0)

    def _worker_reply(self, timeout):
        deadline = time.monotonic() + timeout
        while not self._stopping.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Gesture worker did not respond")
            if self._connection.poll(min(remaining, 0.1)):
                status, result = self._connection.recv()
                if status == "missing":
                    raise ImportError(result)
                if status == "error":
                    raise RuntimeError(result)
                return result
        raise RuntimeError("Gesture recognition is stopping")

    def _close_worker(self):
        worker, self._worker = self._worker, None
        if self._connection is not None:
            self._connection.close()
            self._connection = None
        if worker is None:
            return None
        worker.join(0.2)
        exitcode = worker.exitcode
        if exitcode is None:
            worker.terminate()
            worker.join(0.3)
        if worker.is_alive():
            worker.kill()
            worker.join()
        worker.close()
        return exitcode

    def _recognize(self, jpeg):
        self._connection.send_bytes(jpeg)
        return self._worker_reply(timeout=2.0)

    def scan(self, generation: int, *, automatic: bool = False, after: str | None = None) -> tuple[dict, int]:
        if not self._lock.acquire(blocking=False):
            return {"message": "A gesture scan is already running."}, 409
        try:
            if self._stopping.is_set():
                return {"message": "Gesture recognition is stopping."}, 503
            if self._unavailable:
                return {"message": self._unavailable}, 503
            return self._scan(generation, automatic, after)
        except ImportError as exc:
            self._close_worker()
            log.warning("Gesture dependency could not load: %s", exc)
            return {"message": "Gesture dependencies could not load. Check the Pi gesture setup and logs."}, 503
        except (OSError, EOFError) as exc:
            exitcode = self._close_worker()
            log.error("Gesture worker stopped (exit code %s): %s", exitcode, exc)
            if exitcode in (-signal.SIGILL, 128 + signal.SIGILL):
                self._unavailable = "The gesture library is incompatible with this CPU. Check the Pi gesture setup."
            else:
                self._unavailable = "The gesture worker stopped. Check the Pi logs before restarting AURUS."
            return {"message": self._unavailable}, 503
        except (RuntimeError, ValueError):
            self._close_worker()
            log.exception("Gesture scan failed")
            return {"message": "Gesture recognition could not start. Check the model and Pi logs."}, 503
        finally:
            self._lock.release()

    def _scan(self, generation: int, automatic: bool, after: str | None) -> tuple[dict, int]:
        snapshot, jpeg = self.vision.get_fresh_frame()
        if not jpeg or snapshot.generation != generation:
            return {"message": "Turn the camera on and wait for the live preview."}, 409
        if not self.model_path.is_file():
            return {"message": "The gesture model is missing on AURUS."}, 503

        # Model loading happens on the first request, before the scan window.
        self._start_worker()

        scan = _ExpoScan(after) if automatic else _Scan()
        gesture = None
        started = time.monotonic()
        last_timestamp = started
        # Limit sustained Expo inference to about six samples per second.
        sample_interval = 0.16 if automatic else 0.0
        while not self._stopping.is_set() and time.monotonic() < started + 3.0:
            snapshot, jpeg = self.vision.get_fresh_frame()
            if not snapshot.enabled or snapshot.generation != generation:
                break
            if (jpeg and snapshot.timestamp > last_timestamp
                    and snapshot.timestamp - last_timestamp >= sample_interval):
                last_timestamp = snapshot.timestamp
                gesture = scan.observe(self._recognize(jpeg), snapshot.timestamp)
                if gesture or (automatic and scan.released):
                    break
            time.sleep(0.045)

        if self._stopping.is_set():
            return {"message": "Gesture recognition is stopping."}, 503
        snapshot, jpeg = self.vision.get_fresh_frame()
        if not jpeg or snapshot.generation != generation:
            return {"message": "The camera changed during the scan. Try again when it is ready."}, 409
        if not automatic and gesture is None and scan.still_palm:
            gesture = "open-palm"
        result = {"gesture": gesture, "generation": generation}
        if automatic:
            result["released"] = scan.released
        return result, 200
