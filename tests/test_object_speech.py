"""Object announcements and face captions with fake speech/cloud only."""

import json
from pathlib import Path
import threading
from types import SimpleNamespace
from unittest.mock import Mock
import wave

import numpy as np
import pytest

from src.services.tts_service import DemoSpeech, TTSService
from src.web.app import create_app
from tests.test_evaluation_core import expo_runtime, mock_object_completion


@pytest.mark.parametrize('labels,spoken', [
    ([], "I couldn't identify a clear object. Please try again."),
    (['a red mug'], 'I see a red mug.'),
    (['a red mug', 'a notebook'], 'I see a red mug and a notebook.'),
    (['mug', 'notebook', 'bottle', 'chair', 'table'], 'I see mug, notebook, bottle, and more.'),
])
def test_scan_queues_one_brief_normal_voice_result(expo_runtime, labels, spoken):
    mock_object_completion(expo_runtime, json.dumps({'objects': labels}))
    expo_runtime.tts.backend = 'piper'
    expo_runtime.tts.speak = Mock(return_value=True)
    result = expo_runtime.detect_objects(speak_result=True)
    assert result['objects'] == labels and result['speech_queued'] is True
    expo_runtime.tts.speak.assert_called_once()
    assert expo_runtime.tts.speak.call_args.args == (spoken,)
    assert expo_runtime.tts.speak.call_args.kwargs['valid_when']()


@pytest.mark.parametrize('healthy', [False, True])
def test_speech_unavailable_keeps_successful_visual_result(expo_runtime, healthy):
    mock_object_completion(expo_runtime, '{"objects":["mug"]}')
    expo_runtime.tts.backend = 'piper' if healthy else 'visual'
    expo_runtime.tts.speak = Mock(return_value=False)
    result = expo_runtime.detect_objects(speak_result=True)
    assert result['status'] == 'ok' and result['objects'] == ['mug']
    assert result['speech_queued'] is False


def test_failed_scan_does_not_queue_an_object_announcement(expo_runtime):
    mock_object_completion(expo_runtime, '{"objects":[42]}')
    expo_runtime.tts.speak = Mock()
    assert expo_runtime.detect_objects(speak_result=True)['status'] == 'error'
    expo_runtime.tts.speak.assert_not_called()


@pytest.mark.parametrize('cancel_during_synthesis', [False, True])
def test_stale_queued_speech_is_discarded_without_playback_or_local_fallback(monkeypatch, cancel_during_synthesis):
    service = TTSService()
    service.backend = 'piper'
    valid = threading.Event()
    if cancel_during_synthesis:
        valid.set()
    player = Mock()
    def synthesize(_text):
        valid.clear()
        service._play_locally(player)
    synthesize = Mock(side_effect=synthesize)
    monkeypatch.setattr(service, '_speak', synthesize)
    done = threading.Event()
    service.start()
    try:
        assert service.speak('I see an old object.', done.set, valid_when=valid.is_set)
        assert done.wait(2)
        assert synthesize.call_count == int(cancel_during_synthesis)
        player.assert_not_called()
        assert service.backend == 'piper' and not service.speaking_text
    finally:
        service.stop()


def test_face_caption_is_actual_normal_playback_not_queued_text(expo_runtime, monkeypatch):
    app, _, _, _ = create_app(expo_runtime)
    client = app.test_client()
    tts = expo_runtime.tts
    text = 'I see a red mug and a notebook.'
    assert tts.speak('Waiting in the queue.')
    assert client.get('/face/state').json['transcript'] == ''
    # Remove the deliberately unplayed request before exercising the worker.
    tts._queue.get_nowait()
    seen = []
    def player():
        seen.append(client.get('/face/state').json)
    monkeypatch.setattr(tts, '_speak', lambda words: tts._play_locally(player))
    done = threading.Event()
    tts.start()
    try:
        assert tts.speak(text, done.set)
        assert done.wait(2)
        assert seen == [{'expression': 'speaking', 'transcript': text}]
        assert client.get('/face/state').json == {'expression': 'idle', 'transcript': ''}
    finally:
        tts.stop()


