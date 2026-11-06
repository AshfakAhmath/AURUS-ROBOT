from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.services.gesture_service import GestureService, _Scan


def _point(x, y):
    return SimpleNamespace(x=x, y=y)


def _pose(category, wrist_x=0.45):
    hand = [_point(wrist_x, 0.55)] * 21
    hand[9] = _point(wrist_x, 0.40)
    return SimpleNamespace(
        hand_landmarks=[hand],
        gestures=[[SimpleNamespace(category_name=category, score=0.95)]],
    )


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
    service._load_model = Mock()
    assert service.scan(1)[1] == 409
    assert service.scan(1)[1] == 409
    service._load_model.assert_not_called()


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
    service._load_model = Mock()
    service._recognize = Mock()
    assert service.scan(0)[1] == 409
    service._recognize.assert_not_called()
