# AURUS: manual Pi startup and Groq speech

This guide applies after the new code has been deliberately transferred to the
Pi. It does not perform deployment or hardware tests automatically. Keep the
robot stationary while setting up audio.

## 1. Replace the previously exposed API key

In your own [Groq Console](https://console.groq.com/keys), revoke the key that was
pasted into chat and create a replacement. Enter the new key only in the Pi's
existing `.env`; do not paste it into chat or commit that file.

Check that your project/account can access **canopylabs/orpheus-v1-english**.
If the console asks you to accept model terms, review and accept them yourself.
The selected English voice is **troy**. Speech requests use your account quota.
Groq currently documents a 200-character input limit; our adapter splits longer
replies and joins validated WAV chunks without dropping the rest of the message.

## 2. Publish the reviewed laptop changes

No commit or push was performed by Codex. On the laptop, review the files in
Codex/Git, confirm the intended GitHub remote and publish the reviewed change.
For this change, the complete source/document/test file set is:

```text
.env.example
pytest.ini
src/services/groq_tts_service.py
src/services/tts_service.py
src/runtime.py
scripts/check_groq_tts.py
scripts/prepare_demo_audio.py
tests/conftest.py
tests/test_groq_tts.py
tests/test_exhibition_demo.py
docs/PI_MANUAL_SETUP.md
assets/fit_expo/README.md
AURUS_FINAL_IMPLEMENTATION_REPORT.md
```

Review `git status --short`, `git diff` and `git remote -v` before manually
staging/committing/pushing. Never stage `.env`. The existing prepared WAV assets
are unchanged by this integration; keep their matching manifest when deploying.

## 3. Stop boot startup and update the verified Pi checkout

On the Pi:

```bash
sudo systemctl disable --now aurus.service
systemctl is-enabled aurus.service
systemctl is-active aurus.service
systemctl show aurus.service -p WorkingDirectory -p ExecStart
```

Expected states are `disabled` and `inactive`. The checked-in unit points to
`/home/tt-11/AURUS-3.0`; use the installed unit's actual directory:

```bash
cd /home/tt-11/AURUS-3.0
git remote -v
git status --short
git branch --show-current
git rev-parse HEAD
```

The laptop and Pi previously used different repository URLs. Verify owner/repo
and shared history before fetching/merging. If the worktree is dirty, preserve
it first using the recovery instructions in the implementation report. Do not
reset, force-pull or overwrite `.env`, the database or untracked files.

Only when `origin` and `main` are the verified deployment remote/branch and the
worktree is clean:

```bash
git branch recovery/before-groq-tts-YYYYMMDD-HHMM HEAD
git fetch origin
git log --oneline HEAD..origin/main
git merge-base --is-ancestor HEAD origin/main
git merge --ff-only origin/main
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip check
```

Use a unique recovery branch name. If ancestry/fast-forward checks fail, stop
and inspect the repository mismatch/divergence. The Groq SDK was already a
project dependency; this integration adds no runtime package requirement.

## 4. Edit the existing Pi .env

```bash
nano .env
```

Add/update these entries, retaining your other private keys, model paths and
hardware settings:

```dotenv
GROQ_API_KEY=YOUR_NEW_PRIVATE_KEY
GROQ_VISION_MODEL=qwen/qwen3.8-27b

AURUS_TTS_BACKEND=groq
GROQ_TTS_MODEL=canopylabs/orpheus-v1-english
GROQ_TTS_VOICE=troy
AURUS_TTS_VOLUME_PERCENT=0
AURUS_TTS_GAIN_DB=6
AURUS_AUDIO_OUTPUT=auto

MIC_INDEX=
ALSA_CAPTURE_DEVICE=
ALSA_PLAYBACK_DEVICE=

AURUS_FRONT_SENSOR_ENABLED=false
AURUS_REQUIRE_FULL_SENSOR_COVERAGE=false
```

Replace `YOUR_NEW_PRIVATE_KEY` locally. Save using Ctrl+O, Enter, then Ctrl+X.
Zero volume boost is neutral, not mute. The additional gain is capped to avoid
PCM clipping. The existing cached demo has its own mastered levels and is not
boosted a second time.

Explicit `groq` takes priority over an installed Piper model. `auto` retains the
old provider selection if you intentionally want it. `AURUS_TTS_VOICE` is the
legacy Edge voice setting; **GROQ_TTS_VOICE=troy** selects the Groq voice.

## 5. Verify audio devices without starting the robot

```bash
lsusb
arecord -l
aplay -l
```

No capture devices means the microphone is not ready; a hardcoded card number
cannot repair missing USB detection. Reconnect/test the USB mic before relying
on wake-word control. Groq speech and the cached demo can still be tested through
text/dashboard controls without a working microphone.

Blank capture override permits automatic input discovery. Blank playback uses
the OS default output, which might be HDMI rather than your external speaker.
If `aplay -l` identifies the intended speaker as card 2/device 0, use
`ALSA_PLAYBACK_DEVICE=plughw:2,0`; otherwise use its actual card/device values.

## 6. Generate one Groq speech test (no robot hardware)

```bash
.venv/bin/python scripts/check_groq_tts.py
```

This loads the existing `.env`, requests the default test sentence from Groq,
validates the WAV and saves `logs/groq-tts-test.wav`. It does not start the
runtime, initialize GPIO, move motors or play audio. It prints model, voice,
duration and file path, never the API key.

Play it separately through the selected speaker. If that device is confirmed
as card 2/device 0:

```bash
aplay -D plughw:2,0 logs/groq-tts-test.wav
```

Use the actual confirmed device, not an assumed number. For a default-output
test, `aplay logs/groq-tts-test.wav` uses the OS output. Check the physical
speaker/amplifier volume. API errors identify key/model-access or quota/network
problems. Explicit Groq selection does not silently switch to a local voice.

## 7. Run AURUS manually

```bash
.venv/bin/python run.py
```

Keep the terminal open. Visit `http://PI_ADDRESS:5000` and check that TTS health
shows `groq` with the intended Pi/PC audio route. Type a greeting first and
verify one spoken response. Press Ctrl+C to stop. Leave aurus.service disabled;
do not run the service and the manual Python process together.

The FIT Expo demo still replays the existing loud cached neural narration
offline; it makes no new Groq requests. E-STOP and motor/sensor behavior are
unchanged by this speech integration.

## 8. Optional: prepare the entire demo with Groq's Troy voice

The already-supplied demo cache uses Microsoft Guy neural speech. If you also
want the prerecorded demo to use Groq, do this deliberately with AURUS stopped.
In a build environment with the existing requirements plus FFmpeg (or optional
imageio-ffmpeg), and the replacement key in its private `.env`:

```bash
.venv/bin/python scripts/prepare_demo_audio.py --provider groq --voice troy
```

This sends the public demo script to Groq, respects the 200-character limit,
masters/validates every clip, then replaces the cache and manifest. No local
speech synthesis is used. Review the preview before transferring the complete
`assets/fit_expo` folder to the Pi. Keep the working cache if account/model access
or mastering fails. The Pi needs only the finished WAV files for replay.

Reference: [Groq Orpheus TTS documentation](https://console.groq.com/docs/text-to-speech/orpheus).
