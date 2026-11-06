"""Mocked demo-only Groq Orpheus tests; no account requests, playback, or GPIO."""

from pathlib import Path
import threading
from types import SimpleNamespace
from unittest.mock import Mock
import wave

import numpy as np
import pytest

from src.services.demo_script import DEMO_CLIPS
from src.services.groq_tts_service import GroqSpeechSynthesizer, MODEL, demo_recording_path, speech_chunks
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
    client = configure_demo_client(monkeypatch)
    model = tmp_path / "ryan.onnx"
    model.write_bytes(b"model")
    monkeypatch.setattr("src.services.tts_service.importlib.util.find_spec", lambda name: object())
    service = TTSService(model)
    monkeypatch.setattr(service, "_speak_piper", Mock())
    service._speak("Ordinary reply")
    assert service.backend == "piper" and service.voice == "en-US-GuyNeural"
    service._speak_piper.assert_called_once_with("Ordinary reply")
    client.audio.speech.create.assert_not_called()


@pytest.mark.parametrize("old_mode", ["live", "recorded"])
def test_demo_uses_piper_despite_stale_groq_mode_and_recordings(monkeypatch, tmp_path, old_mode):
    client = configure_demo_client(monkeypatch)
    monkeypatch.setenv("AURUS_DEMO_TTS_MODE", old_mode)
    monkeypatch.setenv("AURUS_DEMO_TTS_VOICE", "troy")
    monkeypatch.setattr("src.services.groq_tts_service.DEMO_RECORDINGS_DIR", tmp_path)
    model = tmp_path / "voice.onnx"
    model.touch()
    monkeypatch.setattr("src.services.tts_service.importlib.util.find_spec", lambda name: object())
    write_pcm(demo_recording_path(DEMO_CLIPS["closing"]))
    service = TTSService(model, audio_output="pi")
    render = Mock(side_effect=lambda _text, path: write_pcm(path))
    monkeypatch.setattr(service, "_synthesize_piper", render)
    monkeypatch.setattr(service, "_play_wav", Mock())
    observed = []
    lines = [DEMO_CLIPS["closing"], "Right now, I can see a red mug."]
    for text in lines:
        service._run_demo(DemoSpeech(text, observed.append, "operator", threading.Event()))
    assert observed == [True, True] and service.backend == "piper"
    assert [call.args[0] for call in render.call_args_list] == lines
    client.audio.speech.create.assert_not_called()


def test_demo_worker_local_failure_does_not_change_normal_backend_and_can_retry(monkeypatch):
    service = TTSService(audio_output="pi")
    def render(text, path):
        if text == "First line":
            raise RuntimeError("Piper voice unavailable")
        write_pcm(path)
    monkeypatch.setattr(service, "_offline_demo_backend", lambda: "piper")
    monkeypatch.setattr(service, "_demo_espeak_backend", lambda: "")
    monkeypatch.setattr(service, "_synthesize_piper", Mock(side_effect=render))
    monkeypatch.setattr(service, "_play_wav", Mock())
    monkeypatch.setattr(service, "_speak", Mock(side_effect=AssertionError("Demo must use its own playback path")))
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
        assert service._synthesize_piper.call_count == 2
        service._speak.assert_not_called()
    finally:
        service.stop()


def test_cancel_during_piper_synthesis_prevents_playback(monkeypatch):
    cancelled = threading.Event()
    def render(_text, path):
        write_pcm(path)
        cancelled.set()
    service = TTSService(audio_output="pi")
    monkeypatch.setattr(service, "_offline_demo_backend", lambda: "piper")
    monkeypatch.setattr(service, "_synthesize_piper", render)
    monkeypatch.setattr(service, "_play_wav", Mock())
    observed = []
    service._run_demo(DemoSpeech("Welcome everyone. " * 30, observed.append, "operator", cancelled))
    assert observed == [False]
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
    timeline = np.arange(24000) / 24000
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(24000)
        output.writeframes((20000 * np.sin(2 * np.pi * 220 * timeline)).astype("<i2").tobytes())
    TTSService._boost_demo_audio(path)
    with wave.open(str(path), "rb") as source:
        samples = np.frombuffer(source.readframes(source.getnframes()), dtype="<i2")
    assert 28000 < samples.max() < 30000


