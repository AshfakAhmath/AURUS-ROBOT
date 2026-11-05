# AURUS — FIT Expo cached narration

These are prepared speech recordings, not a downloaded language model. They
replay without a synthesis service, internet connection, Groq request, or Windows
voice installed on the Raspberry Pi.

- Voice: **Microsoft Zira Desktop**, English (United States), Windows System.Speech.
- Rate: **-1**, with pauses from sentence punctuation.
- Format: mono 16-bit PCM WAV, directly playable by the existing Pi `aplay` path
  or the browser dashboard.
- Level: each clip is peak-normalized to **-1 dBFS**, leaving headroom and avoiding
  sample clipping. Runtime demo replay does not apply the ordinary TTS gain again.
- `fit_expo_preview.wav`: complete narration with pauses, about **62 seconds**.
  The dashboard plays individual clips so movements and cancellation can be coordinated.
- `manifest.json`: reviewed text, voice settings, durations and SHA-256 hashes.
  The demo validates this cache before it can start. Never silently reuse old
  recordings after editing their text.

The introduction says **Faculty of Information Technology Expo**. The closing
says **FIT Expo**, pronounced as F. I. T. The sentence inviting visitors to ask
the team for another demonstration has been removed.

## Dashboard replay

1. Select the existing Pi, PC or Auto audio output before starting.
2. From idle or stationary manual mode, select **Start FIT Expo Demo**.
3. Use **Stop Demo** to cancel; E-STOP remains available at all times.
4. If a gesture is blocked, all remaining movements are cancelled and the
   narration continues from the cache. There is no strafe or diagonal gesture.
5. To change speakers, stop first, select another output, then replay. Wait for
   an already-playing short Pi utterance to finish before restarting.

The speaker/amplifier's physical volume still controls room loudness. Rehearse
with the actual speaker before visitors arrive; normalization cannot compensate
for disconnected or underpowered hardware.

## Rebuild recordings after a script change

On Windows with Microsoft Zira installed and the existing NumPy dependency:

```powershell
python scripts/prepare_demo_audio.py
```

This renders to files only; it does not play speech or initialize robot hardware.
Review the script in `src/services/demo_script.py` and listen to the regenerated
preview. Deploy the complete `assets/fit_expo` folder with the reviewed code.
The Pi does not need System.Speech or this rendering script to replay the cache.

The prepared preview and clips are repository assets and should be included in
the operator's reviewed commit. No model or dependency was installed to generate
these recordings.
