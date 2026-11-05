"""Camera control with fake capture devices, identity, cloud and motors only."""

import json
from pathlib import Path
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.core.models import IdentityResult, RobotMode, VisionSnapshot, camera_session_current
from src.runtime import RobotRuntime
from src.services.behavior_controller import BehaviorController
from src.services.identity_service import IdentityService
from src.services.vision_service import VisionService
from src.web.app import create_app
from tests.test_evaluation_core import RuntimeHarness
from tests.test_final_safety import build


def until(condition, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.005)
    assert condition(), "Camera state transition timed out"


class FakeDevice:
    def __init__(self, owner, number):
        self.owner, self.number = owner, number
        self.released = False
        self.reads = 0

    def release(self):
        if self.owner.fail_release:
            self.owner.fail_release = False
            raise RuntimeError("Fake driver release failure")
        assert not self.released
        self.released = True
        self.owner.active_devices -= 1


class FakeVision(VisionService):
    def __init__(self, enabled=True):
        repository = SimpleNamespace(load_embeddings=lambda: [])
        identity = IdentityService(repository)
        super().__init__(identity, Path('models'), rate_hz=100, enabled=enabled)
        self.devices = []
        self.active_devices = self.max_devices = 0
        self.processed = []
        self.configure_calls = 0
        self.block_read = self.block_open = False
        self.entered = threading.Event()
        self.resume = threading.Event()
        self.fail_release = False

    def _configure_models(self):
        self.configure_calls += 1
        self._backend = 'fake-sface'

    def _open_camera(self):
        device = FakeDevice(self, len(self.devices) + 1)
        self.devices.append(device)
        self.active_devices += 1
        self.max_devices = max(self.max_devices, self.active_devices)
        if self.block_open:
            self.block_open = False
            self.entered.set()
            assert self.resume.wait(3)
        self._camera = device
        self._camera_kind = 'opencv'

    def _read_frame(self):
        device = self._camera
        device.reads += 1
        if self.block_read:
            self.block_read = False
            self.entered.set()
            assert self.resume.wait(3)
        return device.number

    def _process_frame(self, frame):
        self.processed.append(frame)
        self._snapshot = VisionSnapshot(timestamp=time.monotonic(), healthy=True,
                                        backend='fake-sface', generation=self._generation)
        self._jpeg = f'frame-{frame}'.encode()
        return self._snapshot


@pytest.fixture
def vision():
    service = FakeVision()
    try:
        yield service
    finally:
        service.resume.set()
        service.stop()


def runtime_for(vision):
    _, sampler, driver, arbiter, _, _ = build()
    runtime = object.__new__(RobotRuntime)
    runtime._lock = threading.RLock()
    runtime._last_manual_sequence = -1
    runtime._object_scan = threading.Lock()
    runtime.vision = vision
    runtime.identity = vision.identity_service
    runtime.sensor_sampler, runtime.driver, runtime.arbiter = sampler, driver, arbiter
    runtime.behavior = SimpleNamespace(begin_exploration=Mock(), finish_exploration=Mock())
    runtime.demo = SimpleNamespace(active=False, cancel=Mock(), owner_disconnected=Mock())
    runtime.tts = SimpleNamespace(speak=Mock())
    runtime.emit = Mock()
    return runtime


def test_disabled_start_does_not_open_capture_or_load_models():
    service = FakeVision(enabled=False)
    service.start()
    try:
        time.sleep(0.03)
        assert service._thread.is_alive()
        assert not service.devices and service.configure_calls == 0
        assert service.control_state()['state'] == 'off' and service.get_jpeg() is None
    finally:
        service.stop()
    assert not service._thread.is_alive()


def test_off_releases_capture_clears_identity_and_cancels_enrollment(vision):
    vision.start()
    until(lambda: vision.get_snapshot().healthy)
    identity = vision.identity_service
    identity._known[7] = ('Visitor', [])
    identity._current = IdentityResult('known', 7, 'Visitor', 0.9)
    identity.begin_enrollment('Next visitor')
    vision.set_enabled(False)
    until(lambda: vision.control_state()['state'] == 'off')
    reads = vision.devices[0].reads
    time.sleep(0.03)
    assert vision.devices[0].released and vision.active_devices == 0
    assert vision.devices[0].reads == reads
    assert not vision.get_snapshot().enabled and not vision.get_snapshot().healthy
    assert not vision.get_snapshot().faces and vision.get_jpeg() is None
    assert identity.current().user_id is None and not identity.get_enrollment().active
    assert 7 in identity._known  # Saved identities are not deleted.


