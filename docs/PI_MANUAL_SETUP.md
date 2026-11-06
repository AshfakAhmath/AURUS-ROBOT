# AURUS speech and FIT Expo demo setup

Normal speech selects Piper, Edge or eSpeak. The FIT Expo demo uses the
configured Piper model with eSpeak as its local fallback for both scripted
narration and live camera announcements.

## 1. Transfer the reviewed changes

Review/publish the laptop changes deliberately. No commit, push or Pi deployment
was performed automatically. On the Pi, verify the installed service directory,
repository remote, branch and shared history before updating; the laptop and Pi
previously used different repository URLs. Preserve a dirty checkout and private
`.env`/database before updating; do not reset or force-pull.

```bash
sudo systemctl disable --now aurus.service
systemctl is-enabled aurus.service
systemctl is-active aurus.service
systemctl show aurus.service -p WorkingDirectory -p ExecStart
cd /home/tt-11/AURUS-3.0
git remote -v
git status --short
git branch --show-current
git rev-parse HEAD
```

Use the installed unit's actual project directory if different. Service states
should be disabled/inactive. Only for a clean checkout with verified matching
`origin/main` and shared history:

```bash
git branch recovery/before-live-demo-YYYYMMDD-HHMM HEAD
git fetch origin
git log --oneline HEAD..origin/main
git merge-base --is-ancestor HEAD origin/main
git merge --ff-only origin/main
```

Use a unique recovery-branch name. Stop on any ancestry/divergence failure.
No new package requirement was added; Groq was already a project dependency.

## 2. Edit the existing Pi .env

Keep your private `GROQ_API_KEY` for chat, vision and speech recognition.
Remove the obsolete ordinary-cloud settings `AURUS_TTS_BACKEND`,
`GROQ_TTS_MODEL` and `GROQ_TTS_VOICE`. They no longer select normal speech.

```bash
cp -p .env ".env.backup-$(date +%Y%m%d-%H%M%S)"
nano .env
```

Use these speech/audio entries:

```dotenv
# Previous normal speech settings supplied by the operator
AURUS_TTS_VOICE=en-US-GuyNeural
AURUS_TTS_VOLUME_PERCENT=100
AURUS_TTS_GAIN_DB=12

# Demo volume and speech processing
AURUS_DEMO_AUDIO_ENHANCE=true
AURUS_DEMO_TTS_GAIN_DB=6

# Devices confirmed by the operator's latest ALSA output
MIC_INDEX=
ALSA_CAPTURE_DEVICE=plughw:3,0
ALSA_PLAYBACK_DEVICE=plughw:2,0
AURUS_AUDIO_OUTPUT=pi

# Existing remaining-sensor policy
AURUS_FRONT_SENSOR_ENABLED=false
AURUS_REQUIRE_FULL_SENSOR_COVERAGE=false
```

Keep any other previously working normal-speech gain/model-path settings if they
differ. The ordinary Edge voice setting does not change a Piper model's voice.
Save with Ctrl+O, Enter, Ctrl+X. The new demo gain is independent and peak-limited;
ordinary speech's previous settings and fallback behavior have been restored.
With enhancement enabled, demo-only EQ reduces bass and lifts speech presence;
compression with short look-ahead and bounded make-up gain raises average level
while leaving 1 dB sample-peak headroom. Normal conversation gain is unchanged.

## 3. Confirm actual audio devices

```bash
arecord -l
aplay -l
```

The last supplied output identified USB mic card 3/device 0 and Pi headphone
output card 2/device 0. Card numbers can change after reboot. Card 2 is for the
3.5 mm headphone jack, not a USB/Bluetooth speaker. Confirm the correct
connection, speaker power, physical volume and ALSA mute/volume before the demo.

## 4. Check the local demo voice

Set `PIPER_MODEL_PATH` to your installed `.onnx` voice file, with its
`.onnx.json` configuration next to it. Run the voice test with the robot stopped.

```bash
.venv/bin/python scripts/test_tts.py
```

The demo uses Piper when available, and can use eSpeak if Piper fails. A demo
speech or playback failure stops later gestures and reports the reason. Older
`AURUS_DEMO_TTS_MODE` settings are ignored. The live object check still needs
Groq Vision. `scripts/check_demo_tts.py` is a separate Groq audio export tool.

## 5. Start manually and test

```bash
.venv/bin/python run.py
```

Visit `http://PI_ADDRESS:5000`. First type a greeting to confirm the original
normal voice. Choose Pi or PC audio before the demo, then use **Start FIT Expo
Demo**. Speech is generated from the reviewed prototype/FIT Expo introduction,
capabilities, greetings and closing text in `src/services/demo_script.py`.
Short forward/reverse, left/right
strafe and turn gestures use gradual acceleration and deceleration. Demo
gestures bypass ultrasonic distance and coverage restrictions; keep the small
movement area clear and supervise the robot. Stop Demo, E-STOP and operator
disconnect still cancel later actions. Press Ctrl+C to stop AURUS; keep the
service disabled and do not run a second hardware owner concurrently.

## 6. Camera ON/OFF control

After deliberately updating the Pi code, stop the manual AURUS process with
Ctrl+C, start it again with `.venv/bin/python run.py`, and refresh the dashboard
(Ctrl+Shift+R if its old JavaScript/CSS is cached). No new Python package is
required for this change.

The **Live camera** panel has a **Turn camera off / Turn camera on** button
above the video. This controls capture for preview, object detection and gestures.

- OFF clears the current image and releases the camera through its existing
  single worker.
