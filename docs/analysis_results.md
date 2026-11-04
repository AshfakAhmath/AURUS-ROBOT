# AURUS Codebase Analysis — Feature Inventory & Reliability Assessment

## 1. System Overview

AURUS (**Autonomous Robotic Ubiquitous System**) is a voice-activated, emotionally intelligent robotic pet companion. It runs on a **Raspberry Pi 4B** with 4 Mecanum wheels and 5 ultrasonic sensors, controlled via a Flask/SocketIO web dashboard. The AI personality is powered by **Google Gemini API** with a local rule-based fallback.

| Metric | Value |
|---|---|
| **Total source files** | 12 (Python + JS + HTML + CSS) |
| **Python LOC** | ~1,810 |
| **JavaScript LOC** | ~1,256 |
| **CSS LOC** | ~1,100 |
| **Test count** | 17 (all passing ✅) |
| **Dependencies** | 8 packages |

---

## 2. Feature Inventory & Quality Assessment

### 2.1 AI Personality Engine (AURUS Brain)

| Sub-feature | File(s) | Grade |
|---|---|---|
| Gemini API integration | [aurus_brain.py](file:///c:/UOM/L1S1/Group%20project%20L%21/AURUS%20Code/aurus_brain.py) | **A** |
| System prompt / persona | [aurus_brain.py#L17-L69](file:///c:/UOM/L1S1/Group%20project%20L%21/AURUS%20Code/aurus_brain.py#L17-L69) | **A+** |
| Conversation memory (20-turn rolling) | [aurus_brain.py#L88-L130](file:///c:/UOM/L1S1/Group%20project%20L%21/AURUS%20Code/aurus_brain.py#L88-L130) | **B+** |
| State injection into context | [aurus_brain.py#L134-L154](file:///c:/UOM/L1S1/Group%20project%20L%21/AURUS%20Code/aurus_brain.py#L134-L154) | **A** |
| Local fallback brain (rule-based) | [aurus_brain.py#L157-L241](file:///c:/UOM/L1S1/Group%20project%20L%21/AURUS%20Code/aurus_brain.py#L157-L241) | **A** |
| Idle thought generation | [aurus_brain.py#L334-L386](file:///c:/UOM/L1S1/Group%20project%20L%21/AURUS%20Code/aurus_brain.py#L334-L386) | **B+** |
| Treat & obstacle personality reactions | [aurus_brain.py#L388-L414](file:///c:/UOM/L1S1/Group%20project%20L%21/AURUS%20Code/aurus_brain.py#L388-L414) | **A** |
| JSON response validation/sanitization | [aurus_brain.py#L296-L304](file:///c:/UOM/L1S1/Group%20project%20L%21/AURUS%20Code/aurus_brain.py#L296-L304) | **A-** |

**Strengths:** Excellent persona design, graceful Gemini→fallback degradation, structured JSON output enforcement, response sanitization.

> [!WARNING]
> **Reliability concerns:**
> - No retry logic on Gemini API failures — a single network hiccup silently falls back
> - `json.loads()` on Gemini response at [L294](file:///c:/UOM/L1S1/Group%20project%20L%21/AURUS%20Code/aurus_brain.py#L294) can throw `JSONDecodeError` if Gemini returns malformed JSON — the `except Exception` catches it, but there's no logging of *which* kind of failure occurred
> - Conversation memory is in-memory only — a restart wipes all history
> - No unit tests for the brain module at all

---

### 2.2 Mecanum Drive System

| Sub-feature | File(s) | Grade |
|---|---|---|
| Mecanum kinematics (vx, vy, omega) | [motors.py#L94-L149](file:///c:/UOM/L1S1/Group%20project%20L%21/AURUS%20Code/motors.py#L94-L149) | **A** |
| Speed normalization | [motors.py#L117-L122](file:///c:/UOM/L1S1/Group%20project%20L%21/AURUS%20Code/motors.py#L117-L122) | **A** |
| Global speed multiplier | [motors.py#L90-L92](file:///c:/UOM/L1S1/Group%20project%20L%21/AURUS%20Code/motors.py#L90-L92) | **A** |
| Animation library (wiggle, shiver, spin) | [motors.py#L154-L186](file:///c:/UOM/L1S1/Group%20project%20L%21/AURUS%20Code/motors.py#L154-L186) | **B+** |
| Simulation coordinate tracking | [motors.py#L188-L220](file:///c:/UOM/L1S1/Group%20project%20L%21/AURUS%20Code/motors.py#L188-L220) | **A-** |
| Motor direction & swap config | [config.py#L47-L57](file:///c:/UOM/L1S1/Group%20project%20L%21/AURUS%20Code/config.py#L47-L57) | **A** |
| Hardware GPIO setup | [motors.py#L44-L67](file:///c:/UOM/L1S1/Group%20project%20L%21/AURUS%20Code/motors.py#L44-L67) | **A-** |

**Strengths:** Proper thread-safe locking, correct Mecanum kinematic formula, smart speed normalization preventing motor overflow, clean hardware/simulation dual-mode.

> [!WARNING]
> **Reliability concerns:**
> - Animations (`wiggle`, `shiver`, `spin`) spawn **unbounded daemon threads** — calling wiggle 100 times spawns 100 threads with no cancellation mechanism
> - Animations can conflict with each other and with `drive()` calls since they internally call `drive()` repeatedly — race condition
> - `_sim_loop` runs at 20Hz (`sleep(0.05)`) but `dt` computation doesn't account for thread scheduling jitter
> - No watchdog timer — if `drive()` is called but `stop()` is never called (e.g., due to an exception), motors run indefinitely

---

### 2.3 Proximity Sensor System (5× HC-SR04)

| Sub-feature | File(s) | Grade |
|---|---|---|
| 5-sensor physical reading with timeout | [sensors.py#L50-L77](file:///c:/UOM/L1S1/Group%20project%20L%21/AURUS%20Code/sensors.py#L50-L77) | **A-** |
| Raycasting simulation (walls + circles) | [sensors.py#L79-L142](file:///c:/UOM/L1S1/Group%20project%20L%21/AURUS%20Code/sensors.py#L79-L142) | **A** |
| Aggregate methods (front_min, rear_min) | [sensors.py#L198-L206](file:///c:/UOM/L1S1/Group%20project%20L%21/AURUS%20Code/sensors.py#L198-L206) | **A** |
| Sensor noise simulation | [sensors.py#L144-L147](file:///c:/UOM/L1S1/Group%20project%20L%21/AURUS%20Code/sensors.py#L144-L147) | **B+** |

**Strengths:** Mathematically correct ray-circle intersection, proper timeout handling on physical sensors, realistic noise injection.

> [!WARNING]
> **Reliability concerns:**
> - `read_front_min()` and `read_rear_min()` each call their individual sensor methods, which means they re-read sensors each time — calling `read_front_min()` then `read_front()` does **6 sensor reads** instead of 3, wasting time and getting inconsistent data
> - Physical sensor reads have no averaging/filtering — a single noisy spike can trigger emergency stops
> - No sensor health monitoring — if a sensor cable disconnects, it will return 400cm (max range) silently

---

### 2.4 Drive Mode System (Manual / Autonomous / Voice Command)

| Sub-feature | File(s) | Grade |
|---|---|---|
| Mode transition state machine | [app.py#L76-L111](file:///c:/UOM/L1S1/Group%20project%20L%21/AURUS%20Code/app.py#L76-L111) | **A** |
| Autonomous exploration loop | [app.py#L264-L340](file:///c:/UOM/L1S1/Group%20project%20L%21/AURUS%20Code/app.py#L264-L340) | **A-** |
| Voice command movement bursts | [app.py#L184-L260](file:///c:/UOM/L1S1/Group%20project%20L%21/AURUS%20Code/app.py#L184-L260) | **A** |
| Obstacle avoidance (auto mode) | [app.py#L298-L315](file:///c:/UOM/L1S1/Group%20project%20L%21/AURUS%20Code/app.py#L298-L315) | **A** |
| Mid-burst safety checks (voice mode) | [app.py#L236-L258](file:///c:/UOM/L1S1/Group%20project%20L%21/AURUS%20Code/app.py#L236-L258) | **A** |
| Emergency stop → manual fallback | [app.py#L284-L296](file:///c:/UOM/L1S1/Group%20project%20L%21/AURUS%20Code/app.py#L284-L296) | **A** |
| Mode-guarded manual controls | [app.py#L719-L740](file:///c:/UOM/L1S1/Group%20project%20L%21/AURUS%20Code/app.py#L719-L740) | **A** |

**Strengths:** Clean threading with `Event` primitives, motor stop on every mode transition, cancellation tokens for voice bursts, smart turn-direction logic comparing FL vs FR sensors.

> [!WARNING]
> **Reliability concerns:**
> - The autonomous loop at [L267](file:///c:/UOM/L1S1/Group%20project%20L%21/AURUS%20Code/app.py#L267) is a `while True` with no exception handling — any unhandled exception kills the exploration thread silently
> - `set_drive_mode()` emits SocketIO events *outside* the lock at [L110](file:///c:/UOM/L1S1/Group%20project%20L%21/AURUS%20Code/app.py#L110), which is correct, but there's a window where another thread could read stale `rover_state["drive_mode"]`
> - The autonomous forward drive at [L339](file:///c:/UOM/L1S1/Group%20project%20L%21/AURUS%20Code/app.py#L339) starts motors but **never stops them** within the same iteration — relies on the next loop tick to evaluate if stopping is needed

---

### 2.5 Emotional Engine

| Sub-feature | File(s) | Grade |
|---|---|---|
| 1Hz mood decay loop | [app.py#L540-L672](file:///c:/UOM/L1S1/Group%20project%20L%21/AURUS%20Code/app.py#L540-L672) | **A-** |
| Happiness/Curiosity/Fear state model | [app.py#L30-L40](file:///c:/UOM/L1S1/Group%20project%20L%21/AURUS%20Code/app.py#L30-L40) | **A** |
| Idle → sleep transition (120s) | [app.py#L613-L616](file:///c:/UOM/L1S1/Group%20project%20L%21/AURUS%20Code/app.py#L613-L616) | **B+** |
| Sensor-driven fear/curiosity responses | [app.py#L563-L606](file:///c:/UOM/L1S1/Group%20project%20L%21/AURUS%20Code/app.py#L563-L606) | **A** |
| Autonomous idle fidgets (manual mode) | [app.py#L636-L645](file:///c:/UOM/L1S1/Group%20project%20L%21/AURUS%20Code/app.py#L636-L645) | **B+** |
| AURUS brain idle thoughts | [app.py#L674-L688](file:///c:/UOM/L1S1/Group%20project%20L%21/AURUS%20Code/app.py#L674-L688) | **B+** |

> [!WARNING]
> **Reliability concerns:**
> - The mood engine at [L676-L688](file:///c:/UOM/L1S1/Group%20project%20L%21/AURUS%20Code/app.py#L674-L688) calls `brain.generate_idle_thought()` and `execute_brain_action()` **inside** the `state_lock` — this is a major issue because `generate_idle_thought()` may call Gemini API (blocking I/O for seconds), holding the global lock and **deadlocking** other threads
> - No unit tests for emotional decay rates or state transitions

---

### 2.6 Voice Recognition

| Sub-feature | File(s) | Grade |
|---|---|---|
| Server-side wake-word listener | [voice_listener.py](file:///c:/UOM/L1S1/Group%20project%20L%21/AURUS%20Code/voice_listener.py) | **B+** |
| Multiple wake-word variants | [config.py#L15](file:///c:/UOM/L1S1/Group%20project%20L%21/AURUS%20Code/config.py#L15) | **A** |
| Browser-side Web Speech API | [app.js#L328-L507](file:///c:/UOM/L1S1/Group%20project%20L%21/AURUS%20Code/static/js/app.js#L328-L507) | **A-** |

**Strengths:** Dual voice paths (server mic + browser mic), phonetic wake-word variants, continuous listening with auto-restart.

> [!WARNING]
> **Reliability concerns:**
> - Google Web Speech API requires internet — no offline fallback
> - Server listener at [L63](file:///c:/UOM/L1S1/Group%20project%20L%21/AURUS%20Code/voice_listener.py#L63) has a 3-second timeout — in noisy environments this will miss wake-words
> - No voice activity detection (VAD) — processes all audio even when clearly no speech

---

### 2.7 Text-to-Speech (Gemini Native Audio)

| Sub-feature | File(s) | Grade |
|---|---|---|
| Gemini audio modality TTS | [app.py#L465-L500](file:///c:/UOM/L1S1/Group%20project%20L%21/AURUS%20Code/app.py#L465-L500) | **A-** |
| Cross-platform audio playback | [app.py#L168-L180](file:///c:/UOM/L1S1/Group%20project%20L%21/AURUS%20Code/app.py#L168-L180) | **B** |
| Browser-side Web Audio synth sounds | [app.js#L64-L109](file:///c:/UOM/L1S1/Group%20project%20L%21/AURUS%20Code/static/js/app.js#L64-L109) | **A-** |

> [!CAUTION]
> **Reliability concerns:**
> - Audio file is always saved to `response.wav` at [L462](file:///c:/UOM/L1S1/Group%20project%20L%21/AURUS%20Code/app.py#L462) — if two users talk simultaneously, they overwrite each other's audio file (race condition)
> - No audio queue — if two responses arrive quickly, the second overwrites the first before playback completes

---

### 2.8 Web Dashboard (Flask + SocketIO)

| Sub-feature | File(s) | Grade |
|---|---|---|
| Real-time telemetry broadcast (1Hz) | [app.py#L647-L672](file:///c:/UOM/L1S1/Group%20project%20L%21/AURUS%20Code/app.py#L647-L672) | **A** |
| Animated digital face (6 expressions) | [face.js](file:///c:/UOM/L1S1/Group%20project%20L%21/AURUS%20Code/static/js/face.js) | **A+** |
| Sonar radar visualization | [app.js#L810-L895](file:///c:/UOM/L1S1/Group%20project%20L%21/AURUS%20Code/static/js/app.js#L810-L895) | **A** |
| 2D simulation arena renderer | [app.js#L668-L806](file:///c:/UOM/L1S1/Group%20project%20L%21/AURUS%20Code/static/js/app.js#L668-L806) | **A** |
| WASD + mouse control pad | [app.js#L567-L664](file:///c:/UOM/L1S1/Group%20project%20L%21/AURUS%20Code/static/js/app.js#L567-L664) | **A** |
| Chat terminal with typing indicators | [app.js#L288-L326](file:///c:/UOM/L1S1/Group%20project%20L%21/AURUS%20Code/static/js/app.js#L288-L326) | **A** |
| Inner thought overlay with fade | [app.js#L243-L286](file:///c:/UOM/L1S1/Group%20project%20L%21/AURUS%20Code/static/js/app.js#L243-L286) | **A** |
| Gemini API key config modal | [app.js#L528-L565](file:///c:/UOM/L1S1/Group%20project%20L%21/AURUS%20Code/static/js/app.js#L528-L565) | **B+** |
| E-STOP button | [app.py#L742-L751](file:///c:/UOM/L1S1/Group%20project%20L%21/AURUS%20Code/app.py#L742-L751) | **A** |
| Speed limiter slider | [app.js#L627-L630](file:///c:/UOM/L1S1/Group%20project%20L%21/AURUS%20Code/static/js/app.js#L627-L630) | **A** |
| Mode-locked control pad overlay | [app.js#L30-L53](file:///c:/UOM/L1S1/Group%20project%20L%21/AURUS%20Code/static/js/app.js#L30-L53) | **A** |
| Premium dark theme CSS | [style.css](file:///c:/UOM/L1S1/Group%20project%20L%21/AURUS%20Code/static/css/style.css) | **A** |

> [!WARNING]
> **Reliability concerns:**
> - No WebSocket reconnection logic — if the SocketIO connection drops, the dashboard goes permanently dead with no user-visible error
> - Chat terminal grows unbounded — no message limit, will consume unlimited memory in long sessions
> - `cors_allowed_origins="*"` at [app.py#L22](file:///c:/UOM/L1S1/Group%20project%20L%21/AURUS%20Code/app.py#L22) — any origin can connect

---

### 2.9 Configuration & Environment

| Sub-feature | File(s) | Grade |
|---|---|---|
| Centralized config module | [config.py](file:///c:/UOM/L1S1/Group%20project%20L%21/AURUS%20Code/config.py) | **A** |
| `.env` file support | [config.py#L3-L7](file:///c:/UOM/L1S1/Group%20project%20L%21/AURUS%20Code/config.py#L3-L7) | **A** |
| Graceful hardware fallback | All modules | **A+** |

---

### 2.10 Testing

| Sub-feature | File(s) | Grade |
|---|---|---|
| Locomotion unit tests (4 tests) | [test_suite.py#L14-L57](file:///c:/UOM/L1S1/Group%20project%20L%21/AURUS%20Code/test_suite.py#L14-L57) | **A** |
| Sensor raycasting tests (8 tests) | [test_suite.py#L59-L147](file:///c:/UOM/L1S1/Group%20project%20L%21/AURUS%20Code/test_suite.py#L59-L147) | **A** |
| Drive mode safety tests (5 tests) | [test_suite.py#L149-L219](file:///c:/UOM/L1S1/Group%20project%20L%21/AURUS%20Code/test_suite.py#L149-L219) | **A** |

> [!CAUTION]
> **Major gap:** 17 tests cover motors, sensors, and thresholds, but there are **zero tests** for:
> - `aurus_brain.py` (AI responses, memory, fallback logic)
> - `app.py` (SocketIO handlers, mode transitions with full state, emotional engine)
> - `voice_listener.py` (wake-word detection parsing)
> - Edge cases (concurrent mode switches, rapid-fire commands)

---

## 3. Critical Reliability Issues (Prioritized)

### 🔴 P0 — Must Fix for Reliability

| # | Issue | Location | Impact |
|---|---|---|---|
| 1 | **Lock held during Gemini API call** — `mood_engine_loop()` calls `brain.generate_idle_thought()` inside `state_lock`, which can make a blocking HTTP call to Gemini. This deadlocks the entire system for seconds. | [app.py#L674-L688](file:///c:/UOM/L1S1/Group%20project%20L%21/AURUS%20Code/app.py#L674-L688) | **System freeze** |
| 2 | **Autonomous loop has no exception handler** — any unhandled exception in `autonomous_explore_loop()` kills the thread silently. The rover continues driving forward with no obstacle avoidance. | [app.py#L264-L342](file:///c:/UOM/L1S1/Group%20project%20L%21/AURUS%20Code/app.py#L264-L342) | **Safety hazard** |
| 3 | **Audio file race condition** — `response.wav` is shared across all concurrent interactions. Simultaneous users corrupt each other's audio. | [app.py#L462](file:///c:/UOM/L1S1/Group%20project%20L%21/AURUS%20Code/app.py#L462) | **Data corruption** |
| 4 | **Unbounded animation threads** — `wiggle()`, `shiver()`, `spin()` each spawn a new daemon thread with no limit or cancellation. Rapid triggering creates dozens of conflicting motor control threads. | [motors.py#L154-L186](file:///c:/UOM/L1S1/Group%20project%20L%21/AURUS%20Code/motors.py#L154-L186) | **Erratic motor behavior** |

### 🟠 P1 — Should Fix

| # | Issue | Location | Impact |
|---|---|---|---|
| 5 | **No WebSocket reconnection** — a dropped connection renders the dashboard permanently unresponsive. | [app.js#L2](file:///c:/UOM/L1S1/Group%20project%20L%21/AURUS%20Code/static/js/app.js#L2) | **UI failure** |
| 6 | **No sensor reading averaging** — single noisy spikes trigger emergency stops. Should use median/moving average filter. | [sensors.py#L50-L77](file:///c:/UOM/L1S1/Group%20project%20L%21/AURUS%20Code/sensors.py#L50-L77) | **False alarms** |
| 7 | **Motor watchdog missing** — if `drive()` is called and `stop()` is never reached (e.g., exception in calling code), motors run indefinitely. | [motors.py](file:///c:/UOM/L1S1/Group%20project%20L%21/AURUS%20Code/motors.py) | **Safety hazard** |
| 8 | **No Gemini API retry/backoff** — transient network errors immediately fall back to the local brain. | [aurus_brain.py#L277-L310](file:///c:/UOM/L1S1/Group%20project%20L%21/AURUS%20Code/aurus_brain.py#L277-L310) | **Degraded AI** |
| 9 | **Autonomous forward drive never explicitly stops** — at [L339](file:///c:/UOM/L1S1/Group%20project%20L%21/AURUS%20Code/app.py#L339), `driver.drive()` is called but no matching `stop()` within that branch. | [app.py#L337-L340](file:///c:/UOM/L1S1/Group%20project%20L%21/AURUS%20Code/app.py#L337-L340) | **Continuous driving** |

### 🟡 P2 — Nice to Fix

| # | Issue | Location | Impact |
|---|---|---|---|
| 10 | **Chat terminal unbounded growth** — no message limit, memory leak in long sessions. | [app.js#L306-L326](file:///c:/UOM/L1S1/Group%20project%20L%21/AURUS%20Code/static/js/app.js#L306-L326) | **Memory leak** |
| 11 | **CORS wide open** (`*`) — any website can connect to the SocketIO server. | [app.py#L22](file:///c:/UOM/L1S1/Group%20project%20L%21/AURUS%20Code/app.py#L22) | **Security risk** |
| 12 | **API key exposed in `.env`** and committed to repo. | [.env#L2](file:///c:/UOM/L1S1/Group%20project%20L%21/AURUS%20Code/.env#L2) | **Security risk** |
| 13 | **No `.gitignore`** — `.env`, `__pycache__`, `response.wav` likely tracked. | Project root | **Security/hygiene** |
| 14 | **Redundant sensor reads** — `read_front_min()` re-reads all 3 front sensors independently of `read_all()`. | [sensors.py#L198-L201](file:///c:/UOM/L1S1/Group%20project%20L%21/AURUS%20Code/sensors.py#L198-L201) | **Performance** |
| 15 | **No test coverage for brain, voice, app** — only hardware simulation is tested. | [test_suite.py](file:///c:/UOM/L1S1/Group%20project%20L%21/AURUS%20Code/test_suite.py) | **Quality gap** |

---

## 4. Architecture Strengths ✅

The system gets a lot of things right:

1. **Dual-mode design** — Every hardware module (GPIO, sensors, microphone) gracefully degrades to simulation. The system runs identically on Windows and Pi.
2. **Thread safety** — Global `state_lock` protects shared state; `threading.Event` coordinates mode transitions.
3. **Separation of concerns** — Clean module boundaries: `motors.py`, `sensors.py`, `aurus_brain.py`, `voice_listener.py`, `config.py`.
4. **Safety-first autonomous driving** — Emergency proximity → manual revert, pre-flight and mid-burst safety checks in voice mode.
5. **Personality continuity** — The fallback brain preserves the *identical* alien persona when Gemini is unavailable.
6. **Configurable everything** — Motor directions, pin assignments, speed limits, decay rates, API settings all in [config.py](file:///c:/UOM/L1S1/Group%20project%20L%21/AURUS%20Code/config.py).

---

## 5. Recommendations Summary

To make this system **highly reliable**, I recommend addressing the issues in priority order (P0 first). Would you like me to proceed with implementing fixes?

| Priority | Count | Effort |
|---|---|---|
| 🔴 P0 (Must Fix) | 4 issues | ~2-3 hours |
| 🟠 P1 (Should Fix) | 5 issues | ~3-4 hours |
| 🟡 P2 (Nice to Fix) | 6 issues | ~2-3 hours |

> [!IMPORTANT]
> The most dangerous issue is **#1 (lock held during API call)** — this can freeze the entire rover mid-drive. The fix is straightforward: move the idle thought generation outside the lock scope, taking a snapshot of state first and releasing the lock before calling the brain.
