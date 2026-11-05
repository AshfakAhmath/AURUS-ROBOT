import time
import threading
import sys
import ctypes
import struct

try:
    import pyaudio
    import numpy as np
    HAS_AUDIO = True
except ImportError:
    HAS_AUDIO = False

# Suppress ALSA warning messages from C-library (harmless but annoying on Raspberry Pi)
try:
    ERROR_HANDLER_FUNC = ctypes.CFUNCTYPE(None, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p)
    def py_error_handler(filename, line, function, err, fmt):
        pass
    c_error_handler = ERROR_HANDLER_FUNC(py_error_handler)
    asound = ctypes.cdll.LoadLibrary('libasound.so.2')
    asound.snd_lib_error_set_handler(c_error_handler)
except Exception:
    pass

try:
    import pvporcupine
    HAS_PORCUPINE = True
except ImportError:
    HAS_PORCUPINE = False

try:
    from faster_whisper import WhisperModel
    HAS_WHISPER = True
except ImportError:
    HAS_WHISPER = False

from src import config

class VoiceListener:
    def __init__(self, on_command_callback):
        self.on_command_callback = on_command_callback
        self.running = False
        self.thread = None
        
        self.has_hardware = HAS_PORCUPINE and HAS_WHISPER and HAS_AUDIO
        
        if not self.has_hardware:
            print("[VoiceListener] WARNING: pvporcupine, faster-whisper, or pyaudio missing. Voice activation disabled.")
            return

        self.porcupine = None
        self.whisper_model = None
        
        try:
            print(f"[VoiceListener] Loading Whisper model ({config.WHISPER_MODEL})...")
            # Using int8 for faster CPU inference on Raspberry Pi
            self.whisper_model = WhisperModel(config.WHISPER_MODEL, device="cpu", compute_type="int8")
            print("[VoiceListener] Whisper model loaded successfully.")
        except Exception as e:
            print(f"[VoiceListener] ERROR: Failed to load Whisper: {e}")
            self.has_hardware = False

    def start(self):
        if not self.has_hardware:
            return
        
        self.running = True
        self.thread = threading.Thread(target=self._listen_loop, daemon=True)
        self.thread.start()
        print("[VoiceListener] Continuous background listener started.")

    def stop(self):
        self.running = False
        if self.porcupine:
            self.porcupine.delete()
        print("[VoiceListener] Background listener stopped.")

    def _listen_loop(self):
        if not config.PORCUPINE_ACCESS_KEY:
            print("[VoiceListener] ERROR: PORCUPINE_ACCESS_KEY not set in config.")
            self.running = False
            return

        try:
            # We use a built-in keyword for now since 'aurus' requires a custom trained .ppn file.
            # You can replace this with keyword_paths=[path_to_aurus.ppn] if you have it.
            keyword = "porcupine"
            self.porcupine = pvporcupine.create(access_key=config.PORCUPINE_ACCESS_KEY, keywords=[keyword])
        except Exception as e:
            print(f"[VoiceListener] ERROR: Failed to init Porcupine: {e}")
            self.running = False
            return

        pa = pyaudio.PyAudio()
        try:
            audio_stream = pa.open(
                rate=self.porcupine.sample_rate,
                channels=1,
                format=pyaudio.paInt16,
                input=True,
                frames_per_buffer=self.porcupine.frame_length,
                input_device_index=config.MIC_INDEX
            )
        except Exception as e:
            print(f"[VoiceListener] ERROR: Failed to open microphone: {e}")
            self.running = False
            return

        print(f"[VoiceListener] Listening for wake-word (currently set to '{keyword}' for testing)...")

        while self.running:
            try:
                pcm = audio_stream.read(self.porcupine.frame_length, exception_on_overflow=False)
                pcm_unpacked = struct.unpack_from("h" * self.porcupine.frame_length, pcm)
                
                keyword_index = self.porcupine.process(pcm_unpacked)
                
                if keyword_index >= 0:
                    print("[VoiceListener] WAKE-WORD DETECTED! Recording command...")
                    self._record_and_transcribe(pa)
                    # Re-print after transcription
                    print(f"[VoiceListener] Listening for wake-word...")
                    
            except Exception as e:
                print(f"[VoiceListener] Error in listen loop: {e}")
                time.sleep(1)

        audio_stream.close()
        pa.terminate()

    def _record_and_transcribe(self, pa):
        # Record 3 seconds of audio after wake word
        RECORD_SECONDS = 3
        RATE = 16000
        CHUNK = 1024
        
        try:
            stream = pa.open(format=pyaudio.paInt16,
                            channels=1,
                            rate=RATE,
                            input=True,
                            frames_per_buffer=CHUNK,
                            input_device_index=config.MIC_INDEX)
            
            frames = []
            for _ in range(0, int(RATE / CHUNK * RECORD_SECONDS)):
                data = stream.read(CHUNK, exception_on_overflow=False)
                frames.append(data)
                
            stream.close()
            
            # Convert to numpy array for faster-whisper (float32, -1.0 to 1.0)
            audio_data = b''.join(frames)
            audio_np = np.frombuffer(audio_data, dtype=np.int16).astype(np.float32) / 32768.0
            
            print("[VoiceListener] Transcribing with faster-whisper...")
            segments, info = self.whisper_model.transcribe(audio_np, beam_size=1, language="en")
            
            text = "".join([segment.text for segment in segments]).strip()
            print(f"[VoiceListener] Transcribed: \"{text}\"")
            
            if text and self.on_command_callback:
                threading.Thread(
                    target=self.on_command_callback,
                    args=(text,),
                    daemon=True
                ).start()
                
        except Exception as e:
            print(f"[VoiceListener] Failed to record/transcribe: {e}")
