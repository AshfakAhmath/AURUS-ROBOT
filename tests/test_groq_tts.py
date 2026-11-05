"""Mocked demo-only Groq Orpheus tests; no account requests, playback, or GPIO."""

from pathlib import Path
import threading
from types import SimpleNamespace
from unittest.mock import Mock
import wave

import numpy as np
import pytest

from src.services.groq_tts_service import GroqSpeechSynthesizer, MODEL, speech_chunks
from src.services.tts_service import TTSService, DemoSpeech


def write_pcm(path, value=1000, channels=1, rate=24000):
    with wave.open(str(path), "wb") as output:
        output.setnchannels(channels)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(np.full(2400 * channels, value, dtype="<i2").tobytes())


def fake_client():
    create = Mock(return_value=SimpleNamespace(write_to_file=write_pcm))
    return SimpleNamespace(audio=SimpleNamespace(speech=SimpleNamespace(create=create)), close=Mock())


@pytest.mark.parametrize("text", ["Hello everyone!", "Hello. " * 80, "A" * 450, "Line one.\nLine two! " * 30])
def test_split_respects_orpheus_limit_without_losing_text(text):
    chunks = speech_chunks(text)
    assert all(0 < len(chunk) <= 200 for chunk in chunks)
    assert "".join("".join(chunk.split()) for chunk in chunks) == "".join(text.split())


def test_multi_chunk_synthesis_joins_real_wav_and_uses_correct_fields(tmp_path):
    client = fake_client()
    speech = GroqSpeechSynthesizer(client=client)
    path = tmp_path / "speech.wav"
    text = "Hello everyone. Welcome to the Faculty of Information Technology Expo! " * 6
    speech.synthesize(text, path)
    assert client.audio.speech.create.call_count == len(speech_chunks(text))
    for call in client.audio.speech.create.call_args_list:
        assert call.kwargs["model"] == MODEL and call.kwargs["voice"] == "troy"
        assert call.kwargs["response_format"] == "wav" and len(call.kwargs["input"]) <= 200
    with wave.open(str(path), "rb") as source:
        assert source.getframerate() == 24000 and source.getsampwidth() == 2
        assert source.getnframes() > 2400
    assert speech.error == ""


