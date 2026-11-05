# AURUS — FIT Expo cloud neural narration

The demo uses prerecorded **Microsoft Edge online neural TTS**, voice
**en-US-GuyNeural**, at **-8% rate**. Windows Zira and other local synthesizers
are no longer used to prepare the demo. Cloud synthesis runs during preparation;
the Pi replays the finished cache offline.

- Audio: mono 16-bit PCM WAV, **48 kHz**.
- Speech mastering: reduced low-frequency rumble/bass, consonant presence EQ,
  compression, two-pass EBU R128 loudness normalization, and oversampled limiting.
- Target: **-12 LUFS**, with 1.5 LU tolerance; measured true peaks stay below
  the -1.5 dBTP target. The final limiter leaves additional headroom.
- Actual clips: **-13.36 to -11.96 LUFS**, approximately **3.6–5.8 LU louder**
  than the previous local recordings. None contain clipped samples.
- Full preview: **59.886 seconds**. The dashboard uses individual clips for
  narration, gestures and cancellation.
- The manifest records text, cloud voice, mastering settings, measured loudness,
  duration and SHA-256 hashes. A missing, stale or corrupt cache is rejected.
- Runtime replay does not amplify the mastered clips again or call any TTS API.

The introduction says **Faculty of Information Technology Expo**. The closing
says **FIT Expo**, pronounced F. I. T. The invitation to ask the team for another
demonstration remains removed.

## Replay

1. Select Pi, PC or Auto audio output before starting.
2. Select **Start FIT Expo Demo** from idle or stationary manual mode.
3. Use **Stop Demo** to cancel; E-STOP remains available.
4. Stop before switching speakers, then replay. A short Pi utterance already
   playing may finish before the next run can start.
5. Deploy every WAV and manifest together; changing only the manifest or one
   recording invalidates the cache.

The stronger average speech level helps small speakers, but the speaker's
physical volume, available amplifier power and selected audio route still
determine room audibility. Check the actual speaker before the event.

## Regenerate (build environment only)

Use a separate preparation environment with the existing edge-tts package and
FFmpeg. An optional imageio-ffmpeg package can supply the FFmpeg executable.
These build tools do not need to be installed on the Pi for cached playback.

    python scripts/prepare_demo_audio.py

Optional voice/rate/executable settings:

    python scripts/prepare_demo_audio.py --voice en-US-GuyNeural --rate=-8% --ffmpeg PATH_TO_FFMPEG

The public script text is sent to the online TTS service. There is no local
voice fallback. Successful cloud source recordings are cached in the temporary
directory by text/voice/rate hash for remastering without repeated API requests.
All new clips are prepared and validated before the working cache is replaced.
The manifest is published last; an interrupted update cannot look valid.
No playback or robot hardware initialization occurs during preparation.

Sources: [Edge TTS client](https://github.com/rany2/edge-tts),
[FFmpeg audio filters](https://ffmpeg.org/ffmpeg-filters.html).
