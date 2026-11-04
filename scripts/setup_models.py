"""Download free runtime models from their official distribution locations."""

from __future__ import annotations

from pathlib import Path
import importlib.util
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
    definitions = (
        "HEY AURUS :2.0 #0.18 @HEY_AURUS\n"
        "AURUS :2.0 #0.18 @AURUS\n"
        "HEY AURIS :2.0 #0.18 @HEY_AURIS\n"
        "HEY ORIS :2.0 #0.18 @HEY_ORIS\n"
    )
    output_text = output.read_text(encoding="utf-8") if output.is_file() else ""
    expected_labels = ("@HEY_AURUS", "@AURUS", "@HEY_AURIS", "@HEY_ORIS")
    if (
        output.is_file()
        and output.stat().st_size > 10
        and all(label in output_text for label in expected_labels)
        and raw.is_file()
        and raw.read_text(encoding="utf-8") == definitions
    ):
        print(f"Present: {output.name}")
        return
    command = shutil.which("sherpa-onnx-cli")
    if not command:
        raise RuntimeError("sherpa-onnx-cli is missing; install requirements before model setup")
    missing_tokenizers = [
        package for package in ("sentencepiece",) if importlib.util.find_spec(package) is None
    ]
    if missing_tokenizers:
        packages = " ".join(f"'{package}'" for package in missing_tokenizers)
        raise RuntimeError(
            "sherpa-onnx requires its tokenizer helpers to create the AURUS wake-word tokens; "
            f"run: {sys.executable} -m pip install {packages}"
        )
    raw.write_text(definitions, encoding="utf-8")
    subprocess.run(
        [
            command,
            "text2token",
            "--tokens",
            str(model_dir / "tokens.txt"),
            "--tokens-type",
            "bpe",
            "--bpe-model",
            str(model_dir / "bpe.model"),
            str(raw),
            str(output),
        ],
        check=True,
        timeout=30,
    )
    print("Created AURUS wake-word tokens.")


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
    MODELS.mkdir(parents=True, exist_ok=True)
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

    kws_dir = MODELS / KWS_NAME
    if not kws_dir.exists():
        archive_path = MODELS / f"{KWS_NAME}.tar.bz2"
        download(KWS_URL, archive_path)
        print("Extracting wake-word model …")
        with tarfile.open(archive_path, "r:bz2") as archive:
            safe_extract_tar(archive, MODELS)
        archive_path.unlink(missing_ok=True)
    create_wake_keyword(kws_dir)

    download(VAD_URL, MODELS / "silero_vad.onnx")
    download_piper_voice()
    print("Vision, wake-word, VAD, STT, and TTS models are ready.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
