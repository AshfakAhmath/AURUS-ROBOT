"""Download free runtime models from their official distribution locations."""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile


ROOT = Path(__file__).resolve().parents[1]
MODELS = ROOT / "models"

FILES = {
    "face_detection_yunet_2023mar.onnx": "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
    "face_recognition_sface_2021dec.onnx": "https://github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx",
    # Pi OS OpenCV may expose CascadeClassifier without installing its XML
    # data package. Keep an application-local detector for YuNet fallback.
    "haarcascade_frontalface_default.xml": "https://raw.githubusercontent.com/opencv/opencv/4.x/data/haarcascades/haarcascade_frontalface_default.xml",
}
VOSK_URL = "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip"
KWS_NAME = "sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01"
KWS_URL = f"https://github.com/k2-fsa/sherpa-onnx/releases/download/kws-models/{KWS_NAME}.tar.bz2"
VAD_URL = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx"
PIPER_VOICE = "en_US-lessac-medium"

# Boost full phrases more than the bare name; keep common homophones behind a greeting.
WAKE_KEYWORDS = (
    ("HEY AURUS", 2.0, 0.18),
    ("AURUS", 1.5, 0.25),
    ("HEY AURIS", 2.0, 0.18),
    ("HEY ORIS", 2.0, 0.18),
    ("HI AURUS", 2.0, 0.18),
    ("HELLO AURUS", 2.0, 0.18),
    ("HEY ARIES", 2.0, 0.23),
    ("HEY OR US", 2.0, 0.23),
    ("HI OR US", 2.0, 0.23),
    ("HELLO OR US", 2.0, 0.23),
)


def download(url: str, destination: Path) -> None:
    if destination.exists() and destination.stat().st_size > 1024:
        print(f"Present: {destination.name}")
        return
    temporary = destination.with_suffix(destination.suffix + ".part")
    print(f"Downloading {destination.name} …")
    with urllib.request.urlopen(url, timeout=60) as response, temporary.open("wb") as output:
        shutil.copyfileobj(response, output)
    temporary.replace(destination)


def safe_extract_zip(archive: zipfile.ZipFile, destination: Path) -> None:
    root = destination.resolve()
    for member in archive.infolist():
        target = (destination / member.filename).resolve()
        if root not in target.parents and target != root:
            raise RuntimeError(f"unsafe archive entry: {member.filename}")
    archive.extractall(destination)


def safe_extract_tar(archive: tarfile.TarFile, destination: Path) -> None:
    root = destination.resolve()
    for member in archive.getmembers():
        target = (destination / member.name).resolve()
        if root not in target.parents and target != root:
            raise RuntimeError(f"unsafe archive entry: {member.name}")
        if member.issym() or member.islnk() or member.isdev():
            raise RuntimeError(f"unsupported archive entry: {member.name}")
    archive.extractall(destination)


def create_wake_keyword(model_dir: Path) -> None:
    output = model_dir / "aurus_keywords.txt"
    raw = model_dir / "aurus_keywords_raw.txt"
    try:
        from sentencepiece import SentencePieceProcessor
    except ImportError as exc:
        raise RuntimeError("Install sentencepiece from requirements before setting up wake words") from exc

    tokenizer = SentencePieceProcessor(model_file=str(model_dir / "bpe.model"))
    definitions, encoded = [], []
    for phrase, boost, threshold in WAKE_KEYWORDS:
        settings = f":{boost} #{threshold} @{phrase.replace(' ', '_')}"
        definitions.append(f"{phrase} {settings}\n")
        pieces = " ".join(tokenizer.encode(phrase, out_type=str))
        encoded.append(f"{pieces} {settings}\n")

    output_text = "".join(encoded)
    if output.is_file() and output.read_text(encoding="utf-8") == output_text:
        print(f"Present: {output.name}")
    else:
        temporary = output.with_suffix(".txt.part")
        temporary.write_text(output_text, encoding="utf-8")
        temporary.replace(output)
        print("Created AURUS wake-word tokens.")
    raw.write_text("".join(definitions), encoding="utf-8")


def setup_wake_model() -> None:
    kws_dir = MODELS / KWS_NAME
    if not kws_dir.exists():
        archive_path = MODELS / f"{KWS_NAME}.tar.bz2"
        download(KWS_URL, archive_path)
        print("Extracting wake-word model …")
        with tarfile.open(archive_path, "r:bz2") as archive:
            safe_extract_tar(archive, MODELS)
        archive_path.unlink(missing_ok=True)
    create_wake_keyword(kws_dir)


def download_piper_voice() -> None:
    model = MODELS / f"{PIPER_VOICE}.onnx"
    config = MODELS / f"{PIPER_VOICE}.onnx.json"
    if model.is_file() and config.is_file():
        print(f"Present: {model.name}")
        return
    subprocess.run(
        [sys.executable, "-m", "piper.download_voices", PIPER_VOICE, "--data-dir", str(MODELS)],
        check=True,
        timeout=600,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wake-only", action="store_true", help="Install or refresh just the wake-word model and keywords")
    args = parser.parse_args()
    MODELS.mkdir(parents=True, exist_ok=True)
    if args.wake_only:
        setup_wake_model()
        return 0
    for filename, url in FILES.items():
        download(url, MODELS / filename)
    vosk_dir = MODELS / "vosk-model-small-en-us-0.15"
    if not vosk_dir.exists():
        archive_path = MODELS / "vosk-model-small-en-us-0.15.zip"
        download(VOSK_URL, archive_path)
        print("Extracting Vosk model …")
        with zipfile.ZipFile(archive_path) as archive:
            safe_extract_zip(archive, MODELS)
        archive_path.unlink(missing_ok=True)

    setup_wake_model()
    download(VAD_URL, MODELS / "silero_vad.onnx")
    download_piper_voice()
    print("Vision, wake-word, VAD, STT, and TTS models are ready.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
