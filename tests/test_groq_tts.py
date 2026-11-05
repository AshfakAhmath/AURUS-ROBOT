"""Mocked Groq Orpheus tests; no account requests, playback, or GPIO."""

from pathlib import Path
import threading
from types import SimpleNamespace
from unittest.mock import Mock
import wave

import numpy as np
import pytest

from src.services.groq_tts_service import GroqSpeechSynthesizer, MODEL, speech_chunks
from src.services.tts_service import TTSService, CachedPlayback


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


@pytest.mark.parametrize("status,phrase", [(401, "access denied"), (403, "permissions/terms"), (429, "quota"), (500, "request failed")])
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


def configure_groq(monkeypatch):
    monkeypatch.setenv("AURUS_TTS_BACKEND", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "test-only-not-a-real-key")
    client = fake_client()
    monkeypatch.setattr("src.services.groq_tts_service.Groq", lambda **kwargs: client)
    return client


def test_explicit_groq_overrides_available_piper_and_routes_pcm(monkeypatch, tmp_path):
    configure_groq(monkeypatch)
    model = tmp_path / "ryan.onnx"
    model.write_bytes(b"model")
    monkeypatch.setattr("src.services.tts_service.importlib.util.find_spec", lambda name: object())
    service = TTSService(model, audio_output="browser", volume_percent=0, gain_db=0)
    emitted = []
    service.set_audio_emit(lambda event, data: emitted.append((event, data)))
    service._speak("Hello from AURUS")
    assert service.backend == "groq" and service.voice == "troy" and service.model_name == MODEL
    assert emitted[0][0] == "play_audio" and emitted[0][1]["format"] == "wav"
    assert service.active_output == "browser"


def test_failed_groq_never_switches_to_local_voice_and_can_retry(monkeypatch):
    client = configure_groq(monkeypatch)
    client.audio.speech.create.side_effect = [RuntimeError("offline"), SimpleNamespace(write_to_file=write_pcm)]
    service = TTSService(audio_output="browser", audio_emit=lambda *args: None)
    done = threading.Event()
    service.start()
    try:
        assert service.speak("First reply", done.set)
        assert done.wait(2)
        assert service.backend == "groq" and service.error and not service.healthy
        done.clear()
        assert service.speak("Second reply", done.set)
        assert done.wait(2)
        assert service.backend == "groq" and service.healthy
        assert client.audio.speech.create.call_count == 2
    finally:
        service.stop()


def test_cached_demo_replay_does_not_call_groq(monkeypatch):
    client = configure_groq(monkeypatch)
    service = TTSService(audio_output="pi")
    monkeypatch.setattr(service, "_play_wav", Mock())
    completed = []
    cache = Path(__file__).resolve().parents[1] / "assets" / "fit_expo" / "closing.wav"
    service._run_cached(CachedPlayback(cache, completed.append, "operator", threading.Event()))
    assert completed == [True]
    client.audio.speech.create.assert_not_called()


def test_large_gain_is_limited_before_pcm_clipping(tmp_path):
    path = tmp_path / "volume.wav"
    write_pcm(path, value=20000)
    service = TTSService(volume_percent=100, gain_db=12)
    service._amplify_wav(str(path))
    with wave.open(str(path), "rb") as source:
        samples = np.frombuffer(source.readframes(source.getnframes()), dtype="<i2")
    assert 28000 < samples.max() < 30000


def test_missing_key_rejects_ordinary_queue_but_keeps_cached_support(monkeypatch):
    monkeypatch.setenv("AURUS_TTS_BACKEND", "groq")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    service = TTSService()
    assert service.backend == "groq" and not service.healthy
    assert not service.speak("Hello")


def test_failed_groq_demo_preparation_preserves_working_cache(monkeypatch, tmp_path):
    import asyncio
    from scripts import prepare_demo_audio

    destination = tmp_path / "assets" / "fit_expo"
    destination.mkdir(parents=True)
    (destination / "introduction.wav").write_bytes(b"working audio")
    (destination / "manifest.json").write_bytes(b"working manifest")
    previous = {path.name: path.read_bytes() for path in destination.iterdir()}
    speech = SimpleNamespace(ready=True, model=MODEL, synthesize=Mock(side_effect=RuntimeError("Cloud unavailable")))
    monkeypatch.setattr("src.services.groq_tts_service.GroqSpeechSynthesizer", lambda **kwargs: speech)
    monkeypatch.setattr(prepare_demo_audio, "ROOT", tmp_path)
    monkeypatch.setattr(prepare_demo_audio.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(prepare_demo_audio, "analyse", lambda *args: {"input_i": "-12"})

    async def no_wait(*args):
        pass

    monkeypatch.setattr(prepare_demo_audio.asyncio, "sleep", no_wait)
    args = SimpleNamespace(provider="groq", model=MODEL, voice="troy", rate="-8%")
    with pytest.raises(RuntimeError, match="Cloud unavailable"):
        asyncio.run(prepare_demo_audio.prepare(args, "unused-ffmpeg"))
    assert {path.name: path.read_bytes() for path in destination.iterdir()} == previous
