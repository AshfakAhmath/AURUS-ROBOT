"""Recognize a single hand gesture from a short, explicitly requested scan."""

from __future__ import annotations

from math import hypot
from pathlib import Path
import logging
import threading
import time


log = logging.getLogger(__name__)
POSES = {"Thumb_Up": "thumbs-up", "Victory": "peace"}


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


class GestureService:
    def __init__(self, vision, model_path: Path):
        self.vision = vision
        self.model_path = model_path
        self._recognizer = None
        self._lock = threading.Lock()

    def _load_model(self):
        import mediapipe as mp

        options = mp.tasks.vision.GestureRecognizerOptions(
            base_options=mp.tasks.BaseOptions(model_asset_path=str(self.model_path)),
            num_hands=2,
        )
        self._recognizer = mp.tasks.vision.GestureRecognizer.create_from_options(options)

    def _recognize(self, jpeg):
        import cv2
        import mediapipe as mp
        import numpy as np

        frame = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            raise ValueError("Invalid gesture scan frame")
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        return self._recognizer.recognize(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb))

    def scan(self, generation: int) -> tuple[dict, int]:
        if not self._lock.acquire(blocking=False):
            return {"message": "A gesture scan is already running."}, 409
        try:
            return self._scan(generation)
        except ImportError:
            return {"message": "Gesture recognition needs MediaPipe installed on AURUS."}, 503
        except (OSError, RuntimeError, ValueError):
            log.exception("Gesture scan failed")
            return {"message": "Gesture recognition could not start. Check the model and Pi logs."}, 503
        finally:
            self._lock.release()

    def _scan(self, generation: int) -> tuple[dict, int]:
        snapshot, jpeg = self.vision.get_fresh_frame()
        if not jpeg or snapshot.generation != generation:
            return {"message": "Turn the camera on and wait for the live preview."}, 409
        if not self.model_path.is_file():
            return {"message": "The gesture model is missing on AURUS."}, 503

        # Model loading happens on the first request, before the scan window.
        if self._recognizer is None:
            self._load_model()

        scan = _Scan()
        gesture = None
        started = time.monotonic()
        last_timestamp = started
        while time.monotonic() < started + 3.0:
            snapshot, jpeg = self.vision.get_fresh_frame()
            if not snapshot.enabled or snapshot.generation != generation:
                break
            if jpeg and snapshot.timestamp > last_timestamp:
                last_timestamp = snapshot.timestamp
                gesture = scan.observe(self._recognize(jpeg), snapshot.timestamp)
                if gesture:
                    break
            time.sleep(0.045)

        snapshot, jpeg = self.vision.get_fresh_frame()
        if not jpeg or snapshot.generation != generation:
            return {"message": "The camera changed during the scan. Try again when it is ready."}, 409
        if gesture is None and scan.still_palm:
            gesture = "open-palm"
        return {"gesture": gesture, "generation": generation}, 200
