# AURUS MCP Agent Commands Guide

AURUS uses an intelligent Agentic Router powered by Groq. You don't need to use exact syntax—you can talk to it completely naturally via text or voice!

The system will automatically figure out if you want to **CHAT** (casual conversation) or **ACT** (move, scan, explore, remember).

Here are the primary commands and capabilities you can test in the dashboard or via voice:

## 🧭 Movement & Navigation
*(Note: Since your rear wheels are identical to the front wheels, "strafing" sideways won't work, but forward/backward and spinning will work perfectly!)*

* **Drive Forward/Backward:**
  * *"Move forward for 3 seconds"*
  * *"Drive backward slowly"*
* **Spinning (Turning):**
  * *"Turn left"*
  * *"Spin around to the right for 2 seconds"*
* **Stopping:**
  * *"Stop!"*
  * *"Halt all motors"*

## 🦇 Sensors & Autonomous Exploration
* **Distance Checks:**
  * *"Check your sensors"*
  * *"Is the path clear in front of you?"*
* **Exploration:**
  * *"Explore the room!"*
  * *(During exploration, AURUS will autonomously look around and narrate what it sees using Groq-hosted vision.)*

## 👁️ Vision & Camera
* **Person Detection:**
  * *"Look forward, do you see anyone?"*
  * *"Who is in front of you?"*

## 🗣️ Speech & Identity
* **Conversational Chat (Powered by Groq GPT-OSS & Whisper STT):**
  * *"Hey AURUS, what does your name stand for?"*
  * *"Introduce yourself!"*
  * *"Tell me about your hardware components"*

## 🧠 Persistent Memory (SQLite Database)
*(Requires a recognized person/user ID first)*
* **Remembering:**
  * *"Remember that my favorite color is blue"*
  * *"Store a fact: I am the creator"*
* **Recalling:**
  * *"What do you remember about me?"*

## 📊 System Status & Health
* **Diagnostics:**
  * *"What is your current status?"*
  * *"Check your system health"*

## 🎭 Animations & Expressions
* **Physical Emotes:**
  * *"Do a happy wiggle"*
  * *"Shiver"* (Scared/Cold)

---
### 💡 Pro Tip for Testing:
You can combine ideas into natural sentences! For example:
> *"Check your sensors, and if the path is clear, move forward for 2 seconds and say hello!"*
