# AURUS Evaluation Edition

AURUS (AUtonomous Robotic Ubiquitous System) is an AI companion rover built for the Raspberry Pi 4B. Developed by the TechTitans team, it combines persistent face identity, guarded autonomous exploration, and Groq-hosted intelligence.

The existing verified motor directions, GPIO pin assignments, ultrasonic wiring, and mecanum kinematics are intentionally preserved.

## Evaluation Capabilities

- **Agentic Routing**: Routes obvious safety and motion intents locally, uses Groq for ambiguous classification, and sends actions through the guarded MCP tool loop.
- **Autonomous Vision**: While exploring, it takes a photo every 10–12 seconds and uses a current Groq-hosted vision model to narrate its surroundings.
- **Speech-to-Text**: Uses Groq Whisper Large V3 Turbo, with lazy Vosk fallback when the local model is installed.
- **Realistic TTS**: Integrated with Microsoft Edge's Read Aloud Neural TTS for nuanced, incredibly realistic robot voices (falling back to Piper/eSpeak if offline).
- **Face Recognition**: Enroll and recognize a person with OpenCV YuNet/SFace.
- **Persistent Memory**: Persist names, face embeddings, memories, interactions, and events in SQLite.
- **Wake Word**: The Pi listens locally for “Hey AURUS” and variants such as “Hi AURUS” and “Hello AURUS”. The dashboard can also listen through the browser's Web Speech API.

## Quick Start on Raspberry Pi

Install runtime packages:

```bash
sudo apt update
sudo apt install -y python3-venv python3-picamera2 python3-opencv python3-pyaudio portaudio19-dev espeak-ng build-essential cmake libopenblas-dev mpg123
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
pip install -r requirements.txt
python scripts/setup_models.py
cp .env.example .env
# Add GROQ_API_KEY to .env, then validate it:
python scripts/setup_llm.py
```

`scripts/setup_models.py` installs offline voice and vision fallback assets. Set `AURUS_FRONT_SENSOR_ENABLED=false` only if the center ultrasonic sensor is physically unavailable; this enables slower degraded exploration, but it cannot replace the coverage of a working forward sensor.

```bash
python run.py
```

Open `http://<raspberry-pi-ip>:5000`.

With **Browser Mic** enabled, say “Hey AURUS, introduce yourself” in one go, or say “Hey AURUS” and give the command when the button shows the listening countdown. Browser microphone access may require HTTPS when the dashboard is opened from another device.

Type **Introduce yourself** in chat or say it as a voice command to start the supervised FIT Expo demo. When using the Pi microphone, say “Hey AURUS” and wait for “I'm listening” before giving the command.

## Exhibition display and object detection

Keep the command center open on the operator laptop. Select **Open Face Display**
or open `http://<raspberry-pi-ip>:5000/face` in a second window, move it to the
external monitor, and enter browser fullscreen. The display shows AURUS's current
expression and reconnects automatically. While AURUS speaks, the spoken sentence
appears below the face and clears when playback ends. This includes ordinary
replies, object announcements and FIT Expo narration, through Pi or PC audio.
Keep dashboard access on the operator network.

With the camera running, hold up a prop and select **Detect Objects** under the
live feed. AURUS sends one camera frame to Groq Vision and lists the object labels
on the command center, then speaks a short result using the normal conversation
voice, for example, "I see a red mug and a notebook." Voice or typed
`Identify this object` requests also speak once. Camera-off or stale scans do
not queue an announcement; queued button results are checked again before
playback. Audio already playing can finish.
Keep `GROQ_VISION_MODEL=qwen/qwen3.8-27b` in the Pi's `.env` and verify the feature
with the exhibition connection before presenting.

The **Start FIT Expo Demo** sequence uses live Groq or recorded speech, six short smooth
gestures (forward, backward, left/right strafe and left/right turn), then stops
for a live camera object check and speaks what it finds. The supervised demo
bypasses ultrasonic distance and coverage restrictions; keep its movement area
clear. Stop Demo, E-STOP, disconnect and command timeouts remain active. Camera
OFF or a failed/changed scan is explained and the introduction continues.