def test_groq_demo_narration_uses_the_same_face_caption_path(expo_runtime, monkeypatch):
    app, _, _, _ = create_app(expo_runtime)
    client = app.test_client()
    tts = expo_runtime.tts
    def synthesize(text, path, **kwargs):
        with wave.open(str(path), 'wb') as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(24000)
            output.writeframes(np.zeros(2400, dtype='<i2').tobytes())
    tts._demo_speech = SimpleNamespace(ready=True, error='', synthesize=synthesize)
    observed = []
    def player():
        observed.append(client.get('/face/state').json['transcript'])
    monkeypatch.setattr(tts, '_play_wav', lambda path: tts._play_locally(player))
    completed = []
    tts._run_demo(DemoSpeech('Hello! I am a prototype.', completed.append, 'operator', threading.Event()))
    assert completed == [True] and observed == ['Hello! I am a prototype.']
    assert not tts.speaking_text


def emit_browser_audio(runtime, client, tmp_path, text):
    path = tmp_path / 'browser.wav'
    path.write_bytes(b'RIFFmock-audio')
    runtime.tts._utterance_text = text
    assert runtime.tts._emit_to_browser(str(path))
    payload = next(event['args'][0] for event in client.get_received() if event['name'] == 'play_audio')
    return payload['audio_id']


def test_browser_captions_follow_clip_ids_even_when_next_reply_is_synthesizing(expo_runtime, tmp_path):
    app, socketio, _, _ = create_app(expo_runtime)
    face = app.test_client()
    operator = socketio.test_client(app)
    operator.get_received()
    first = emit_browser_audio(expo_runtime, operator, tmp_path, 'First spoken reply.')
    assert face.get('/face/state').json['transcript'] == ''
    operator.emit('browser_audio_state', {'playing': True, 'audio_id': first, 'text': 'UNTRUSTED TEXT'})
    assert face.get('/face/state').json['transcript'] == 'First spoken reply.'
    second = emit_browser_audio(expo_runtime, operator, tmp_path, 'Second spoken reply.')
    assert face.get('/face/state').json['transcript'] == 'First spoken reply.'
    operator.emit('browser_audio_state', {'playing': True, 'audio_id': second})
    assert face.get('/face/state').json['transcript'] == 'Second spoken reply.'
    operator.emit('browser_audio_state', {'playing': True, 'audio_id': second, 'finished': True})
    assert face.get('/face/state').json['transcript'] == 'First spoken reply.'
    operator.emit('browser_audio_state', {'playing': False, 'audio_id': first, 'finished': True})
    assert face.get('/face/state').json['transcript'] == ''
    operator.disconnect()


def test_browser_disconnect_clears_caption(expo_runtime, tmp_path):
    app, socketio, _, _ = create_app(expo_runtime)
    operator = socketio.test_client(app)
    operator.get_received()
    audio_id = emit_browser_audio(expo_runtime, operator, tmp_path, 'Hello visitors.')
    operator.emit('browser_audio_state', {'playing': True, 'audio_id': audio_id})
    assert expo_runtime.tts.speaking_text == 'Hello visitors.'
    operator.disconnect()
    assert not expo_runtime.tts.speaking_text


def test_unknown_audio_id_cannot_inject_face_text(expo_runtime):
    app, socketio, _, _ = create_app(expo_runtime)
    operator = socketio.test_client(app)
    operator.emit('browser_audio_state', {'playing': True, 'audio_id': 'unknown', 'text': 'INJECTED'})
    assert app.test_client().get('/face/state').json['transcript'] == ''
    operator.disconnect()


def test_demo_caption_cannot_be_claimed_by_another_dashboard(tmp_path):
    tts = TTSService(audio_output='browser', audio_emit=lambda *args: None)
    tts._utterance_text = 'Hello from the demo.'
    tts._demo_job = DemoSpeech(tts._utterance_text, lambda success: None, 'operator', threading.Event())
    path = tmp_path / 'demo.wav'
    path.write_bytes(b'RIFFmock-audio')
    assert tts._emit_to_browser(str(path))
    audio_id = next(reversed(tts._audio_captions))
    tts.track_browser_audio('visitor', audio_id, True)
    assert not tts.speaking_text
    tts.track_browser_audio('operator', audio_id, True)
    assert tts.speaking_text == 'Hello from the demo.'
    tts.clear_browser_audio('operator')
    assert not tts.speaking_text


def test_estop_face_hides_caption_even_if_current_audio_is_finishing(expo_runtime):
    app, _, _, _ = create_app(expo_runtime)
    expo_runtime.tts._utterance_text = 'An utterance already playing.'
    def player():
        expo_runtime.arbiter.emergency_stop()
        assert app.test_client().get('/face/state').json == {'expression': 'estop', 'transcript': ''}
    expo_runtime.tts._play_locally(player)
