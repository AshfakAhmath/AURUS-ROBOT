# AURUS: original normal TTS and live Groq FIT Expo demo

Normal speech uses the previous automatic Piper / Edge / eSpeak selection and
its previous gain behavior. Only the demo uses Groq Orpheus/Troy. Each predefined
line requests fresh audio on every run; long lines are split at 200 characters.
There is no saved-demo replay or local-voice fallback for demo narration.
Temporary WAVs are removed after playback or failure.

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
All source deletions must be included when transferring manually: the old cached
WAV/manifest, preparation script and general Groq test command are retired.
No new package requirement was added; Groq was already a project dependency.

## 2. Edit the existing Pi .env

Keep your private replacement `GROQ_API_KEY`; it is still needed for chat,
vision, speech recognition and live demo narration. Never paste it into chat.
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

# Demo only: live Groq speech every time
AURUS_DEMO_TTS_MODEL=canopylabs/orpheus-v1-english
AURUS_DEMO_TTS_VOICE=troy
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

## 4. Confirm Groq demo access

Use the Groq account/organization that owns the private key. Open the
[Orpheus playground](https://console.groq.com/playground?model=canopylabs/orpheus-v1-english).
If it requests model terms, the appropriate account administrator must review
and accept them if they agree. HTTP 400 alone does not establish the cause;
`model_terms_required` is now reported explicitly when returned by Groq.

The live demo requires internet and available Groq quota. A failed generation
cancels the demo before its next gesture and reports the reason. It does not
reuse an earlier recording or silently change the demo voice. An in-flight
cloud request can finish after Stop, but its audio is discarded and no later
chunk, playback or movement is started for the cancelled demo.

After transferring the updated source (including `scripts/check_demo_tts.py`
and `src/services/speech_audio.py`), verify synthesis without robot startup:

```bash
.venv/bin/python scripts/check_demo_tts.py
```

To also play through the confirmed headphone output:

```bash
.venv/bin/python scripts/check_demo_tts.py --play --device plughw:2,0
```

Both commands request a fresh introduction and delete the temporary audio.
Only `--play` invokes audio playback. They never initialize the robot runtime,
GPIO or motors and do not print the API key. Successful synthesis confirms API
access at that moment; an audible introduction confirms the speaker route.

## 5. Start manually and test

```bash
.venv/bin/python run.py
```

Visit `http://PI_ADDRESS:5000`. First type a greeting to confirm the original
normal voice. Choose Pi or PC audio before the demo, then use **Start FIT Expo
Demo**. Speech is generated from the reviewed prototype/FIT Expo introduction,
capabilities, greetings and closing text in `src/services/demo_script.py`.
Each new run makes new speech requests. Short forward/reverse/turn gestures
remain arbiter-controlled; there is no strafing. Stop Demo, E-STOP and operator
disconnect still cancel later actions. Press Ctrl+C to stop AURUS; keep the
service disabled and do not run a second hardware owner concurrently.

Reference: [Groq Orpheus speech](https://console.groq.com/docs/text-to-speech/orpheus).

## 6. Camera ON/OFF control

After deliberately updating the Pi code, stop the manual AURUS process with
Ctrl+C, start it again with `.venv/bin/python run.py`, and refresh the dashboard
(Ctrl+Shift+R if its old JavaScript/CSS is cached). No new Python package is
required for this change.

The **Visual identity** panel now has a **Turn camera off / Turn camera on**
button above the video. This controls actual capture and face processing.

- OFF clears the current image and recognized visitor, cancels unfinished
  enrollment, and releases the camera through its existing single worker.
  Previously saved people, face models and memories remain stored.
- The panel shows **Stopping camera...** while an in-flight camera operation
  finishes, then **Camera switched off** after release. Driver release failures
  are shown as camera errors and retried; they are not reported as a completed
  OFF transition. The application does not create a second camera owner.
- Enrollment and visual questions are unavailable while OFF. Old frames and
  AI descriptions that cross an OFF/ON transition are rejected. Re-enabling
  requires fresh frames before enrollment or identification can resume.
- Switching OFF during Explore stops motion and returns to idle. Switching ON
  does not resume movement: choose Explore again when ready.
- Manual ultrasonic-controlled movement, ordinary speech and the scripted
  Groq FIT Expo demo remain available while the camera is OFF.
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

For a stationary Pi check, toggle OFF and confirm the image disappears, face
enrollment is disabled and “What can you see?” explains that the camera is off.
Toggle ON and wait for a fresh image. Recognize/enroll again, then check that
text chat and the demo still work. Test Explore pause only in a suitable clear
area under operator control; no physical movement test was run automatically.

## 7. Object announcements and face transcripts

After transferring the updated code, stop AURUS with Ctrl+C and restart with
`.venv/bin/python run.py`. Refresh both the dashboard and `/face` display with
Ctrl+Shift+R. No `.env` change or new package is required for these features.

With the camera ON and ready, hold a prop in view and press **Detect Objects**.
The dashboard lists the objects and AURUS speaks a brief result using the same
voice as normal conversation. A voice or typed `Identify this object` request
still speaks only once. If speech cannot be queued, the visual result remains
available and the dashboard explains the speech issue.

Open **Open Face Display** on the presentation monitor. The current spoken
sentence appears below the face during Pi-speaker or PC-browser playback,
including FIT Expo narration. Text clears when speech ends. Captions show the
whole utterance, with the existing display polling delay, rather than word-by-word
highlighting. Check a normal greeting, an object result and a demo line.

Switching the camera OFF or restarting its session discards unfinished scans
and button announcements still waiting for playback. An announcement already
playing may finish. The face window remains a read-only display and does not
take over dashboard controls.
