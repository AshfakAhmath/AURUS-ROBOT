# AURUS (Autonomous Robotic Ubiquitous System) - Complete System Documentation

## 1. Introduction
This document serves as the master specification for the AURUS V2 project (also known as RoverBuddy). It contains the Software Requirements Specification (SRS), Hardware Integration details, Software Architecture, and specific instructions for reproducing the AI behaviors using Google AI Studio.

---

## 2. Software Requirements Specification (SRS)

### 2.1 Purpose & Scope
AURUS is a voice-activated, emotionally intelligent, and visually aware robotic companion. It operates on a Raspberry Pi 4B platform, featuring a 4-wheel Mecanum drive system, 5 ultrasonic sensors, an HD camera, and a personality powered by the Google Gemini API. It can also run entirely in a local 2D simulation environment for development on Windows/macOS.

### 2.2 Functional Requirements
1. **Wake-Word Detection:** The system shall continuously monitor microphone input for the wake word "AURUS" (and phonetic variations) using the `SpeechRecognition` library.
2. **AI Personality Core:** The system shall query the Google Gemini API with a strict system prompt defining an "ancient cosmic entity trapped in a robot body." It must receive structured JSON responses dictating speech, emotion, physical action, and inner thoughts.
3. **Computer Vision & Presence:** The system shall utilize OpenCV and MediaPipe to track human faces, returning bounding box coordinates and maintaining a "Present" or "Absent" state. It shall also use MobileNet SSD for object recognition.
4. **Conversation Memory:** The system shall store conversation interactions in an SQLite database, allowing it to retrieve short-term contexts and generate daily summary reports upon request.
5. **Text-to-Speech (TTS):** The system shall utilize Gemini's native `"audio"` modality to generate lifelike speech (e.g., using the "Puck" voice) and play it asynchronously.
6. **Proactive Emotional Engine:** The system shall maintain an internal mood state consisting of Happiness, Curiosity, Social Energy, and Trust. These values influence autonomous behaviors, and the system shall spontaneously initiate greetings or check-ins when humans are present.
7. **Mecanum Locomotion:** The system shall provide omnidirectional movement (forward, backward, strafe, spin) and complex choreographed animations (wiggle, shiver, nod, dance).
8. **Autonomous Explore Mode:** The current runtime uses guarded ultrasonic reactions and periodic sensor sweeps; follow mode has been removed.
9. **Explainable AI (XAI):** The system shall broadcast the internal logic (Decision, Reason, Confidence, Source Data) of the Gemini API over WebSockets to be displayed live on the UI.
10. **Web Control Dashboard:** The system shall serve a web interface (Flask/SocketIO) providing live telemetry, manual strafe controls, sensor radar, simulated camera feed, XAI readouts, and an automated Demonstration Mode.

### 2.3 Non-Functional Requirements
- **Platform:** Python 3 on Raspbian OS (production) or Windows/macOS (simulation).
- **Responsiveness:** The emotional engine must evaluate state at 1Hz. The autonomous driving loop must run at ~6.6Hz. The vision thread must process frames at ~20 FPS.
- **Safety:** The system must immediately halt motors and transition to manual mode if an obstacle breaches the critical proximity threshold (`PROXIMITY_ALARM_CM`).

---

## 3. Hardware Architecture & Wiring

### 3.1 Core Components
- **Microcomputer:** Raspberry Pi 4B
- **Chassis:** 4-wheel Mecanum chassis
- **Motor Control:** 2x L298N Dual H-Bridge Motor Drivers
- **Sensors:** 5x HC-SR04 Ultrasonic Distance Sensors
- **Vision:** Raspberry Pi Camera Module V2 (or USB Webcam)
- **Audio:** USB Microphone, External Speaker

