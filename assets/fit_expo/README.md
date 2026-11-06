# FIT Expo demo: live Groq narration

In live mode, the demo sends its predefined messages from
`src/services/demo_script.py` to Groq each time a line is spoken. For repeated
shows with saved clips, see [recorded mode in the Pi setup guide](../../docs/PI_MANUAL_SETUP.md).
The Groq settings select Orpheus English and the Troy voice:

```dotenv
AURUS_DEMO_TTS_MODEL=canopylabs/orpheus-v1-english
AURUS_DEMO_TTS_VOICE=troy
AURUS_DEMO_AUDIO_ENHANCE=true
AURUS_DEMO_TTS_GAIN_DB=6
```

`GROQ_API_KEY` stays in the private `.env`. Internet, model access/terms and API
quota are required. Lines longer than 200 characters are split and their WAV
responses joined. A temporary WAV is played through the existing Pi/PC/auto
route and deleted after playback or failure. Nothing is retained for replay.
Live mode uses temporary audio for each line. Demo-only EQ reduces bass and lifts speech presence;
compression with short look-ahead and make-up gain lifts the average level.
Peaks retain 1 dB headroom. Disabling enhancement restores the prior bounded
gain processor. No extra audio-processing package is required. Speaker/amplifier volume
and the correct output device still determine actual audibility.

The prototype introduction, Faculty of Information Technology Expo wording,
capabilities, greetings and closing are unchanged. Normal robot speech uses
its previous Piper/Edge/eSpeak selection independently of demo speech.

Select the speaker output, then Start FIT Expo Demo. A generation/playback
failure cancels later gestures. Stop Demo, E-STOP and operator disconnect remain
active. See `docs/PI_MANUAL_SETUP.md` for manual startup and configuration.
