import os
from pathlib import Path
import signal
import sys
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.services.gesture_service import GestureService, _ExpoScan, _Scan


def _point(x, y):
    return SimpleNamespace(x=x, y=y)


def _pose(category, wrist_x=0.45):
    hand = [_point(wrist_x, 0.55)] * 21
    hand[9] = _point(wrist_x, 0.40)
    return SimpleNamespace(
        hand_landmarks=[hand],
        gestures=[[SimpleNamespace(category_name=category, score=0.95)]],
    )


def _crash_on_start(connection, model_path):
    os._exit(128 + signal.SIGILL)


def _crash_on_frame(connection, model_path):
    connection.send(("ok", None))
    connection.recv_bytes()
    os._exit(128 + signal.SIGILL)


@pytest.mark.parametrize(("category", "gesture"), [("Thumb_Up", "thumbs-up"), ("Victory", "peace")])
def test_static_pose_requires_repeated_observations(category, gesture):
    scan = _Scan()
    assert scan.observe(_pose(category), 0.0) is None
    assert scan.observe(_pose(category), 0.1) == gesture


def test_stationary_open_palm_and_wave_are_distinct():
    scan = _Scan()
    for timestamp in (0.0, 0.2, 0.4):
        assert scan.observe(_pose("Open_Palm"), timestamp) is None
    assert scan.still_palm

    assert scan.observe(_pose("Open_Palm", 0.61), 0.6) is None
    assert not scan.still_palm
    assert scan.observe(_pose("Open_Palm", 0.44), 0.8) == "wave"


def test_expo_ignores_held_pose_and_accepts_a_different_gesture():
    scan = _ExpoScan(after="thumbs-up")
    assert scan.observe(_pose("Thumb_Up"), 0.0) is None
    assert scan.observe(_pose("Thumb_Up"), 0.2) is None
    assert not scan.released
    assert scan.observe(_pose("Victory"), 0.4) is None
    assert scan.observe(_pose("Victory"), 0.6) == "peace"


def test_expo_releases_only_after_hands_leave_the_frame():
    scan = _ExpoScan(after="peace")
    no_hands = SimpleNamespace(hand_landmarks=[], gestures=[])
    assert scan.observe(no_hands, 0.0) is None
    assert scan.observe(_pose("Victory"), 0.3) is None
    assert scan.observe(no_hands, 0.5) is None
    assert not scan.released
    assert scan.observe(no_hands, 1.0) is None
    assert scan.released


@pytest.mark.parametrize(("after", "gesture"), [(None, "open-palm"), ("wave", None), ("open-palm", None)])
def test_expo_open_palm_requires_a_pause_and_does_not_repeat(after, gesture):
    scan = _ExpoScan(after=after)
    for timestamp in (0.0, 0.3, 0.6):
        assert scan.observe(_pose("Open_Palm"), timestamp) is None
    assert scan.observe(_pose("Open_Palm"), 0.95) == gesture


def test_heart_requires_both_hands_and_joined_fingertips():
    left = [_point(0.28, 0.59)] * 21
    right = [_point(0.72, 0.59)] * 21
    left[9], right[9] = _point(0.39, 0.52), _point(0.61, 0.52)
    left[8], right[8] = _point(0.49, 0.43), _point(0.51, 0.43)
    left[4], right[4] = _point(0.49, 0.55), _point(0.51, 0.55)
    scan = _Scan()
    result = SimpleNamespace(hand_landmarks=[left, right], gestures=[])
    assert scan.observe(result, 0.0) is None
    assert scan.observe(result, 0.1) == "heart"

    right[8] = _point(0.70, 0.43)
    assert scan.observe(result, 0.2) is None


def test_camera_off_or_stale_generation_skips_model_loading(tmp_path):
    camera = Mock()
    camera.get_fresh_frame.side_effect = [
        (SimpleNamespace(enabled=False, generation=1), None),
        (SimpleNamespace(enabled=True, generation=2), b"new-frame"),
    ]
    service = GestureService(camera, tmp_path / "gesture_recognizer.task")
    service._start_worker = Mock()
    assert service.scan(1)[1] == 409
    assert service.scan(1)[1] == 409
    service._start_worker.assert_not_called()


def test_camera_change_during_scan_discards_result(tmp_path):
    model = tmp_path / "gesture_recognizer.task"
    model.touch()
    camera = Mock()
    camera.get_fresh_frame.side_effect = [
        (SimpleNamespace(enabled=True, generation=0), b"old"),
        (SimpleNamespace(enabled=True, generation=1), b"new"),
        (SimpleNamespace(enabled=True, generation=1), b"new"),
    ]
    service = GestureService(camera, model)
    service._start_worker = Mock()
    service._recognize = Mock()
    assert service.scan(0)[1] == 409
    service._recognize.assert_not_called()


def test_worker_timeout_cleans_up_and_allows_retry(tmp_path):
    service = GestureService(Mock(), tmp_path / "gesture_recognizer.task")
    service._scan = Mock(side_effect=[TimeoutError("Gesture worker timed out during startup"), ({}, 200)])
    service._close_worker = Mock()

    result, status = service.scan(0)
    assert status == 503
    assert "timed out during startup" in result["message"]
    service._close_worker.assert_called_once_with()
    assert service.scan(0) == ({}, 200)


@pytest.mark.skipif(os.name != "posix", reason="requires an inherited POSIX pipe")
def test_separate_python_worker_uses_the_gesture_pipe(monkeypatch, tmp_path):
    worker_script = tmp_path / "gesture_worker.py"
    worker_script.write_text(
        "import sys\n"
        "from multiprocessing.connection import Connection\n"
        "with Connection(int(sys.argv[1])) as connection:\n"
        "    connection.send(('ok', sys.argv[2]))\n"
        "    connection.send(('ok', connection.recv_bytes()))\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("src.services.gesture_service.__file__", str(tmp_path / "gesture_service.py"))
    model = tmp_path / "gesture_recognizer.task"
    service = GestureService(Mock(), model, worker_python=Path(sys.executable))
    try:
        service._start_worker()
        assert service._recognize(b"frame") == b"frame"
        worker = service._worker
    finally:
        service.stop()
    assert worker.returncode == 0
    assert service._connection is None


@pytest.mark.parametrize("worker", [_crash_on_start, _crash_on_frame])
def test_native_crash_only_disables_gestures(worker, monkeypatch, tmp_path):
    model = tmp_path / "gesture_recognizer.task"
    model.touch()
    camera = Mock()
    camera.get_fresh_frame.side_effect = lambda: (
        SimpleNamespace(enabled=True, generation=0, timestamp=time.monotonic()), b"frame",
    )
    monkeypatch.setattr("src.services.gesture_service.serve_gestures", worker)
    service = GestureService(camera, model)
    try:
        result, status = service.scan(0)
        assert status == 503
        assert "incompatible with this CPU" in result["message"]
        assert service._worker is None
        assert service._connection is None
        service._start_worker = Mock()
        assert service.scan(0) == (result, status)
        service._start_worker.assert_not_called()
    finally:
        service.stop()