def test_reenable_uses_one_worker_and_only_fresh_camera_frames(vision):
    vision.start()
    until(lambda: vision.get_snapshot().healthy)
    thread = vision._thread
    vision.set_enabled(False)
    until(lambda: vision.control_state()['state'] == 'off')
    vision.set_enabled(True)
    until(lambda: vision.get_jpeg() == b'frame-2')
    for _ in range(5):
        vision.set_enabled(True)
        vision.start()
    assert vision._thread is thread and len(vision.devices) == 2
    assert vision.max_devices == 1 and vision.get_snapshot().generation == 2


@pytest.mark.parametrize('operation', ['read', 'open'])
def test_off_on_while_driver_is_busy_discards_old_frame_and_never_duplicates_owner(vision, operation):
    setattr(vision, 'block_' + operation, True)
    vision.start()
    assert vision.entered.wait(1)
    state = vision.set_enabled(False)
    assert state['state'] == 'stopping' and vision.get_jpeg() is None
    vision.set_enabled(True)
    vision.start()
    vision.resume.set()
    until(lambda: vision.get_jpeg() == b'frame-2')
    assert 1 not in vision.processed
    assert vision.devices[0].released and vision.max_devices == 1


def test_release_failure_is_visible_instead_of_claiming_camera_is_off(vision):
    vision.start()
    until(lambda: vision.get_snapshot().healthy)
    vision.fail_release = True
    vision.set_enabled(False)
    until(lambda: vision.control_state()['state'] == 'error')
    assert 'release failed' in vision.control_state()['error']
    assert vision.active_devices == 1 and vision.get_jpeg() is None
    vision._wake.set()  # Prompt a retry instead of waiting through the backoff.
    until(lambda: vision.control_state()['state'] == 'off')
    assert vision.active_devices == 0


def test_late_processing_and_error_cannot_restore_disabled_or_old_session(vision):
    old = vision.get_snapshot()
    vision.set_enabled(False)
    vision.process_frame(1, old.generation)
    vision._set_error('Old camera failure', old.generation)
    assert vision.get_snapshot().backend == 'disabled' and not vision.processed
    vision.set_enabled(True)
    vision.process_frame(1, old.generation)
    assert not vision.processed and vision.get_jpeg() is None
    assert not camera_session_current(old, VisionSnapshot(healthy=True, generation=2))


@pytest.mark.parametrize('invalid', ['false', None, 0, 1, {}])
def test_camera_control_requires_actual_boolean(vision, invalid):
    with pytest.raises(ValueError, match='true or false'):
        vision.set_enabled(invalid)
    assert vision.enabled


def test_camera_off_halts_explore_and_reenable_never_resumes_it(vision):
    runtime = runtime_for(vision)
    assert runtime.set_mode('explore')
    runtime.arbiter.command('explore-forward', 0.2, 0, 0)
    runtime.set_camera_enabled(False)
    assert runtime.arbiter.mode == RobotMode.IDLE and runtime.driver.commands[-1] == (0, 0, 0)
    runtime.behavior.finish_exploration.assert_called_once()
    assert not runtime.set_mode('explore')
    # Commands already in flight cannot undo the camera-off stop.
    assert not runtime.arbiter.command('curiosity-scan', 0, 0, 0.2)
    assert not runtime.arbiter.command('explore-forward', 0.2, 0, 0)
    runtime.set_camera_enabled(True)
    assert runtime.arbiter.mode == RobotMode.IDLE
    assert runtime.set_mode('explore')


def test_camera_off_keeps_manual_control_and_demo_available(vision):
    runtime = runtime_for(vision)
    runtime.set_camera_enabled(False)
    assert runtime.set_mode('manual')
    assert runtime.manual_drive(0.2, 0, 0, 1)
    runtime.demo.active = True
    runtime.set_camera_enabled(True)
    runtime.set_camera_enabled(False)
    runtime.demo.cancel.assert_not_called()
    runtime.tts.speak.assert_not_called()


def test_enrollment_and_visual_questions_explain_camera_off(vision):
    runtime = runtime_for(vision)
    runtime.set_camera_enabled(False)
    with pytest.raises(ValueError, match='switched off'):
        runtime.start_enrollment('Visitor')
    harness = RuntimeHarness()
    harness.vision = vision
    harness.groq_vision = Mock(ready=True)
    result = harness.handle_text('What can you see?')
    assert 'switched off' in result['response']
    harness.groq_vision.describe.assert_not_called()


