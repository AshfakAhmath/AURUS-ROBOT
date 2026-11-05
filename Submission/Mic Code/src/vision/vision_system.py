import cv2
import mediapipe as mp
import time
import threading
import os
import random

class PresenceTracker:
    def __init__(self):
        self.state = "Absent"
        self.last_seen_time = 0

    def update(self, faces):
        now = time.time()
        if len(faces) > 0:
            self.last_seen_time = now
            # faces is a list of dicts: {"box": [x,y,w,h], "score": float}
            # Mediapipe bbox width is normalized (0.0 to 1.0)
            max_w = max(f["box"][2] for f in faces) if faces else 0
            
            if max_w > 0.15:
                self.state = "Present"
            else:
                self.state = "Nearby"
        else:
            time_since_seen = now - self.last_seen_time
            if time_since_seen > 10:
                self.state = "Absent"
            elif time_since_seen > 3:
                self.state = "Away"
                
        return self.state

class ObjectDetector:
    """Handles object recognition using MobileNet-SSD. Falls back to mock detection if model is missing."""
    def __init__(self, model_dir="models"):
        self.mock_mode = not os.path.exists(os.path.join(model_dir, "mobilenet_ssd.caffemodel"))
        if self.mock_mode:
            print("[VisionSystem] MobileNet-SSD not found. Running Object Recognition in Mock Mode.")
            self.mocked_objects = ["Laptop", "Bottle", "Chair", "Book", "Phone"]
        else:
            print("[VisionSystem] MobileNet-SSD loaded successfully.")
            
        self.current_objects = []
        self.last_mock_time = 0

    def process(self, frame):
        objects = []
        if self.mock_mode:
            now = time.time()
            # Mock detection: hold an object for 5 seconds, then clear for 5 seconds
            if now - self.last_mock_time > 10:
                self.last_mock_time = now
                self.current_objects = [{
                    "label": random.choice(self.mocked_objects),
                    "confidence": round(random.uniform(0.75, 0.99), 2),
                    "box": [0.2, 0.2, 0.3, 0.3] # Normalized [x, y, w, h]
                }]
            elif now - self.last_mock_time > 5:
                self.current_objects = []
                
            objects = self.current_objects
        return objects

class VisionSystem:
    def __init__(self, camera_index=0):
        self.camera_index = camera_index
        self.cap = None
        self.running = False
        self.thread = None
        
        try:
            self.mp_face_detection = mp.solutions.face_detection
            self.face_detection = self.mp_face_detection.FaceDetection(
                model_selection=0, min_detection_confidence=0.5)
            self.mock_face_mode = False
        except AttributeError:
            print("[VisionSystem] MediaPipe solutions not available. Running Face Detection in Mock Mode.")
            self.mock_face_mode = True
            self.face_detection = None
            
        self.presence_tracker = PresenceTracker()
        self.object_detector = ObjectDetector()
        
        # Latest frame and detections
        self.current_frame = None
        self.faces = []
        self.objects = []
        self.presence_state = "Absent"
        self.mock_presence_override = None

    def start(self):
        self.cap = cv2.VideoCapture(self.camera_index)
        if not self.cap.isOpened():
            print(f"[VisionSystem] Error: Could not open camera {self.camera_index}")
            return
            
        self.running = True
        self.thread = threading.Thread(target=self._process_loop, daemon=True)
        self.thread.start()
        print("[VisionSystem] Started vision processing loop.")

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=2.0)
        if self.cap:
            self.cap.release()
        print("[VisionSystem] Stopped vision processing.")

    def _process_loop(self):
        while self.running:
            ret, frame = self.cap.read()
            if not ret:
                time.sleep(0.1)
                continue
                
            self.current_frame = frame.copy()
            
            # Convert BGR to RGB for MediaPipe
            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            
            # Process face detection
            detected_faces = []
            if not self.mock_face_mode:
                results = self.face_detection.process(rgb_frame)
                if results.detections:
                    for detection in results.detections:
                        bbox = detection.location_data.relative_bounding_box
                        detected_faces.append({
                            "box": [bbox.xmin, bbox.ymin, bbox.width, bbox.height],
                            "score": detection.score[0]
                        })
            else:
                # Mock face detection: simulate a face presence periodically
                if time.time() % 10 < 5:
                    detected_faces.append({
                        "box": [0.4, 0.4, 0.2, 0.2],
                        "score": 0.95
                    })
                    
            self.faces = detected_faces
            self.presence_state = self.presence_tracker.update(self.faces)
            
            # Process object detection
            self.objects = self.object_detector.process(frame)
            
            # Sleep a bit to limit framerate and save CPU
            time.sleep(0.05)
            
    def get_latest_data(self):
        """Returns the latest detections and state for other modules to consume."""
        return {
            "presence_state": self.mock_presence_override if self.mock_presence_override else self.presence_state,
            "faces": self.faces,
            "objects": self.objects
        }

    def set_mock_presence(self, state):
        """Force a presence state for demonstration purposes ('Present', 'Absent', or None to disable)."""
        self.mock_presence_override = state