### 3.2 GPIO Pin Configuration (BCM Numbering)
**Front Motor Driver (L298N #1):**
- **Front-Left:** Speed/PWM (ENA) = GPIO 25, IN1 = 24, IN2 = 23
- **Front-Right:** Speed/PWM (ENB) = GPIO 12, IN3 = 5, IN4 = 6

**Rear Motor Driver (L298N #2):**
- **Rear-Left:** Speed/PWM (ENA) = GPIO 18, IN1 = 17, IN2 = 27
- **Rear-Right:** Speed/PWM (ENB) = GPIO 26, IN3 = 13, IN4 = 19

**HC-SR04 Ultrasonic Sensors (Trig / Echo):**
- **Front-Left:** GPIO 4 / 7
- **Front Center:** GPIO 20 / 21
- **Front-Right:** GPIO 8 / 9
- **Rear-Left:** GPIO 16 / 22
- **Rear-Right:** GPIO 10 / 11

> [!WARNING]
> HC-SR04 Echo pins output 5V. You **must** use a voltage divider (e.g., 1kΩ and 2kΩ resistors) before connecting them to the Raspberry Pi's 3.3V GPIO pins to prevent damage.

---

## 4. Google AI Studio & Gemini Integration

To recreate the exact intelligence of AURUS using Google AI Studio, the codebase uses the modern `google-genai` Python SDK. 

### 4.1 System Instruction Prompt
AURUS requires a highly specific persona. The API is called with a system prompt that enforces:
- An identity as a cosmic consciousness baffled by Earth.
- Short, punchy speech (under 30 words).
- Strict output formatting in JSON, including an `xai` metadata block.

### 4.2 Dynamic Context Injection
Before sending a user message to Gemini, the backend (`aurus_brain.py`) injects live telemetry and memory history:
```text
[CURRENT BODY STATE]
Mood: happiness=0.85, curiosity=0.60, social_energy=0.9
Expression: happy
Drive Mode: manual
Sensors — Front-Left: 120cm, Front: 150cm, Front-Right: 80cm
Vision — Presence: Present, Objects: ["chair", "laptop"]
Relationship Strength: 0.75
Uptime: 45 minutes
```

### 4.3 Structured JSON Response
The model is configured with `response_mime_type="application/json"`. The required schema is:
```json
{
    "speech": "By the Rings of Zelthar! A human! Hello!",
    "emotion": "happy",
    "action": "wiggle",
    "inner_thought": "Biologicals are so fascinating.",
    "xai": {
        "decision": "Greeting interaction",
        "reason": "Presence changed to Present",
        "confidence": "95%",
        "source_data": "Vision Pipeline"
    }
}
```

### 4.4 Gemini Audio Generation (Native TTS)
Instead of relying on third-party TTS libraries, AURUS uses Gemini's native audio modality.
```python
response = client.models.generate_content(
    model="gemini-2.0-flash",
    contents="Read this aloud: Hello human!",
    config=types.GenerateContentConfig(
        response_modalities=["audio"],
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Puck")
            )
        )
    )
)
```
The resulting binary audio data is extracted, saved as a `.wav` file, and played asynchronously.

---

## 5. Software Architecture Modules

### 5.1 `src/web/app.py`
The master controller. It runs a Flask web server and a SocketIO WebSocket server.
- **`mood_engine_loop()`:** Runs in the background (1Hz). Evaluates sensor data, updates `Social Energy`, triggers proactive initiatives, and tracks presence states.
- **`autonomous_explore_loop()`:** Runs at ~6Hz. Automatically drives the rover forward and steers away from obstacles dynamically.
- **Explore controller:** `BehaviorController` issues short-lived requests while `MotionArbiter` enforces proximity and deadman safety.

### 5.2 `src/ai/aurus_brain.py` & `intent_router.py`
The AI logic core. 
- Manages the SQLite `ConversationMemory`.
- Constructs complex prompts combining sensor data and history.
- Evaluates intents to trigger guarded actions such as exploration or daily summaries.
- Injects local fallback XAI metadata.

### 5.3 `src/vision/vision_system.py`
The dedicated computer vision module.
- Captures frames from the camera.
- Extracts `faces` and `presence_state` via MediaPipe.
- Extracts `objects` via MobileNet SSD.
- Includes a `mock_presence_override` for Demonstration Mode.

### 5.4 `src/hardware/motors.py` & `sensors.py`
Handles Mecanum kinematics and Proximity sensors. 
- Converts `(vx, vy, omega)` variables into specific duty cycles.
- **Simulation Mode:** Performs 2D mathematical raycasting from the virtual rover's position to simulate realistic sensor readings when run on PC.

---

## 6. Setup & Execution

1. **Environment Setup:** Create a `.env` file containing:
   ```env
   GEMINI_API_KEY=your_google_ai_studio_api_key
   GEMINI_MODEL=gemini-2.0-flash
   GEMINI_VOICE=Puck
   ```
2. **Dependencies:** `pip install -r requirements.txt` (requires `portaudio19-dev` on Linux for microphone access).
3. **Run Server:** `python run.py`
4. **Access UI:** Open `http://<localhost>:5000` in a web browser.
5. **Testing:** Run `python tests/test_suite.py` to run simulated kinematics and raycasting unit tests.