def write_signal(path, samples, rate=24000):
    channels = 1 if samples.ndim == 1 else samples.shape[1]
    with wave.open(str(path), "wb") as output:
        output.setnchannels(channels)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(np.rint(samples * 32767).astype("<i2").tobytes())


def read_signal(path):
    with wave.open(str(path), "rb") as source:
        return np.frombuffer(source.readframes(source.getnframes()), dtype="<i2").astype(float) / 32768


def test_compression_increases_average_level_over_old_peak_bounded_boost(tmp_path):
    from src.services.speech_audio import enhance_demo_wav
    timeline = np.arange(48000) / 24000
    envelope = np.where(timeline % 0.5 < 0.05, 0.85, 0.06)
    source = envelope * np.sin(2 * np.pi * 220 * timeline)
    old_gain = min(10 ** (6 / 20), (10 ** (-1 / 20)) / np.max(np.abs(source)))
    previous_rms = np.sqrt(np.mean((source * old_gain) ** 2))
    path = tmp_path / "speech.wav"
    write_signal(path, source)
    enhance_demo_wav(path, gain_db=6)
    result = read_signal(path)
    assert np.sqrt(np.mean(result ** 2)) > previous_rms * 1.15
    assert np.max(np.abs(result)) <= 10 ** (-1 / 20)


def test_equalizer_emphasizes_speech_presence_over_low_bass(tmp_path):
    from src.services.speech_audio import enhance_demo_wav
    timeline = np.arange(24000) / 24000
    source = 0.01 * (np.sin(2 * np.pi * 80 * timeline) + np.sin(2 * np.pi * 2400 * timeline))
    path = tmp_path / "eq.wav"
    write_signal(path, source)
    enhance_demo_wav(path, gain_db=0)
    spectrum = np.abs(np.fft.rfft(read_signal(path)))
    assert spectrum[2400] > spectrum[80] * 2


@pytest.mark.parametrize("channels", [1, 2])
def test_enhancement_preserves_pcm_format_duration_and_stereo_balance(tmp_path, channels):
    from src.services.speech_audio import enhance_demo_wav
    timeline = np.arange(16000) / 16000
    mono = 0.1 * np.sin(2 * np.pi * 440 * timeline)
    source = mono if channels == 1 else np.column_stack((mono, mono * 0.5))
    path = tmp_path / "format.wav"
    write_signal(path, source, rate=16000)
    enhance_demo_wav(path)
    with wave.open(str(path), "rb") as result:
        assert (result.getnchannels(), result.getsampwidth(), result.getframerate(), result.getnframes()) == (channels, 2, 16000, 16000)
    if channels == 2:
        signal = read_signal(path).reshape(-1, 2)
        assert np.sqrt(np.mean(signal[:, 0] ** 2)) / np.sqrt(np.mean(signal[:, 1] ** 2)) == pytest.approx(2, abs=0.01)


def test_enhancement_keeps_silence_silent(tmp_path):
    from src.services.speech_audio import enhance_demo_wav
    path = tmp_path / "silence.wav"
    write_signal(path, np.zeros(2400))
    enhance_demo_wav(path)
    assert not np.any(read_signal(path))


