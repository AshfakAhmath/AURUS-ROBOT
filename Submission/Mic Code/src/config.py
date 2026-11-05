import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    print("python-dotenv not installed. Skipping .env file loading.")

# --- Gemini API Configuration ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
GEMINI_VOICE = os.getenv("GEMINI_VOICE", "Puck")  # Choose from Aoede, Puck, Charon, Kore, Fenrir, etc.

# --- Voice Recognition Settings ---
PORCUPINE_ACCESS_KEY = os.getenv("PORCUPINE_ACCESS_KEY", "")
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "tiny.en")
WAKE_WORDS = ["aurus"]
MIC_INDEX = os.getenv("MIC_INDEX", None)
if MIC_INDEX and MIC_INDEX.lower() not in ["none", "null"]:
    MIC_INDEX = int(MIC_INDEX)
else:
    MIC_INDEX = None

try:
    AURUS_RECORD_SECONDS = float(
        os.getenv("AURUS_MAX_UTTERANCE_SECONDS", os.getenv("AURUS_RECORD_SECONDS", "10.0"))
    )
except ValueError:
    AURUS_RECORD_SECONDS = 10.0

# Headless audio playback command (aplay for Raspbian Linux, mock/print on Windows)
import platform
if platform.system() == "Linux":
    AUDIO_PLAYER_CMD = "aplay"
else:
    AUDIO_PLAYER_CMD = "mock"

# --- GPIO Pin Configurations (BCM numbering) ---
# L298N Motor Driver #1 (Front Wheels: FL & FR)
MOTOR_FL_ENA = 25  # PWM Front-Left Speed
MOTOR_FL_IN1 = 24  # Front-Left Forward
MOTOR_FL_IN2 = 23  # Front-Left Backward
MOTOR_FR_IN3 = 5   # Front-Right Forward
MOTOR_FR_IN4 = 6   # Front-Right Backward
MOTOR_FR_ENB = 12  # PWM Front-Right Speed

# L298N Motor Driver #2 (Rear Wheels: RL & RR)
MOTOR_RL_ENA = 18  # PWM Rear-Left Speed
MOTOR_RL_IN1 = 17  # Rear-Left Forward
MOTOR_RL_IN2 = 27  # Rear-Left Backward
MOTOR_RR_IN3 = 13  # Rear-Right Forward
MOTOR_RR_IN4 = 19  # Rear-Right Backward
MOTOR_RR_ENB = 26  # PWM Rear-Right Speed

# Motor direction multipliers (1 for normal, -1 to invert)
MOTOR_FL_DIR = int(os.getenv("MOTOR_FL_DIR", "-1"))  # Inverted due to front motor wiring
MOTOR_FR_DIR = int(os.getenv("MOTOR_FR_DIR", "-1"))  # Inverted due to front motor wiring
MOTOR_RL_DIR = int(os.getenv("MOTOR_RL_DIR", "1"))   # Normal wiring
MOTOR_RR_DIR = int(os.getenv("MOTOR_RR_DIR", "1"))   # Normal wiring

# Strafe direction multiplier (1 for normal, -1 to invert)
MOTOR_STRAFE_DIR = int(os.getenv("MOTOR_STRAFE_DIR", "1"))  # Normal (1)

# Global motor driver speed scaling (torque/power) multiplier
MOTOR_SPEED_MULTIPLIER = float(os.getenv("MOTOR_SPEED_MULTIPLIER", "1.0"))

# Final PWM scaling is deliberately separate from mecanum vector scaling. Keep
# MOTOR_SPEED_MULTIPLIER at 1.0; this value uniformly scales every active wheel
# after direction and wheel mixing have already been calculated.
try:
    MOTOR_OUTPUT_SCALE = float(os.getenv("MOTOR_OUTPUT_SCALE", "1.0"))
except ValueError:
    MOTOR_OUTPUT_SCALE = 1.0
MOTOR_OUTPUT_SCALE = max(0.5, min(1.0, MOTOR_OUTPUT_SCALE))


# Motor channel swap helpers (to fix accidentally swapped front/rear wires in software)
MOTOR_SWAP_FL_RL = os.getenv("MOTOR_SWAP_FL_RL", "false").lower() == "true"
MOTOR_SWAP_FR_RR = os.getenv("MOTOR_SWAP_FR_RR", "false").lower() == "true"