def fresh_snapshot(vision):
    vision._snapshot = VisionSnapshot(timestamp=time.monotonic(), healthy=True,
                                      backend='fake-sface', generation=vision._generation)
    vision._jpeg = b'fresh-jpeg'


def test_visual_answer_started_before_off_on_is_discarded(vision):
    fresh_snapshot(vision)
    harness = RuntimeHarness()
    harness.vision = vision
    def describe(_jpeg):
        vision.set_enabled(False)
        vision.set_enabled(True)
        fresh_snapshot(vision)
        return 'STALE_PERSON_DESCRIPTION'
    harness.groq_vision = SimpleNamespace(ready=True, describe=describe)
    result = harness.handle_text('What can you see?')
    assert 'STALE_PERSON' not in result['response'] and 'ask again' in result['response']


def test_mcp_camera_tool_rejects_off_and_discards_late_result(vision):
    _, _, _, _, runtime, agent = build()
    runtime.vision = vision
    runtime.groq_vision = Mock()
    vision.set_enabled(False)
    assert 'switched off' in json.loads(agent._dispatch_tool('capture_image', {}))['message']
    runtime.groq_vision.describe.assert_not_called()
    vision.set_enabled(True)
    fresh_snapshot(vision)
    def describe(_jpeg):
        vision.set_enabled(False)
        return 'STALE_PERSON_DESCRIPTION'
    runtime.groq_vision.describe.side_effect = describe
    result = json.loads(agent._dispatch_tool('capture_image', {}))
    assert result['status'] == 'error' and 'STALE_PERSON' not in str(result)


def test_object_scan_respects_camera_off_and_discards_old_session(vision):
    runtime = runtime_for(vision)
    runtime.groq_vision = Mock(ready=True)
    runtime.set_camera_enabled(False)
    result = runtime.detect_objects()
    assert result['status'] == 'error' and 'switched off' in result['message']
    runtime.groq_vision.detect_objects.assert_not_called()
    runtime.set_camera_enabled(True)
    fresh_snapshot(vision)
    def detect(_jpeg):
        runtime.set_camera_enabled(False)
        runtime.set_camera_enabled(True)
        fresh_snapshot(vision)
        return ['STALE_OBJECT']
    runtime.groq_vision.detect_objects.side_effect = detect
    result = runtime.detect_objects()
    assert result['status'] == 'error' and not result['objects']
    assert 'STALE_OBJECT' not in str(result)


def test_late_greeting_from_previous_camera_session_is_discarded(vision):
    runtime = runtime_for(vision)
    runtime._announce = Mock()
    runtime.set_camera_enabled(False)
    runtime.set_camera_enabled(True)
    runtime._handle_behavior_event('greeting', {'name': 'Old visitor', 'camera_generation': 0})
    runtime._announce.assert_not_called()


@pytest.mark.parametrize('method', ['describe', 'analyze_discovery'])
def test_exploration_discards_cloud_result_after_camera_toggle(vision, method):
    fresh_snapshot(vision)
    def analyze(*args):
        vision.set_enabled(False)
        return {'observation': 'STALE_SCENE', 'interesting': True} if method == 'analyze_discovery' else 'STALE_SCENE'
    analyzer = SimpleNamespace(**{method: analyze})
    events = []
    controller = BehaviorController(None, None, vision, lambda *args: events.append(args), analyzer)
    controller._observe_environment()
    assert not events


def test_socket_control_validates_and_updates_all_dashboards(vision):
    runtime = runtime_for(vision)
    runtime.set_event_sink = Mock()
    runtime.dashboard_connected = Mock()
    runtime.health = lambda: {'camera_enabled': vision.enabled}
    runtime.telemetry = lambda: {'vision': vision.get_snapshot().as_dict()}
    runtime.emit = Mock()
    app, socketio, _, _ = create_app(runtime)
    operator, visitor = socketio.test_client(app), socketio.test_client(app)
    operator.get_received(); visitor.get_received()
    operator.emit('set_camera_enabled', {'enabled': 'false'})
    assert vision.enabled
    assert any(event['name'] == 'command_error' for event in operator.get_received())
    operator.emit('set_camera_enabled', {'enabled': False})
    for client in (operator, visitor):
        updates = [event for event in client.get_received() if event['name'] == 'camera_configuration']
        assert updates and updates[-1]['args'][0]['enabled'] is False
    operator.disconnect(); visitor.disconnect()