- The panel shows **Stopping camera...** while an in-flight camera operation
  finishes, then **Camera switched off** after release. Driver release failures
  are shown as camera errors and retried; they are not reported as a completed
  OFF transition. The application does not create a second camera owner.
- Visual questions, object detection and gesture scans are unavailable while
  OFF. Results from before an OFF/ON transition are discarded.
- Switching OFF during Explore stops motion and returns to idle. Switching ON
  does not resume movement: choose Explore again when ready.
- Manual ultrasonic-controlled movement, ordinary speech and the scripted
  FIT Expo demo remain available while the camera is OFF.
- All connected dashboards receive the state. Disconnecting hides that
  browser's old image and disables its camera controls until reconnection.

The switch changes runtime state only. Set the initial state in the existing
private `.env` if desired:

```dotenv
AURUS_CAMERA_ENABLED=true
```

True preserves the previous camera-on startup behavior; false starts with
capture and model initialization paused. Restart reads this value again.
Keep one AURUS instance running; do not launch separate camera diagnostics
while the camera is still reported as stopping or in a release-error state.

For a stationary Pi check, toggle OFF and confirm the image disappears,
**Try a Gesture** is disabled and “What can you see?” explains that the camera
is off. Toggle ON and wait for a fresh image. Test Explore pause only in a suitable clear
area under operator control; no physical movement test was run automatically.

## 7. Object announcements and mascot transcripts

After transferring the updated code, stop AURUS with Ctrl+C and restart with
`.venv/bin/python run.py`. Refresh both the dashboard and `/face` display with
Ctrl+Shift+R. No `.env` change or new package is required for these features.

With the camera ON and ready, hold a prop in view and press **Detect Objects**.
The dashboard lists the objects and AURUS speaks a brief result using the same
voice as normal conversation. A voice or typed `Identify this object` request
still speaks only once. If speech cannot be queued, the visual result remains
available and the dashboard explains the speech issue.

Open **Open Mascot Display** on the presentation monitor. The current spoken
sentence appears below the mascot during Pi-speaker or PC-browser playback,
including FIT Expo narration. Text clears when speech ends. Captions show the
whole utterance, with the existing display polling delay, rather than word-by-word
highlighting. Check a normal greeting, an object result and a demo line.

Switching the camera OFF or restarting its session discards unfinished scans
and button announcements still waiting for playback. An announcement already
playing may finish.

## 8. Refined demo and MCP clearance margins

Transfer the reviewed code, restart the manual AURUS process and hard-refresh
the dashboard and face display. No `.env` edit, dependency installation or
sensor disabling is required. Secure the wheels before using the new strafes.

The demo gestures each last 0.6 seconds with a peak requested axis value of
0.20, smoothly ramping from zero and back to zero. Existing motor scaling and
strafe compensation still apply; this is a speed command, not a distance
guarantee. Normal completion decelerates; operator stops act immediately.
The dashboard explicitly shows **Ultrasonic stops off for demo** while armed.
Manual and Explore continue using their existing sensor protection.

After the gestures and capabilities line, AURUS remains stationary, takes a
fresh camera frame, identifies visible objects and speaks a brief live result
using the selected demo narration mode with face captions. Camera OFF, no fresh frame,
network/model failure or a changed camera session skips that result and allows
the closing to continue. Switching the camera OFF during synthesis discards
an object line that has not started playback; audio already playing can finish.
Stop/E-STOP during a vision request stops motors immediately and discards its
late result. The in-flight request may finish before the demo worker exits.

MCP moves, spins and animations (dashboard and standalone server) stop below
20 cm and slow from 20 to below 25 cm. They do not use the old 35 cm caution or
45/75 cm degraded-front distance margins. Sensor coverage still follows
`AURUS_REQUIRE_FULL_SENSOR_COVERAGE`; all readings unavailable still stops MCP.
Explicit stop, E-STOP, disconnect and command time limits remain enforced.
Do not run the standalone MCP server together with the main hardware runtime.

Before presenting, test one full demo with clear space, then Stop Demo and
E-STOP during separate runs. Confirm strafes are brief, speed rises/falls
gradually, and the live object line matches a visible prop. Check an MCP command
at a measured 30 cm, 22–24 cm and below 20 cm to confirm normal, reduced and
stopped output respectively. Physical motor smoothness and stopping distance
must be checked on the Pi; automated tests use simulated devices only.

## 9. Mascot gestures

On 64-bit Raspberry Pi OS, install the optional recognizer into the existing
virtual environment and download the model. The OpenCV and NumPy bounds keep
the install compatible with AURUS's NumPy 1.x dependency.

```bash
.venv/bin/python -m pip install --only-binary=:all: 'mediapipe==1.2.2' 'numpy>=1.24,<2' 'opencv-contrib-python==4.11.0.86'
.venv/bin/python scripts/setup_models.py --gesture-only
.venv/bin/python run.py
```

Open **Open Mascot Display** from the dashboard. With the preview live, select
**Expo Auto Mode** once. Visitors can then show any of these gestures.

- **Wave** by moving one open hand side to side.
- **Thumbs-up** by holding one thumb up.
- **Heart** by joining both index fingertips above both thumbs.
- **Open palm** by holding one open hand still, facing the camera.
- **Peace sign** by raising the index and middle fingers.

Lower both hands out of view briefly before repeating the same gesture. Expo
mode pauses when the camera goes off or the display tab is hidden and resumes
when both are ready. Select **Expo Auto Mode** again to end the session. Use
**Try a Gesture** for a single scan while Expo mode is off.

Scans run locally, with extra time to load the model on the first scan. Gesture
results animate the mascot. Use **Set name** or say “My name is …” for
personal memory.
