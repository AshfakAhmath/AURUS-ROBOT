"""Generate cloud neural FIT Expo narration and master it for small speakers.

Build-time only: edge-tts plus FFmpeg (PATH, --ffmpeg, or imageio-ffmpeg).
Replay on the Pi uses the prepared PCM WAV cache without these build tools.
There is deliberately no local-synthesis fallback.
"""

import argparse
import asyncio
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import wave

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.services.demo_script import DEMO_CLIPS, DEMO_ORDER, DemoAudioCache

TARGET_LUFS = -12.0
TRUE_PEAK_DBTP = -1.5
LOUDNESS_TOLERANCE_LU = 1.5
# Reduce bass load and emphasize consonants; moderate compression lifts quiet
# speech without forcing loud peaks into clipping. Apply only when preparing.
SPEECH_FILTERS = (
    "highpass=f=100,lowpass=f=7500,"
    "equalizer=f=2200:t=q:w=1:g=3,"
    "acompressor=threshold=0.03:ratio=3:attack=3:release=100:makeup=3,"
    "alimiter=limit=0.125:level=false:attack=5:release=100:latency=true"
)
FINAL_LIMITER = "aresample=192000,alimiter=limit=0.794328:level=false:attack=5:release=100:latency=true,aresample=48000"


def ffmpeg_path(configured=None):
    if configured:
        path = str(Path(configured).resolve())
    else:
        path = shutil.which("ffmpeg")
        if not path:
            try:
                import imageio_ffmpeg
                path = imageio_ffmpeg.get_ffmpeg_exe()
            except ImportError:
                raise RuntimeError("Preparing cloud audio requires FFmpeg; pass --ffmpeg or install the build-only imageio-ffmpeg package") from None
    return path


def analyse(ffmpeg, path, prefix=""):
    filters = prefix + ("," if prefix else "") + f"loudnorm=I={TARGET_LUFS}:TP={TRUE_PEAK_DBTP}:LRA=5:print_format=json"
    result = subprocess.run(
        [ffmpeg, "-hide_banner", "-nostdin", "-i", str(path), "-af", filters, "-f", "null", "-"],
        capture_output=True, text=True, check=True, timeout=60,
    )
    start = result.stderr.rfind("{")
    if start < 0:
        raise ValueError("FFmpeg returned no loudness measurements")
    measurements, _ = json.JSONDecoder().raw_decode(result.stderr[start:])
    if not all(math.isfinite(float(measurements[key])) for key in ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")):
        raise ValueError("Synthesis produced silent or invalid audio")
    return measurements


def master(ffmpeg, source, destination):
    first = analyse(ffmpeg, source, SPEECH_FILTERS)
    normalizer = (
        f"loudnorm=I={TARGET_LUFS}:TP={TRUE_PEAK_DBTP}:LRA=5:"
        f"measured_I={first['input_i']}:measured_TP={first['input_tp']}:"
        f"measured_LRA={first['input_lra']}:measured_thresh={first['input_thresh']}:"
        f"offset={first['target_offset']}:linear=true"
    )
    subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", str(source),
         "-af", SPEECH_FILTERS + "," + normalizer + "," + FINAL_LIMITER, "-ac", "1", "-ar", "48000", "-c:a", "pcm_s16le", str(destination)],
        capture_output=True, text=True, check=True, timeout=60,
    )
    level = analyse(ffmpeg, destination)
    if abs(float(level["input_i"]) - TARGET_LUFS) > LOUDNESS_TOLERANCE_LU or float(level["input_tp"]) > -0.8:
        raise ValueError(f"Mastered clip failed loudness/peak checks: {destination.name}, {level['input_i']} LUFS, {level['input_tp']} dBTP")
    return level


def pcm(path):
    with wave.open(str(path), "rb") as source:
        if source.getnchannels() != 1 or source.getsampwidth() != 2:
            raise ValueError("Prepared clips must be mono 16-bit PCM")
        rate = source.getframerate()
        frames = source.readframes(source.getnframes())
    samples = np.frombuffer(frames, dtype="<i2").astype(np.float64)
    if not len(samples) or not np.any(samples) or np.any(np.abs(samples) >= 32767):
        raise ValueError("Prepared recording is empty, silent or clipped")
    return rate, frames, round(20 * math.log10(float(np.sqrt(np.mean((samples / 32768) ** 2)))), 2)


