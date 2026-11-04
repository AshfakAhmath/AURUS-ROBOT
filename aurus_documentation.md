# AURUS V2 - System Overview & Documentation

> [!NOTE]
> This document provides a comprehensive snapshot of the AURUS V2 codebase following the major 10-Phase Upgrade. It covers core features, the expanded technology stack, and hardware configurations.

## 1. System Features

The AURUS V2 codebase evolves the original robotic pet into a proactive, emotionally intelligent, and visually aware robotic companion. Key features include:

- **Vision & Presence System (Computer Vision):** Uses OpenCV and MediaPipe for facial recognition to determine user presence ("Absent" vs "Present") and tracks bounding boxes for navigation. Incorporates MobileNet SSD for environmental object recognition.
- **SQLite Conversation Memory:** Stores long-term conversation history locally in an SQLite database (`aurus_memory.db`), allowing AURUS to recall previous topics and synthesize daily summaries.
- **Proactive Relationship Engine:** Expands the emotional model to track `Trust`, `Social Energy`, and `Relationship Strength`. AURUS dynamically decays these over time and can spontaneously initiate interactions (greetings, check-ins, or encouragements) based on human presence.
- **Autonomous Follow Mode:** Uses a PID controller (`FollowController`) to automatically align and drive towards the user by combining visual face bounding boxes (for angle correction) and sonar proximity data (for distance maintenance).
- **Explainable AI (XAI) Dashboard:** The UI features a real-time debugging panel that broadcasts the internal logic of the Gemini LLM. It displays the `Decision`, `Reason`, `Confidence`, and `Source Data` for every action.
- **Demonstration Mode:** The UI includes an automated "Run Demo" button that simulates camera and voice inputs to showcase the full HRI workflow (Greeting -> Memory Recall -> Follow Mode -> Absence -> Daily Report).
- **Google AI Studio Native TTS:** Connects to the Gemini API (`google-genai`) to parse instructions, manage the mood engine, and natively synthesize lifelike vocal responses.
- **Interactive Web UI:** A glassmorphic control panel built on Flask-SocketIO. It displays real-time diagnostics, sonar radar sweeps, animated Canvas-based expressions, the live camera feed (or simulated object detection), XAI outputs, and allows manual control.

---

## 2. Technology Stack

AURUS blends software simulation with edge-hardware execution, utilizing the following stack:

### **Hardware & OS**
- **Compute Unit:** Raspberry Pi 4B
- **OS Environment:** Raspbian OS (Linux)

### **Backend Server & AI**
- **Core Language:** Python 3
- **Web Server:** Flask & Flask-SocketIO (with `eventlet` for async concurrency)
- **AI Integration:** `google-genai` (for logic, conversation, and audio synthesis)
- **Computer Vision:** `OpenCV` and `MediaPipe`
- **Database:** `sqlite3`
- **Audio Processing:** `SpeechRecognition` and `PyAudio`

### **Frontend & UI**
- **Structure:** HTML5
- **Styling:** Vanilla CSS (employing glassmorphism, dynamic gradients, and modern micro-animations)
- **Logic & Rendering:** Vanilla JavaScript, focusing heavily on HTML5 `<canvas>` for drawing the simulated physical arena and animated eye expressions.

### **Hardware Interfacing**
- **Library:** `rpi-lgpio` (for direct GPIO access)

---

## 3. Hardware PIN Configurations

> [!WARNING]
> HC-SR04 Echo pins output 5V logic. Ensure a voltage divider (e.g., 1kΩ + 2kΩ resistors) is used on all Echo pins to drop the voltage to the Raspberry Pi's safe 3.3V logic level.

### **Mecanum Motor Drivers (L298N BCM Numbering)**

* **L298N #1 (Front Wheels):**
  - **Front-Left (FL):** Speed (ENA) = `GPIO 25`, Forward (IN1) = `GPIO 24`, Backward (IN2) = `GPIO 23`
  - **Front-Right (FR):** Speed (ENB) = `GPIO 12`, Forward (IN3) = `GPIO 5`, Backward (IN4) = `GPIO 6`
* **L298N #2 (Rear Wheels):**
  - **Rear-Left (RL):** Speed (ENA) = `GPIO 18`, Forward (IN1) = `GPIO 17`, Backward (IN2) = `GPIO 27`
  - **Rear-Right (RR):** Speed (ENB) = `GPIO 26`, Forward (IN3) = `GPIO 13`, Backward (IN4) = `GPIO 19`

### **Proximity Sensors (HC-SR04 BCM Numbering)**

The robot utilizes 5 ultrasonic sensors mounted at different angles.

- **Front-Left (FL - +45°):** Trig = `GPIO 4`, Echo = `GPIO 7`
- **Front Center (F - 0°):** Trig = `GPIO 20`, Echo = `GPIO 21`
- **Front-Right (FR - -45°):** Trig = `GPIO 8`, Echo = `GPIO 9`
- **Rear-Left (RL - +135°):** Trig = `GPIO 16`, Echo = `GPIO 22`
- **Rear-Right (RR - -135°):** Trig = `GPIO 10`, Echo = `GPIO 11`

---
*Updated for the AURUS V2 Upgrade.*