def test_groq_voice_validation_and_missing_key_are_explicit(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    speech = GroqSpeechSynthesizer()
    assert not speech.ready and "GROQ_API_KEY" in speech.error
    assert not GroqSpeechSynthesizer(voice="en-US-GuyNeural", client=fake_client()).ready
    assert not GroqSpeechSynthesizer(model="unavailable-model", client=fake_client()).ready


@pytest.mark.parametrize("status,phrase", [(400, "HTTP 400"), (401, "access denied"), (403, "permissions/terms"), (404, "HTTP 404"), (429, "quota"), (500, "request failed")])
def test_errors_are_actionable_without_exposing_secret_or_input(tmp_path, status, phrase):
    client = fake_client()
    error = RuntimeError("EXAMPLE_SECRET_MUST_NOT_APPEAR")
    error.status_code = status
    client.audio.speech.create.side_effect = error
    speech = GroqSpeechSynthesizer(client=client)
    with pytest.raises(RuntimeError) as caught:
        speech.synthesize("Private user reply", tmp_path / "speech.wav")
    assert phrase in str(caught.value)
    assert "EXAMPLE_SECRET" not in str(caught.value) and "Private user" not in str(caught.value)


def test_invalid_audio_does_not_get_published(tmp_path):
    client = fake_client()
    client.audio.speech.create.return_value.write_to_file = lambda path: Path(path).write_bytes(b"not audio")
    speech = GroqSpeechSynthesizer(client=client)
    destination = tmp_path / "speech.wav"
    with pytest.raises(RuntimeError, match="invalid or incompatible"):
        speech.synthesize("Hello", destination)
    assert not destination.exists()


def test_incompatible_chunks_preserve_previous_recording(tmp_path):
    client = fake_client()
    client.audio.speech.create.side_effect = [
        SimpleNamespace(write_to_file=lambda path: write_pcm(path, rate=24000)),
        SimpleNamespace(write_to_file=lambda path: write_pcm(path, rate=48000)),
    ]
    speech = GroqSpeechSynthesizer(client=client)
    destination = tmp_path / "speech.wav"
    destination.write_bytes(b"previous recording")
    with pytest.raises(RuntimeError, match="invalid or incompatible"):
        speech.synthesize("Hello everyone. " * 20, destination)
    assert destination.read_bytes() == b"previous recording"


def configure_demo_client(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-only-not-a-real-key")
    client = fake_client()
    monkeypatch.setattr("src.services.groq_tts_service.Groq", lambda **kwargs: client)
    return client


def test_normal_tts_restores_previous_selection_despite_stale_groq_settings(monkeypatch, tmp_path):
    monkeypatch.setenv("AURUS_TTS_BACKEND", "groq")
    monkeypatch.setenv("GROQ_TTS_VOICE", "troy")
    monkeypatch.setenv("AURUS_TTS_VOICE", "en-US-GuyNeural")
    monkeypatch.setattr("src.services.tts_service.GroqSpeechSynthesizer", Mock(side_effect=AssertionError("Normal TTS must not initialize Groq")))
    model = tmp_path / "ryan.onnx"
    model.write_bytes(b"model")
    monkeypatch.setattr("src.services.tts_service.importlib.util.find_spec", lambda name: object())
    service = TTSService(model)
    monkeypatch.setattr(service, "_speak_piper", Mock())
    service._speak("Ordinary reply")
    assert service.backend == "piper" and service.voice == "en-US-GuyNeural"
    service._speak_piper.assert_called_once_with("Ordinary reply")
    assert service._demo_speech is None


def test_demo_uses_separate_settings_and_fresh_api_calls_per_replay(monkeypatch):
    client = configure_demo_client(monkeypatch)
    monkeypatch.setenv("AURUS_DEMO_TTS_VOICE", "troy")
    # Obsolete ordinary-cloud configuration cannot override demo-only settings.
    monkeypatch.setenv("GROQ_TTS_MODEL", "obsolete")
    monkeypatch.setenv("GROQ_TTS_VOICE", "invalid")
    service = TTSService(audio_output="pi")
    played = []
    monkeypatch.setattr(service, "_play_wav", lambda path: played.append(Path(path)))
    backend = service.backend
    observed = []
    for _ in range(2):
        service._run_demo(DemoSpeech("Hello from AURUS", observed.append, "operator", threading.Event()))
    assert observed == [True, True] and service.backend == backend
    assert client.audio.speech.create.call_count == 2
    assert all(call.kwargs["voice"] == "troy" for call in client.audio.speech.create.call_args_list)
    assert played[0] != played[1] and all(not path.exists() for path in played)


def test_demo_worker_cloud_failure_does_not_change_normal_backend_and_can_retry(monkeypatch):
    client = configure_demo_client(monkeypatch)
    client.audio.speech.create.side_effect = [RuntimeError("offline"), SimpleNamespace(write_to_file=write_pcm)]
    service = TTSService(audio_output="pi")
    monkeypatch.setattr(service, "_play_wav", Mock())
    monkeypatch.setattr(service, "_speak", Mock(side_effect=AssertionError("No local fallback for demo")))
    backend, original_error = service.backend, service.error
    observed = []
    done = threading.Event()
    def completed(success):
        observed.append(success)
        done.set()
    service.start()
    try:
        assert service.speak_demo("First line", completed, "operator", threading.Event())
        assert done.wait(2) and observed == [False]
        assert service.demo_error and service.backend == backend and service.error == original_error
        done.clear()
        assert service.speak_demo("Next attempt", completed, "operator", threading.Event())
        assert done.wait(2) and observed == [False, True]
        assert not service.demo_error and service.backend == backend
        assert client.audio.speech.create.call_count == 2
        service._speak.assert_not_called()
    finally:
        service.stop()


def test_cancel_during_cloud_request_prevents_playback_and_later_chunks(monkeypatch):
    client = configure_demo_client(monkeypatch)
    cancelled = threading.Event()
    def response(path):
        write_pcm(path)
        cancelled.set()
    client.audio.speech.create.return_value.write_to_file = response
    service = TTSService(audio_output="pi")
    monkeypatch.setattr(service, "_play_wav", Mock())
    observed = []
    service._run_demo(DemoSpeech("Welcome everyone. " * 30, observed.append, "operator", cancelled))
    assert observed == [False] and client.audio.speech.create.call_count == 1
    service._play_wav.assert_not_called()
    assert not service.busy and not service.playback_active.is_set()


def test_model_terms_error_is_actionable_and_redacted(tmp_path):
    client = fake_client()
    error = RuntimeError("EXAMPLE_SECRET_MUST_NOT_APPEAR")
    error.status_code = 400
    error.body = {"error": {"code": "model_terms_required", "message": "EXAMPLE_SECRET_MUST_NOT_APPEAR"}}
    client.audio.speech.create.side_effect = error
    speech = GroqSpeechSynthesizer(client=client)
    with pytest.raises(RuntimeError, match="organization admin") as caught:
        speech.synthesize("Hello", tmp_path / "speech.wav")
    assert "HTTP 400" in str(caught.value) and "EXAMPLE_SECRET" not in str(caught.value)


def test_demo_boost_is_peak_limited_and_separate_from_normal_volume(monkeypatch, tmp_path):
    monkeypatch.setenv("AURUS_DEMO_TTS_GAIN_DB", "12")
    path = tmp_path / "volume.wav"
    write_pcm(path, value=20000)
    TTSService._boost_demo_audio(path)
    with wave.open(str(path), "rb") as source:
        samples = np.frombuffer(source.readframes(source.getnframes()), dtype="<i2")
    assert 28000 < samples.max() < 30000


def test_missing_key_affects_only_demo_readiness(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    service = TTSService()
    backend, original_error = service.backend, service.error
    assert service.speak("Ordinary reply")
    assert not service.prepare_demo_speech() and "GROQ_API_KEY" in service.demo_error
    assert service.backend == backend and service.error == original_error