def write_wav(path, rate, frames):
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(frames)


async def prepare(args, ffmpeg):
    try:
        import edge_tts
    except ImportError:
        raise RuntimeError("Install the existing edge-tts dependency in the audio preparation environment") from None
    destination = ROOT / "assets" / "fit_expo"
    manifest = {
        "engine": "Microsoft Edge online neural TTS", "voice": args.voice, "rate": args.rate,
        "mastering": {"target_lufs": TARGET_LUFS, "loudness_tolerance_lu": LOUDNESS_TOLERANCE_LU,
                      "true_peak_limit_dbtp": TRUE_PEAK_DBTP,
                      "loudness_range_lu": 5, "filters": SPEECH_FILTERS,
                      "final_limiter": FINAL_LIMITER, "normalization": "two-pass EBU R128"},
        "clips": {},
    }
    # Prepare and verify everything before replacing any working cache files.
    with tempfile.TemporaryDirectory(prefix="aurus-cloud-demo-") as scratch:
        scratch = Path(scratch)
        source_cache = Path(tempfile.gettempdir()) / "aurus-fit-expo-neural-sources"
        source_cache.mkdir(parents=True, exist_ok=True)
        rendered = {}
        for key, text in DEMO_CLIPS.items():
            previous = destination / (key + ".wav")
            previous_level = float(analyse(ffmpeg, previous)["input_i"]) if previous.exists() else None
            spoken = text.replace("FIT Expo", "F. I. T. Expo").replace("Groq-powered", "Grock powered").replace("TechTitans", "Tech Titans")
            raw = scratch / (key + ".mp3")
            source_key = hashlib.sha256(json.dumps([spoken, args.voice, args.rate]).encode()).hexdigest()
            cached_source = source_cache / (source_key + ".mp3")
            if cached_source.is_file():
                print(f"Reusing cloud source: {key}", flush=True)
                shutil.copyfile(cached_source, raw)
            else:
                print(f"Generating cloud voice: {key}", flush=True)
                for attempt in range(3):
                    try:
                        await edge_tts.Communicate(spoken, args.voice, rate=args.rate, volume="+0%", receive_timeout=45).save(str(raw))
                        break
                    except Exception:
                        if attempt == 2:
                            raise
                        await asyncio.sleep(1 + attempt)
                shutil.copyfile(raw, cached_source)
            path = scratch / (key + ".wav")
            level = master(ffmpeg, raw, path)
            sample_rate, frames, rms = pcm(path)
            rendered[key] = frames
            manifest["clips"][key] = {
                "text": text, "synthesis_text": spoken, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "duration_s": round(len(frames) / 2 / sample_rate, 3),
                "integrated_lufs": float(level["input_i"]), "true_peak_dbtp": float(level["input_tp"]),
                "rms_dbfs": rms, "previous_integrated_lufs": previous_level,
            }
            print(f"  {manifest['clips'][key]['duration_s']} s; {level['input_i']} LUFS; peak {level['input_tp']} dBTP", flush=True)
        silence = bytes(int(sample_rate * 0.75) * 2)
        preview = silence.join(rendered[key] for key in DEMO_ORDER)
        write_wav(scratch / "fit_expo_preview.wav", sample_rate, preview)
        manifest["preview_duration_s"] = round(len(preview) / 2 / sample_rate, 3)
        (scratch / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        DemoAudioCache(scratch).validate()
        destination.mkdir(parents=True, exist_ok=True)
        for key in (*DEMO_CLIPS, "fit_expo_preview"):
            shutil.copyfile(scratch / (key + ".wav"), destination / (key + ".wav"))
        # Manifest is published last: interrupted updates cannot appear valid.
        shutil.copyfile(scratch / "manifest.json", destination / "manifest.json")
    print(f"Prepared {len(DEMO_CLIPS)} cloud clips. Preview: {manifest['preview_duration_s']} seconds. No audio played.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--voice", default="en-US-GuyNeural")
    parser.add_argument("--rate", default="-8%")
    parser.add_argument("--ffmpeg", help="Optional FFmpeg executable path")
    args = parser.parse_args()
    asyncio.run(prepare(args, ffmpeg_path(args.ffmpeg)))


if __name__ == "__main__":
    main()
