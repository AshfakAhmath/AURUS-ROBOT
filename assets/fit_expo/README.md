# FIT Expo narration

The demo synthesizes its scripted lines and live camera announcements with
the Piper voice selected by `PIPER_MODEL_PATH`. eSpeak is the local fallback.
The live object check uses Groq Vision. These settings control demo volume and processing.

```dotenv
AURUS_DEMO_AUDIO_ENHANCE=true
AURUS_DEMO_TTS_GAIN_DB=6
```

AURUS sends a temporary WAV through the selected Pi or PC speaker route. Demo
EQ lifts speech presence, and the gain limiter leaves peak headroom.
`scripts/check_demo_tts.py` can still export Groq clips for standalone playback.

Select the speaker output, then Start FIT Expo Demo. A generation/playback
failure cancels later gestures. Stop Demo, E-STOP and operator disconnect remain
active. See the [Pi setup guide](../../docs/PI_MANUAL_SETUP.md) for startup and configuration.