For repeated expo runs, set `AURUS_DEMO_TTS_MODE=recorded` on the Pi. Prepare
Troy clips once with `.venv/bin/python scripts/check_demo_tts.py --prepare`
when Groq quota is available. Recorded mode uses Piper or eSpeak for missing
clips and live object results. See the [Pi setup guide](docs/PI_MANUAL_SETUP.md).

MCP movement retains obstacle stopping below **20 cm**, with reduced speed
from **20 to below 25 cm**. Manual and Explore retain their existing thresholds
and configured missing-sensor policy. These changes need no new `.env` entry.

## Camera and audio diagnostics

Run only one AURUS instance. If `aurus.service` is active, do not also launch `python run.py`:

```bash
sudo systemctl status aurus --no-pager
sudo fuser -v /dev/video0 /dev/media0
```

At startup, `run.py` now stops known legacy AURUS units and reclaims camera or
microphone handles from stale same-user AURUS processes and orphaned
`rpicam`/`libcamera`/`arecord` helpers. It sends `SIGTERM`, waits three seconds,
and uses `SIGKILL` only for those verified stale owners. Unrelated applications
are reported but are never terminated automatically. Set
`AURUS_DEVICE_RECLAIM_STRICT=true` if startup should abort while such an owner
remains instead of allowing the camera and speech services to report their own
degraded state.

When `aurus.service` starts under systemd, it may also replace a verified
same-user manual AURUS runtime that still holds the runtime lock. A manual
`python run.py` launch never performs that takeover, so it cannot kill the
active system service or create a restart fight.

For a USB microphone, run `arecord -l`. If it reports card 3, device 0, configure:

```dotenv
MIC_INDEX=
ALSA_CAPTURE_DEVICE=plughw:3,0
```

Then test the exact capture path with `python scripts/test_mic.py`. Local wake status and the selected microphone are reported by `python scripts/check_environment.py` and `/health`.

On an existing Pi installation, run `python scripts/setup_models.py --wake-only` from the project's environment, then restart AURUS to load the new wake phrases. The local mic now gets a 6 dB boost by default; lower `AURUS_MIC_GAIN_DB` in `.env` if the mic is already loud or the audio distorts. The browser uses its own microphone settings.

TTS gain and output routing are configurable:

```dotenv
AURUS_TTS_VOLUME_PERCENT=60
AURUS_TTS_GAIN_DB=6
AURUS_TTS_VOICE=en-US-GuyNeural
ALSA_PLAYBACK_DEVICE=plughw:2,0
```

Use `aplay -l` to find the output card, `alsamixer` to raise/unmute its hardware mixer, and `python scripts/test_tts.py` to test the configured path.

Motor geometry and final power are intentionally separate. Leave the mecanum
vector multiplier at `1.0`; use only the final output scale for uniform speed
adjustment across manual, voice/MCP, Explore, and animations:

```dotenv
MOTOR_SPEED_MULTIPLIER=1.0
MOTOR_OUTPUT_SCALE=1.0
```

After confirming all four channels work at `1.0`, reduce
`MOTOR_OUTPUT_SCALE` gradually (for example `0.95`, then `0.90`). Values below
`0.5` are clamped because unequal motor friction can cause individual wheels to
stall. Restart AURUS after changing `.env`.

For a complete report, first inspect ownership while AURUS is running, then stop it and run isolated device tests:

```bash
python scripts/diagnose_hardware.py
sudo systemctl stop aurus
python scripts/diagnose_hardware.py --active
```

The active test records three seconds from the selected microphone, captures one CSI camera image, and plays a known -6 dB tone. If the tone itself is quiet, the problem is the ALSA mixer, selected output, amplifier, speaker power, or wiring—not TTS synthesis.

## Tests

```bash
python -m pytest -q
```