def test_demo_check_command_synthesizes_plays_and_discards_audio(monkeypatch, capsys):
    from scripts import check_demo_tts
    client = configure_demo_client(monkeypatch)
    monkeypatch.setattr("sys.argv", ["check_demo_tts.py", "--play", "--device", "plughw:2,0"])
    monkeypatch.setattr(check_demo_tts.shutil, "which", lambda name: "fake-aplay")
    played = []
    def play(command, **kwargs):
        assert command[:4] == ["fake-aplay", "-q", "-D", "plughw:2,0"]
        path = Path(command[-1])
        assert path.exists()
        played.append(path)
    monkeypatch.setattr(check_demo_tts.subprocess, "run", play)
    assert check_demo_tts.main() == 0
    assert client.audio.speech.create.call_count >= 2
    assert played and not played[0].exists()
    assert "SUCCESS" in capsys.readouterr().out


def test_demo_check_command_reports_failure_without_playback(monkeypatch, capsys):
    from scripts import check_demo_tts
    client = configure_demo_client(monkeypatch)
    client.audio.speech.create.side_effect = RuntimeError("EXAMPLE_SECRET_MUST_NOT_APPEAR")
    monkeypatch.setattr("sys.argv", ["check_demo_tts.py", "--play"])
    play = Mock()
    monkeypatch.setattr(check_demo_tts.subprocess, "run", play)
    assert check_demo_tts.main() == 1
    play.assert_not_called()
    assert "EXAMPLE_SECRET" not in capsys.readouterr().err


def test_demo_readiness_depends_on_local_voice(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    service = TTSService()
    backend, original_error = service.backend, service.error
    assert service.speak("Ordinary reply")
    monkeypatch.setattr(service, "_offline_demo_backend", lambda: "")
    assert not service.prepare_demo_speech() and "PIPER_MODEL_PATH" in service.demo_error
    monkeypatch.setattr(service, "_offline_demo_backend", lambda: "piper")
    assert service.prepare_demo_speech() and not service.demo_error
    assert service.backend == backend and service.error == original_error


def test_prepare_resumes_after_rate_limit_and_reuses_completed_recordings(monkeypatch, tmp_path):
    from scripts import check_demo_tts

    client = configure_demo_client(monkeypatch)
    monkeypatch.setattr("src.services.groq_tts_service.DEMO_RECORDINGS_DIR", tmp_path)
    monkeypatch.setattr(check_demo_tts, "DEMO_CLIPS", {"first": "Hello visitors.", "second": "Welcome to the FIT Expo."})
    monkeypatch.setattr("sys.argv", ["check_demo_tts.py", "--prepare"])
    first = demo_recording_path("Hello visitors.")
    second = demo_recording_path("Welcome to the FIT Expo.")
    create = client.audio.speech.create
    rate_limit = RuntimeError("rate limit")
    rate_limit.status_code = 429
    create.side_effect = [create.return_value, rate_limit]
    assert check_demo_tts.main() == 1
    assert first.is_file() and not second.exists()

    create.side_effect = None
    create.reset_mock()
    assert check_demo_tts.main() == 0
    assert second.is_file()
    create.assert_called_once()
    assert create.call_args.kwargs["input"] == "Welcome to the FIT Expo."

    monkeypatch.setattr(check_demo_tts, "GroqSpeechSynthesizer", Mock(side_effect=AssertionError("Recordings are complete")))
    assert check_demo_tts.main() == 0


def test_demo_uses_espeak_if_piper_fails(monkeypatch):
    service = TTSService(audio_output="pi")
    monkeypatch.setattr(service, "_offline_demo_backend", lambda: "piper")
    monkeypatch.setattr(service, "_demo_espeak_backend", lambda: "espeak-ng")
    monkeypatch.setattr(service, "_synthesize_piper", Mock(side_effect=RuntimeError("Piper load failed")))
    def render(command, **_kwargs):
        assert command[:2] == ["espeak-ng", "-w"]
        write_pcm(command[2])
    monkeypatch.setattr("src.services.tts_service.subprocess.run", Mock(side_effect=render))
    monkeypatch.setattr(service, "_play_wav", Mock())
    completed = []
    service._run_demo(DemoSpeech(DEMO_CLIPS["closing"], completed.append, "operator", threading.Event()))
    assert completed == [True]
    service._play_wav.assert_called_once()
