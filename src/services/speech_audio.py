"""Small-speaker speech EQ and compression using the existing NumPy dependency."""

from pathlib import Path
import wave

import numpy as np


def enhance_demo_wav(path: str | Path, gain_db: float = 6.0) -> None:
    """Raise average speech level while keeping PCM peaks below -1 dBFS.

    Reduce low bass, add a broad 2.4 kHz presence lift, compress the speech
    envelope, then restore level with bounded make-up gain. Silence stays silent.
    This operates on a complete temporary utterance, never on microphone input.
    """
    with wave.open(str(path), "rb") as source:
        params = source.getparams()
        if params.sampwidth != 2 or params.nchannels not in (1, 2):
            raise ValueError("Demo enhancement requires mono/stereo 16-bit PCM WAV")
        raw = source.readframes(params.nframes)
    samples = np.frombuffer(raw, dtype="<i2").astype(np.float64)
    if not samples.size or not np.any(samples):
        return
    audio = samples.reshape(-1, params.nchannels) / 32768.0
    audio -= np.mean(audio, axis=0, keepdims=True)
    reference_rms = float(np.sqrt(np.mean(audio * audio)))
    rate = params.framerate

    # Smooth frequency response and reflected padding avoid abrupt EQ edges.
    padding = min(len(audio) - 1, max(1, int(rate * 0.02)))
    padded = np.pad(audio, ((padding, padding), (0, 0)), mode="reflect")
    fft_size = 1 << (len(padded) - 1).bit_length()
    frequencies = np.fft.rfftfreq(fft_size, 1.0 / rate)
    safe_frequencies = np.maximum(frequencies, 1e-6)
    bass_cut = 1.0 / np.sqrt(1.0 + (100.0 / safe_frequencies) ** 4)
    presence_db = 3.0 * np.exp(-0.5 * (np.log2(safe_frequencies / 2400.0) / 0.7) ** 2)
    treble_cut = 1.0 / np.sqrt(1.0 + (frequencies / min(8000.0, rate * 0.45)) ** 8)
    response = bass_cut * (10.0 ** (presence_db / 20.0)) * treble_cut
    response[0] = 0.0
    spectrum = np.fft.rfft(padded, n=fft_size, axis=0)
    equalized = np.fft.irfft(spectrum * response[:, None], n=fft_size, axis=0)
    equalized = equalized[padding:padding + len(audio)]

    # Linked stereo RMS detector, 3:1 ratio, 6 dB knee, fast attack/slow release.
    block = max(1, int(rate * 0.01))
    block_count = (len(audio) + block - 1) // block
    detector = np.pad(equalized, ((0, block_count * block - len(audio)), (0, 0)))
    rms = np.sqrt(np.mean(detector.reshape(block_count, block, params.nchannels) ** 2, axis=(1, 2)))
    over = 20.0 * np.log10(np.maximum(rms, 1e-8)) + 24.0
    reduction_db = (1.0 - 1.0 / 3.0) * np.where(
        over < -3.0, 0.0, np.where(over <= 3.0, (over + 3.0) ** 2 / 12.0, over),
    )
    target = 10.0 ** (-reduction_db / 20.0)
    # The complete utterance is available: start attenuation 20 ms before a
    # loud block, so its leading transient cannot force the final peak guard
    # to turn down the entire recording.
    lookahead = np.pad(target, (0, 2), mode="edge")
    target = np.minimum.reduce((lookahead[:-2], lookahead[1:-1], lookahead[2:]))
    envelope = np.empty_like(target)
    previous = float(target[0])
    for index, desired in enumerate(target):
        seconds = 0.005 if desired < previous else 0.08
        smoothing = np.exp(-block / (rate * seconds))
        previous = smoothing * previous + (1.0 - smoothing) * desired
        envelope[index] = previous
    centers = np.arange(block_count) * block + block / 2.0
    gains = np.interp(np.arange(len(audio)), centers, envelope)
    processed = equalized * gains[:, None]

    # Request the configured increase over the original RMS, then constrain the
    # whole waveform to leave peak headroom rather than hard-clipping samples.
    processed_rms = float(np.sqrt(np.mean(processed * processed)))
    if processed_rms > 1e-10:
        processed *= reference_rms * (10.0 ** (gain_db / 20.0)) / processed_rms
    peak = float(np.max(np.abs(processed)))
    ceiling = 10.0 ** (-1.0 / 20.0)
    if peak > ceiling:
        processed *= ceiling / peak
    output_samples = np.rint(processed * 32767).astype("<i2")
    with wave.open(str(path), "wb") as output:
        output.setparams(params)
        output.writeframes(output_samples.tobytes())
