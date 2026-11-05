"""Picamera2/OpenCV vision with YuNet detection and SFace recognition."""

from __future__ import annotations

import os
from pathlib import Path
import threading
import time

import numpy as np

from src.core.models import IdentityResult, VisionSnapshot

try:
    import cv2
except ImportError:  # pragma: no cover - exercised on dependency-free hosts
    cv2 = None

try:
    from picamera2 import Picamera2
except ImportError:  # pragma: no cover - normal away from Raspberry Pi
    Picamera2 = None


class VisionService:
    def __init__(
        self,
        identity_service,
        model_dir: str | Path,
        camera_index: int = 0,
        process_size: tuple[int, int] = (320, 256),
        rate_hz: float = 10.0,
        detection_threshold: float | None = None,
    ):
        self.identity_service = identity_service
        self.model_dir = Path(model_dir)
        self.camera_index = camera_index
        self.width, self.height = process_size
        self.period = 1.0 / max(1.0, rate_hz)
        if detection_threshold is None:
            try:
                detection_threshold = float(os.getenv("AURUS_FACE_DETECTION_THRESHOLD", "0.65"))
            except ValueError:
                detection_threshold = 0.65
        self.detection_threshold = max(0.3, min(0.95, detection_threshold))
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._camera = None
        self._camera_kind = "none"
        self._camera_format = ""
        self._detector = None
        self._recognizer = None
        self._haar = None
        self._snapshot = VisionSnapshot()
        self._jpeg: bytes | None = None
        self._backend = "unavailable"
        self._dnn_fallback_reported = False

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="vision-service", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        self._close_camera()

    def get_snapshot(self) -> VisionSnapshot:
        with self._lock:
            return self._snapshot

    def get_jpeg(self) -> bytes | None:
        with self._lock:
            return self._jpeg

    def get_fresh_jpeg(self) -> bytes | None:
        with self._lock:
            if self._snapshot.healthy and self._snapshot.age <= 1.0:
                return self._jpeg
            return None

    def _configure_models(self) -> None:
        if cv2 is None:
            raise RuntimeError("OpenCV is not installed")
        yunet = self.model_dir / "face_detection_yunet_2023mar.onnx"
        sface = self.model_dir / "face_recognition_sface_2021dec.onnx"
        self._detector = None
        self._recognizer = None

        # Load recognition independently. Some Raspberry Pi OS OpenCV builds
        # cannot execute the current YuNet graph but can still run SFace.
        if sface.exists() and hasattr(cv2, "FaceRecognizerSF"):
            try:
                self._recognizer = cv2.FaceRecognizerSF.create(str(sface), "")
            except Exception as exc:
                print(f"[VisionService] SFace could not be loaded: {exc}")

        if yunet.exists() and hasattr(cv2, "FaceDetectorYN"):
            try:
                self._detector = cv2.FaceDetectorYN.create(
                    str(yunet), "", (self.width, self.height),
                    self.detection_threshold, 0.3, 5000,
                )
                self._backend = "yunet+sface" if self._recognizer is not None else "yunet-only"
                return
            except Exception as exc:
                print(f"[VisionService] YuNet could not be loaded: {exc}")

        if self._configure_haar():
            return

        print("[VisionService] Warning: No YuNet or Haar face detection models found. Running video stream in streaming-only mode.")
        self._backend = "video-only-fallback"

    def _configure_haar(self) -> bool:
        """Configure the low-cost detector used when YuNet is unavailable."""
        self._haar = None

        cascade_paths = []
        if hasattr(cv2, "data") and hasattr(cv2.data, "haarcascades"):
            cascade_paths.append(Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml")
        cascade_paths.extend([
            self.model_dir / "haarcascade_frontalface_default.xml",
            Path("/usr/share/opencv/haarcascades/haarcascade_frontalface_default.xml"),
            Path("/usr/share/opencv4/haarcascades/haarcascade_frontalface_default.xml"),
            Path("/usr/local/share/opencv4/haarcascades/haarcascade_frontalface_default.xml"),
            Path("/usr/share/share/opencv4/haarcascades/haarcascade_frontalface_default.xml"),
        ])

        for path in cascade_paths:
            if path.exists():
                self._haar = cv2.CascadeClassifier(str(path))
                if not self._haar.empty():
                    self._backend = (
                        "haar+sface-fallback"
                        if self._recognizer is not None
                        else "haar-session-fallback"
                    )
                    return True
        return False

    def _open_camera(self) -> None:
        if Picamera2 is not None and os.name != "nt":
            camera = None
            try:
                camera = Picamera2()
                configuration_error = None
                for builder in (camera.create_video_configuration, camera.create_preview_configuration):
                    try:
                        # Picamera2's RGB888 is stored B, G, R in memory, which
                        # is already the channel order OpenCV expects.
                        config = builder(main={"size": (640, 480), "format": "RGB888"})
                        camera.configure(config)
                        configuration_error = None
                        break
                    except Exception as exc:
                        configuration_error = exc
                if configuration_error is not None:
                    raise configuration_error
                try:
                    camera.set_controls({"AwbEnable": True})
                except Exception as exc:
                    print(f"[VisionService] Automatic white balance could not be enabled: {exc}")
                camera.start()
                time.sleep(0.5)
                frame = camera.capture_array("main")
                if frame is not None and frame.size > 0:
                    self._camera = camera
                    self._camera_kind = "picamera2"
                    self._camera_format = "RGB888"
                    print("[VisionService] Raspberry Pi CSI camera connected via Picamera2.")
                    return
                else:
                    camera.stop()
                    camera.close()
                    print("[VisionService] Picamera2 started but capture_array returned empty frame.")
            except Exception as exc:
                if camera is not None:
                    try:
                        camera.stop()
                    except Exception:
                        pass
                    try:
                        camera.close()
                    except Exception:
                        pass
                message = str(exc).strip()
                if "sequence did not complete" in message.lower() or "busy" in message.lower():
                    raise RuntimeError(
                        "CSI camera is busy or owned by another process; "
                        "stop the other camera process and AURUS will reconnect"
                    ) from exc
                print(f"[VisionService] Picamera2 failed: {exc}. Trying OpenCV Video4Linux fallback...")
        elif os.name != "nt" and Picamera2 is None:
            print("[VisionService] Note: python3-picamera2 library not found in current Python environment. If using a venv on Raspberry Pi, ensure it was created with --system-site-packages.")

        if cv2 is None:
            raise RuntimeError("No camera backend available")

        indices_to_try = []
        for idx in [self.camera_index, 0, 1, 2, 3, 4]:
            if idx not in indices_to_try:
                indices_to_try.append(idx)

        for idx in indices_to_try:
            try:
                camera = cv2.VideoCapture(idx)
                if not camera.isOpened():
                    camera.release()
                    continue
                camera.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                time.sleep(0.2)
                ok, frame = camera.read()
                if ok and frame is not None and frame.size > 0:
                    self._camera = camera
                    self._camera_kind = "opencv"
                    print(f"[VisionService] Connected to video camera at index {idx} ({frame.shape[1]}x{frame.shape[0]}).")
                    return
                else:
                    camera.release()
            except Exception:
                pass

        raise RuntimeError(f"Could not open camera stream (checked indices {indices_to_try}). Ensure ribbon cable is seated & sensor enabled.")

    def _close_camera(self) -> None:
        camera = self._camera
        self._camera = None
        camera_kind = self._camera_kind
        self._camera_kind = "none"
        self._camera_format = ""
        if camera is None:
            return
        try:
            if camera_kind == "picamera2":
                camera.stop()
                camera.close()
            else:
                camera.release()
        except Exception:
            pass

    def _read_frame(self):
        try:
            if self._camera_kind == "picamera2":
                frame = self._camera.capture_array("main")
                if frame is None or frame.size == 0:
                    return None
                return self._picamera_array_to_bgr(frame, self._camera_format)
            ok, frame = self._camera.read()
            return frame if (ok and frame is not None and frame.size > 0) else None
        except Exception:
            return None

    @staticmethod
    def _picamera_array_to_bgr(frame, pixel_format: str):
        """Convert Picamera2's counterintuitively named formats to OpenCV BGR."""
        fmt = str(pixel_format).upper()
        if frame.ndim != 3:
            return frame
        if frame.shape[2] == 3:
            # Picamera2 RGB888 memory is BGR; BGR888 memory is RGB.
            if fmt == "BGR888":
                return cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
            return np.ascontiguousarray(frame)
        if frame.shape[2] == 4:
            # XRGB8888 memory is B,G,R,A; XBGR8888 is R,G,B,A.
            if fmt == "XBGR8888":
                return cv2.cvtColor(frame, cv2.COLOR_RGBA2BGR)
            return np.ascontiguousarray(frame[:, :, :3])
        raise ValueError(f"Unsupported Picamera2 frame shape: {frame.shape}")

    def _detect(self, frame):
        resized = cv2.resize(frame, (self.width, self.height))
        detections = []
        if self._detector is not None:
            try:
                self._detector.setInputSize((self.width, self.height))
                _, faces = self._detector.detect(resized)
            except Exception as exc:
                # Older Pi OS OpenCV builds can reject newer YuNet graphs or
                # particular dynamic input shapes. Degrade once instead of
                # retrying the same failing DNN on every frame.
                if not self._dnn_fallback_reported:
                    print(
                        f"[VisionService] YuNet inference unavailable ({str(exc)[:180]}). "
                        "Falling back to Haar detection."
                    )
                    self._dnn_fallback_reported = True
                self._detector = None
                if not self._configure_haar():
                    self._backend = "video-only-fallback"
                return self._detect(frame)
            if faces is not None:
                for face in faces:
                    x, y, w, h = [int(value) for value in face[:4]]
                    detections.append((x, y, w, h, float(face[-1]), face))
        elif self._haar is not None:
            gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
            faces = self._haar.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(48, 48))
            for x, y, w, h in faces:
                detections.append((int(x), int(y), int(w), int(h), 1.0, None))
        return resized, detections

    def _embedding(self, frame, raw_face, box):
        if self._recognizer is None:
            return None
        if raw_face is not None:
            aligned = self._recognizer.alignCrop(frame, raw_face)
        else:
            # Haar does not provide eye/nose landmarks for alignCrop. Preserve
            # recognition by using a padded, square 112x112 face crop. It is
            # less precise than YuNet alignment but remains persistent SFace,
            # not a session-only name association.
            x, y, w, h = box
            padding = int(max(w, h) * 0.12)
            x1 = max(0, x - padding)
            y1 = max(0, y - padding)
            x2 = min(frame.shape[1], x + w + padding)
            y2 = min(frame.shape[0], y + h + padding)
            crop = frame[y1:y2, x1:x2]
            if crop.size == 0:
                return None
            side = max(crop.shape[:2])
            square = np.zeros((side, side, 3), dtype=crop.dtype)
            top = (side - crop.shape[0]) // 2
            left = (side - crop.shape[1]) // 2
            square[top:top + crop.shape[0], left:left + crop.shape[1]] = crop
            aligned = cv2.resize(square, (112, 112))
        return np.asarray(self._recognizer.feature(aligned), dtype=np.float32).reshape(-1)

    def process_frame(self, frame) -> VisionSnapshot:
        resized, detections = self._detect(frame)
        detections.sort(key=lambda item: item[2] * item[3], reverse=True)
        identity = IdentityResult()
        center_x = None
        primary_width = None
        embedding = None

        if detections:
            x, y, w, h, _, raw = detections[0]
            center_x = (x + w / 2) / self.width
            primary_width = w / self.width
            embedding = self._embedding(resized, raw, (x, y, w, h))
            if self._recognizer is not None:
                identity = self.identity_service.process_embedding(embedding)
            else:
                identity = self.identity_service.current()
        else:
            self.identity_service.clear_current()

        faces_payload = []
        for index, (x, y, w, h, score, _) in enumerate(detections):
            faces_payload.append(
                {
                    "box": [x / self.width, y / self.height, w / self.width, h / self.height],
                    "score": round(score, 3),
                    "primary": index == 0,
                }
            )
            colour = (57, 217, 138) if index == 0 else (73, 159, 255)
            cv2.rectangle(resized, (x, y), (x + w, y + h), colour, 2)

        label = "No face"
        if detections:
            label = identity.name if identity.status == "known" and identity.name else identity.status.title()
            if identity.confidence:
                label += f" {identity.confidence:.2f}"
            x, y, _, _, _, _ = detections[0]
            cv2.putText(resized, label, (max(0, x), max(20, y - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2)

        ok, encoded = cv2.imencode(".jpg", resized, [int(cv2.IMWRITE_JPEG_QUALITY), 72])
        snapshot = VisionSnapshot(
            timestamp=time.monotonic(),
            healthy=True,
            faces=tuple(faces_payload),
            primary_center_x=center_x,
            primary_width=primary_width,
            identity=identity,
            backend=self._backend,
            error="",
        )
        with self._lock:
            self._snapshot = snapshot
            self._jpeg = encoded.tobytes() if ok else None
        return snapshot

    def _set_error(self, message: str) -> None:
        if self._snapshot.error != message[:200]:
            print(f"[VisionService Error] {message}")
        jpeg_fallback = None
        if cv2 is not None:
            try:
                canvas = np.zeros((240, 320, 3), dtype=np.uint8)
                canvas[:] = (35, 35, 40)
                cv2.putText(canvas, "CAMERA OFFLINE", (55, 95), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (73, 159, 255), 2)
                cv2.putText(canvas, "Reconnecting...", (95, 125), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (180, 180, 180), 1)
                short_msg = message.replace("Waiting for camera: ", "").strip()[:42]
                cv2.putText(canvas, short_msg, (max(10, 160 - len(short_msg)*3), 165), cv2.FONT_HERSHEY_SIMPLEX, 0.36, (140, 140, 220), 1)
                ok, encoded = cv2.imencode(".jpg", canvas, [int(cv2.IMWRITE_JPEG_QUALITY), 72])
                if ok:
                    jpeg_fallback = encoded.tobytes()
            except Exception:
                pass
        with self._lock:
            self._snapshot = VisionSnapshot(
                timestamp=time.monotonic(), healthy=False, backend=self._backend, error=message[:200]
            )
            self._jpeg = jpeg_fallback

    def _run(self) -> None:
        try:
            self._configure_models()
        except Exception as exc:
            self._set_error(f"Model configuration failed: {exc}")
            return

        reconnect_delay = 2.0
        while not self._stop.is_set():
            started = time.monotonic()
            if self._camera is None:
                try:
                    self._open_camera()
                except Exception as exc:
                    self._set_error(f"Waiting for camera: {exc}")
                    self._stop.wait(reconnect_delay)
                    reconnect_delay = min(30.0, reconnect_delay * 1.7)
                    continue
                reconnect_delay = 2.0

            frame = self._read_frame()
            if frame is None:
                self._set_error("Camera returned no frame; reconnecting...")
                self._close_camera()
                self._stop.wait(1.0)
                continue

            try:
                self.process_frame(frame)
            except Exception as exc:
                self._set_error(f"Vision processing failed: {exc}")
                self._stop.wait(0.2)
                continue

            self._stop.wait(max(0.0, self.period - (time.monotonic() - started)))
        self._close_camera()
