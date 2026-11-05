"""Render FIT Expo clips without playback/GPIO, then normalize and cache PCM WAV.

Run on Windows: python scripts/prepare_demo_audio.py
The resulting assets travel with the repository and play offline on the Pi.
"""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import wave

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.services.demo_script import DEMO_CLIPS, DEMO_ORDER


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--voice", default="Microsoft Zira Desktop")
    parser.add_argument("--rate", type=int, default=-1, choices=range(-3, 2))
    args = parser.parse_args()
    destination = ROOT / "assets" / "fit_expo"
    destination.mkdir(parents=True, exist_ok=True)
    manifest = {"engine": "Windows System.Speech", "voice": args.voice, "rate": args.rate,
                "peak_dbfs": -1.0, "clips": {}}
    with tempfile.TemporaryDirectory(prefix="aurus-demo-render-") as scratch:
        scratch = Path(scratch)
        request = {"voice": args.voice, "rate": args.rate, "clips": [
            {"path": str(scratch / (key + ".wav")),
             "text": text.replace("FIT Expo", "F. I. T. Expo").replace("Groq-powered", "Grock powered")}
            for key, text in DEMO_CLIPS.items()
        ]}
        spec = scratch / "request.json"
        spec.write_text(json.dumps(request), encoding="utf-8")
        renderer = scratch / "render.ps1"
        renderer.write_text('''param([string]$RequestFile)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$spec = Get-Content -LiteralPath $RequestFile -Raw | ConvertFrom-Json
$voice = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    $voice.SelectVoice($spec.voice)
    $voice.Rate = $spec.rate
    $voice.Volume = 100
    foreach ($clip in $spec.clips) {
        $voice.SetOutputToWaveFile($clip.path)
        $voice.Speak($clip.text)
        $voice.SetOutputToNull()
    }
} finally { $voice.Dispose() }
''', encoding="utf-8")
        subprocess.run(["powershell.exe", "-NoProfile", "-File", str(renderer), str(spec)], check=True, timeout=180)
        pcm_clips = {}
        sample_rate = None
        for key, text in DEMO_CLIPS.items():
            with wave.open(str(scratch / (key + ".wav")), "rb") as source:
                if source.getsampwidth() != 2 or source.getnchannels() != 1:
                    raise ValueError("Renderer must produce mono 16-bit PCM")
                rate = source.getframerate()
                samples = np.frombuffer(source.readframes(source.getnframes()), dtype="<i2").astype(np.float64)
            if sample_rate is not None and rate != sample_rate:
                raise ValueError("Inconsistent sample rates")
            sample_rate = rate
            samples -= samples.mean()
            peak = float(np.abs(samples).max())
            if peak == 0:
                raise ValueError(f"Renderer produced silence for {key}")
            samples *= (32767 * 10 ** (-1 / 20)) / peak
            pcm = np.rint(samples).astype("<i2").tobytes()
            pcm_clips[key] = pcm
            path = destination / (key + ".wav")
            write_wav(path, rate, pcm)
            manifest["clips"][key] = {"text": text, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                                      "duration_s": round(len(pcm) / 2 / rate, 3)}
        silence = bytes(int(sample_rate * 0.75) * 2)
        preview = silence.join(pcm_clips[key] for key in DEMO_ORDER)
        write_wav(destination / "fit_expo_preview.wav", sample_rate, preview)
        manifest["preview_duration_s"] = round(len(preview) / 2 / sample_rate, 3)
        (destination / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Prepared {len(DEMO_CLIPS)} cached clips; preview {manifest['preview_duration_s']} seconds. No audio played.")


def write_wav(path, sample_rate, pcm):
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(pcm)


if __name__ == "__main__":
    main()