# Ultrasonic Sensors HC-SR04 (5 sensors: FL, F, FR, RL, RR)
# Note: Echo pins require a voltage divider (1kΩ + 2kΩ) to convert 5V → 3.3V
SENSOR_FL_TRIG_PIN = 4    # Front-Left Trig
SENSOR_FL_ECHO_PIN = 7    # Front-Left Echo
SENSOR_FRONT_TRIG_PIN = 20  # Front Center Trig
SENSOR_FRONT_ECHO_PIN = 21  # Front Center Echo
SENSOR_FR_TRIG_PIN = 8    # Front-Right Trig
SENSOR_FR_ECHO_PIN = 9    # Front-Right Echo
SENSOR_RL_TRIG_PIN = 16   # Rear-Left Trig
SENSOR_RL_ECHO_PIN = 22   # Rear-Left Echo
SENSOR_RR_TRIG_PIN = 10   # Rear-Right Trig
SENSOR_RR_ECHO_PIN = 11   # Rear-Right Echo

# Sensor angular offsets from chassis heading (radians, used by simulation raycasting)
import math
SENSOR_ANGLE_FL = math.pi / 4       # Front-Left: +45°
SENSOR_ANGLE_FRONT = 0.0            # Front Center: 0°
SENSOR_ANGLE_FR = -math.pi / 4      # Front-Right: -45°
SENSOR_ANGLE_RL = 3 * math.pi / 4   # Rear-Left: +135°
SENSOR_ANGLE_RR = -3 * math.pi / 4  # Rear-Right: -135°

# --- Emotion Engine Thresholds & Constants ---
# Decay rates per tick (1 tick = ~1 second)
DECAY_HAPPINESS = 0.02
DECAY_CURIOSITY = 0.03
DECAY_SOCIAL_ENERGY = 0.01

# Mood & Relationship increments
INC_TRUST_POSITIVE = 0.05
DEC_TRUST_IGNORED = 0.01
INC_RELATIONSHIP_INTERACTION = 0.01

INC_HAPPINESS_TREAT = 0.3
INC_HAPPINESS_TALK = 0.15
INC_CURIOSITY_MOVE = 0.1
INC_CURIOSITY_SENSOR = 0.2

# Proximity Alarm boundaries (in cm)
PROXIMITY_ALARM_CM = 15.0
PROXIMITY_ALERT_CM = 40.0

# --- Drive Mode Constants ---
DRIVE_MODES = ["manual", "autonomous", "voice_command"]
DEFAULT_DRIVE_MODE = "manual"

# Autonomous exploration settings
AUTO_DRIVE_SPEED = 0.4           # Conservative forward speed (0.0–1.0)
AUTO_SPIN_DURATION = 0.8         # Seconds to spin when turning away from obstacle
AUTO_OBSTACLE_REACT_CM = 35.0    # Distance (cm) to begin turning in autonomous mode
AUTO_EXPLORE_INTERVAL = 0.15     # Loop tick rate (seconds) for the autonomous thread
AUTO_IDLE_SPIN_CHANCE = 0.05     # Per-tick probability of a random curious spin

# Voice command movement settings
VOICE_CMD_DRIVE_DURATION = 1.5   # Seconds of movement per voice burst
VOICE_CMD_SPIN_DURATION = 1.0    # Seconds of spin per voice command
VOICE_CMD_DRIVE_SPEED = 0.5      # Speed for voice-commanded movements

# --- AURUS Brain Configuration ---
BRAIN_MEMORY_MAX_TURNS = 20          # Max conversation turns to remember
BRAIN_IDLE_THOUGHT_INTERVAL = 45     # Seconds between idle monologues
BRAIN_IDLE_THOUGHT_CHANCE = 0.6      # Probability (0-1) of generating an idle thought per interval
BRAIN_STATE_INJECTION = True         # Inject sensor/mood data into Gemini context
BRAIN_TEMPERATURE = 1.2              # Gemini creativity (higher = more alien)
BRAIN_MAX_SPEECH_WORDS = 30          # Max words per speech response

# Allowed motor actions the AI brain can request
BRAIN_ACTIONS = [
    "stop", "wiggle", "spin", "forward", "backward",
    "strafe_left", "strafe_right", "shiver", "nod", "dance"
]
