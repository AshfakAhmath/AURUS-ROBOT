"""Run native hand recognition outside the robot control process."""

from types import SimpleNamespace


def serve_gestures(connection, model_path):
    try:
        import cv2
        import mediapipe as mp
        import numpy as np

        options = mp.tasks.vision.GestureRecognizerOptions(
            base_options=mp.tasks.BaseOptions(model_asset_path=str(model_path)),
            num_hands=2,
        )
        with mp.tasks.vision.GestureRecognizer.create_from_options(options) as recognizer:
            connection.send(("ok", None))
            while True:
                jpeg = connection.recv_bytes()
                frame = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
                if frame is None:
                    raise ValueError("Invalid gesture scan frame")
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                result = recognizer.recognize(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb))
                # Sending native result types could import MediaPipe in the parent.
                observation = SimpleNamespace(
                    hand_landmarks=[
                        [SimpleNamespace(x=float(point.x), y=float(point.y)) for point in hand]
                        for hand in result.hand_landmarks
                    ],
                    gestures=[
                        [SimpleNamespace(category_name=top.category_name, score=float(top.score))
                         for top in categories[:1]]
                        for categories in result.gestures
                    ],
                )
                connection.send(("ok", observation))
    except ImportError as exc:
        connection.send(("missing", str(exc)))
    except (EOFError, BrokenPipeError, ConnectionResetError):
        pass
    except Exception as exc:
        connection.send(("error", str(exc)))
    finally:
        connection.close()
