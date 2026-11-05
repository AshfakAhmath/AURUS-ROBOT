"""Groq MCP Agent Service for autonomous tool calling from the dashboard."""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Any

from src.core.models import RobotMode
from src.services.motion_execution import execute_sequence
from src.services.distance_motion import prepare_movement, distance_feedback

try:
    from groq import Groq
except ImportError:
    Groq = None


# Standard OpenAI/Groq Tool Schemas mapping to AURUS MCP capabilities
AURUS_MCP_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "move_rover",
            "description": "Move AURUS in a linear direction (translating/strafing). Do NOT use this for turning or spinning.",
            "parameters": {
                "type": "object",
                "properties": {
                    "direction": {
                        "type": "string",
                        "enum": [
                            "forward",
                            "backward",
                            "left",
                            "right",
                            "forward_left",
                            "forward_right",
                            "backward_left",
                            "backward_right",
                        ],
                        "description": "Direction to drive. 'left' means strafe left sideways.",
                    },
                    "speed": {
                        "type": "number",
                        "description": "Speed from 0.0 to 1.0 (default 1.0).",
                        "default": 1.0,
                    },
                    "duration": {
                        "type": "number",
                        "description": "Seconds to drive (default 1.0).",
                        "default": 1.0,
                    },
                    "distance_cm": {
                        "type": "number",
                        "description": "Optional estimated distance in centimeters for forward/backward/left/right. Requires measured calibration at the selected speed; overrides duration.",
                    },
                },
                "required": ["direction"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "spin_rover",
            "description": "Spin or turn AURUS in place (rotate). Use this whenever the user asks to 'turn left' or 'turn right'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "direction": {
                        "type": "string",
                        "enum": ["left", "right"],
                        "default": "left",
                    },
                    "speed": {
                        "type": "number",
                        "default": 1.0,
                    },
                    "duration": {
                        "type": "number",
                        "default": 1.0,
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stop_rover",
            "description": "Immediately stop all motors. Call after movement or for emergency stop.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_sensors",
            "description": "Read all 5 ultrasonic distance sensors in centimeters and check if front/rear are clear.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "capture_image",
            "description": "Analyze the current camera photo to describe visible surroundings and check face identity.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "speak_text",
            "description": "Make AURUS speak a sentence aloud using text-to-speech through the speaker.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "The words to say aloud (max 300 chars)."}
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remember_fact",
            "description": "Store a fact in AURUS persistent SQLite memory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "fact": {"type": "string", "description": "The text to remember."},
                    "user_id": {"type": "integer", "default": 1},
                },
                "required": ["fact"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "recall_memories",
            "description": "Retrieve all stored memories and facts about a person.",
            "parameters": {
                "type": "object",
                "properties": {"user_id": {"type": "integer", "default": 1}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_rover_status",
            "description": "Get current status of AURUS including position, mode, battery/voltage simulation, and health.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "perform_animation",
            "description": "Play a predefined physical motion animation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "enum": ["wiggle", "shiver", "spin"],
                        "description": "'wiggle' (happy), 'shiver' (scared), 'spin' (curious).",
                    }
                },
                "required": ["name"],
            },
        },
    },
]


class MCPAgentService:
    def __init__(self, runtime: Any, api_key: str | None = None, model: str | None = None):
        self.runtime = runtime
        self.api_key = api_key or os.getenv("GROQ_API_KEY", "")
        self.model = model or os.getenv("GROQ_AGENT_MODEL", "openai/gpt-oss-120b")
        self.chat_model = os.getenv("GROQ_CHAT_MODEL", "openai/gpt-oss-20b")
        self.router_model = os.getenv("GROQ_ROUTER_MODEL", self.chat_model)
        self._client = None
        self.error = ""
        self._execution_lock = threading.Lock()
        if self.api_key and Groq is not None:
            try:
                self._client = Groq(api_key=self.api_key, timeout=15.0, max_retries=1)
            except Exception as exc:
                self.error = f"Groq client initialization failed: {str(exc)[:180]}"
        elif Groq is None:
            self.error = "groq SDK is not installed"
        else:
            self.error = "GROQ_API_KEY is not configured"

    @property
    def ready(self) -> bool:
        return self._client is not None

    @property
    def busy(self) -> bool:
        return self._execution_lock.locked()

    def execute_command(self, user_command: str) -> dict:
        if not self._execution_lock.acquire(blocking=False):
            msg = "The MCP Agent is already executing a command. Stop it or wait for it to finish."
            self.runtime.emit(
                "conversation",
                {
                    "source": "agent",
                    "transcript": user_command,
                    "response": msg,
                    "provider": "local",
                    "fallback_reason": "agent busy",
                },
            )
            return {"status": "error", "message": msg}
        try:
            if getattr(self.runtime, "demo_active", False):
                return {"status": "error", "message": "Stop the FIT Expo demo before running the MCP agent."}
            return self._execute_command(user_command)
        finally:
            self._execution_lock.release()

    def _execute_command(self, user_command: str) -> dict:
        """Executes a natural language instructions using Groq tool calling loop."""
        if not self._client:
            msg = "⚡ Groq MCP Agent is unavailable. Please check that 'groq' is installed and GROQ_API_KEY is set in your .env file."
            self.runtime.emit(
                "conversation",
                {
                    "source": "agent",
                    "transcript": user_command,
                    "response": msg,
                    "provider": "local",
                    "fallback_reason": "missing key/sdk",
                },
            )
            return {"status": "error", "message": msg}

        # Inform dashboard that agent started thinking
        self.runtime.emit(
            "conversation",
            {
                "source": "agent",
                "transcript": f"[MCP Agent] {user_command}",
                "response": "🧠 Groq Llama-3 agent received objective. Planning tool sequence...",
                "provider": "groq-mcp",
                "fallback_reason": "",
            },
        )

        system_prompt = (
            "You are the autonomous cloud AI brain controlling AURUS, a 4-wheel mecanum rover with "
            "ultrasonic sensors, a camera, text-to-speech, and persistent SQLite memory. "
            "When given a goal, invoke the appropriate tools to accomplish it safely. "
            "Always check sensors if safety or obstacles are mentioned. Keep verbal speech concise and warm. "
            "For any question about what you see or the environment, call capture_image and report its "
            "description without inventing or adding visual details. "
            "IMPORTANT: Always use native JSON tool calls. NEVER output raw <function> tags in your response text. "
            "If the user just says 'hi', use the speak_text tool to greet them."
            " A blocked tool result cancels this entire objective: never retry, reroute, or resume. "
            "Never choose an alternative direction around an obstacle without a fresh user request. "
            "If distance calibration is missing or mismatched, explain the error and do not substitute timed movement. "
            "Explain the block in one short sentence using obstacle_direction and only a non-null "
            "obstacle_distance_cm. Do not invent distances or claim interrupted motion completed. "
            "Distance requests use distance_cm, and completed distances are estimates without encoders. "
            "The final explanation is spoken automatically; do not call speak_text for it."
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_command[:1000]},
        ]

        iterations = 0
        max_iterations = 6
        final_summary = ""
        spoke_during_run = False
        blocked_result = None


        try:
            while iterations < max_iterations:
                iterations += 1
                completion = self._client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    tools=AURUS_MCP_TOOLS,
                    tool_choice="none" if blocked_result else "auto",
                    temperature=0.2,
                    max_completion_tokens=700,
                    reasoning_effort="low",
                )

                response_message = completion.choices[0].message
                tool_calls = response_message.tool_calls

                if not tool_calls:
                    final_summary = (response_message.content or "Task completed.").strip()
                    break

                messages.append(response_message)

                for tool_call in tool_calls:
                    fn_name = tool_call.function.name
                    raw_args = tool_call.function.arguments
                    try:
                        args = __import__('json').loads(raw_args) if raw_args else {}
                    except Exception:
                        args = {}

                    # Format arguments beautifully
                    args_str = ", ".join(f"{k}='{v}'" if isinstance(v, str) else f"{k}={v}" for k, v in args.items())
                    display_args = f" with {args_str}" if args_str else ""

                    # Notify dashboard of live tool call
                    self.runtime.emit(
                        "mcp_tool_exec",
                        {
                            "id": tool_call.id,
                            "tool": fn_name,
                            "arguments": args,
                            "status": "calling",
                        },
                    )
                    self.runtime.emit(
                        "conversation",
                        {
                            "source": "tool",
                            "transcript": "",
                            "response": f"⚡ Running action: {fn_name}{display_args}",
                            "provider": "mcp-tool",
                            "fallback_reason": "",
                        },
                    )

                    # Execute tool against live runtime
                    if blocked_result:
                        result_json = json.dumps({
                            **blocked_result,
                            "message": "Action cancelled. A fresh user command is required.",
                        })
                    else:
                        result_json = self._dispatch_tool(fn_name, args)
                    result_data = json.loads(result_json)
                    if result_data.get("status") == "blocked" or (
                        fn_name in ("move_rover", "spin_rover", "perform_animation")
                        and result_data.get("status") == "error"
                    ):
                        blocked_result = result_data
                    if fn_name == "speak_text":
                        try:
                            spoke_during_run = json.loads(result_json).get("status") == "ok"
                        except (TypeError, ValueError, json.JSONDecodeError):
                            pass

                    # Parse result for clean UI
                    try:
                        res_dict = __import__('json').loads(result_json)
                        if "message" in res_dict:
                            res_str = res_dict["message"]
                        elif "action" in res_dict:
                            res_str = res_dict["action"]
                        elif "distances_cm" in res_dict:
                            res_str = f"Front clear: {res_dict.get('front_clear', False)}, Front closest: {res_dict.get('front_min_cm', 0)}cm"
                        elif "detected_person" in res_dict:
                            name = res_dict["detected_person"].get("name", "Unknown")
                            res_str = f"Seen: {name}"
                        elif "memories" in res_dict:
                            res_str = f"Found {res_dict.get('count', 0)} memories."
                        elif "health" in res_dict:
                            mode = res_dict.get("mode", "unknown")
                            res_str = f"Robot is in {mode} mode."
                        else:
                            res_str = "Success" if res_dict.get("status") == "ok" else "Error occurred"
                    except Exception:
                        res_str = "Finished execution."

                    self.runtime.emit(
                        "mcp_tool_exec",
                        {
                            "id": tool_call.id,
                            "tool": fn_name,
                            "arguments": args,
                            "result": __import__('json').loads(result_json) if result_json.startswith("{") else result_json,
                            "status": "completed",
                        },
                    )
                    self.runtime.emit(
                        "conversation",
                        {
                            "source": "tool_res",
                            "transcript": "",
                            "response": f"➔ Result: {res_str}",
                            "provider": "mcp-res",
                            "fallback_reason": "",
                        },
                    )

                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "name": fn_name,
                            "content": str(result_json),
                        }
                    )

            if not final_summary:
                final_summary = (
                    "Movement was cancelled: " + blocked_result.get("reason", blocked_result.get("message", "the path could not be evaluated safely"))
                    if blocked_result else f"Agent execution ended after {iterations} turns."
                )

            if blocked_result or not spoke_during_run:
                self._speak_once(final_summary)
            try:
                person = self.runtime._current_person()
                self.runtime.repository.log_interaction(
                    person.user_id, "mcp-agent", user_command, final_summary, "groq-mcp"
                )
            except Exception:
                pass

            self.runtime.emit(
                "conversation",
                {
                    "source": "agent_done",
                    "transcript": "",
                    "response": f"🤖 {final_summary}",
                    "provider": "groq-mcp",
                    "fallback_reason": "",
                },
            )
            return {"status": blocked_result["status"] if blocked_result else "ok", "summary": final_summary, "turns": iterations}

        except Exception as exc:
            self.error = f"Groq agent request failed: {str(exc)[:180]}"
            if blocked_result:
                explanation = "Movement was cancelled: " + blocked_result.get("reason", blocked_result.get("message", "unsafe sensor coverage"))
                self._speak_once(explanation)
                self.runtime.emit("conversation", {"source": "agent_done", "response": explanation, "provider": "local"})
                return {"status": blocked_result["status"], "summary": explanation, "fallback_reason": self.error}
            self.runtime.emit(
                "conversation",
                {
                    "source": "agent",
                    "transcript": "",
                    "response": "My cloud request failed. Check the Groq key, network, model settings, and rate limits.",
                    "provider": "groq-mcp",
                    "fallback_reason": self.error,
                },
            )
            return {"status": "error", "message": self.error}

    def _speak_once(self, text: str) -> bool:
        try:
            return bool(self.runtime.tts.speak(text))
        except Exception:
            return False

    def _execute_motion_sequence(
        self,
        steps: list[tuple[float, float, float, float]],
    ) -> tuple[bool, str]:
        """Run an existing MCP motion through the same arbiter as every other behavior."""
        completed, reason, self._motion_feedback = execute_sequence(
            self.runtime.arbiter, steps, "mcp-agent"
        )
        return completed, reason

    def _dispatch_tool(self, name: str, args: dict) -> str:
        """Execute the requested MCP tool using the active robot runtime."""
        try:
            if name == "move_rover":
                direction = str(args.get("direction", "forward")).lower().replace(" ", "_")
                speed, duration, distance = prepare_movement(
                    direction, args.get("speed", 1.0), args.get("duration", 1.0), args.get("distance_cm")
                )

                direction_map = {
                    "forward": (speed, 0.0, 0.0),
                    "backward": (-speed, 0.0, 0.0),
                    "left": (0.0, -speed, 0.0),
                    "right": (0.0, speed, 0.0),
                    "forward_left": (speed, -speed, 0.0),
                    "forward_right": (speed, speed, 0.0),
                    "backward_left": (-speed, -speed, 0.0),
                    "backward_right": (-speed, speed, 0.0),
                }
                vels = direction_map.get(direction.lower().replace(" ", "_"))
                if vels is None:
                    return json.dumps({
                        "status": "error",
                        "message": f"Unknown movement direction: {direction}",
                    })
                completed, reason = self._execute_motion_sequence(
                    [(vels[0], vels[1], vels[2], duration)]
                )
                state = self.runtime.driver.get_simulation_state()
                return json.dumps(
                    {
                        "status": "ok" if completed else "blocked",
                        "action": f"Moved {direction} for {duration}s" if completed else "Movement stopped",
                        "reason": reason,
                        **getattr(self, "_motion_feedback", {}),
                        **distance_feedback(distance, duration, completed, getattr(self, "_motion_feedback", {})),
                        "simulation_mode": self.runtime.driver.is_simulation,
                        "position": {
                            "x_cm": round(state.get("x", 0.0), 1),
                            "y_cm": round(state.get("y", 0.0), 1),
                        },
                    }
                )

            elif name == "spin_rover":
                direction = str(args.get("direction", "left"))
                if direction.lower() not in ("left", "right"):
                    return json.dumps({
                        "status": "error",
                        "message": f"Unknown spin direction: {direction}",
                    })
                speed, duration, _ = prepare_movement(direction, args.get("speed", 1.0), args.get("duration", 1.0))
                omega = -speed if direction.lower() == "left" else speed
                completed, reason = self._execute_motion_sequence([(0.0, 0.0, omega, duration)])
                return json.dumps({
                    "status": "ok" if completed else "blocked",
                    "action": f"Spun {direction} for {duration}s" if completed else "Spin stopped",
                    "reason": reason,
                    **getattr(self, "_motion_feedback", {}),
                })

            elif name == "stop_rover":
                self.runtime.arbiter.halt("mcp-agent")
                if not self.runtime.arbiter.estopped:
                    self.runtime.arbiter.set_mode(RobotMode.IDLE)
                return json.dumps({"status": "ok", "message": "All motors stopped."})

            elif name == "read_sensors":
                snapshot = self.runtime.sensor_sampler.get_snapshot()
                data = snapshot.as_dict()
                center_enabled = snapshot.center_enabled(getattr(self.runtime.arbiter, "front_sensor_enabled", True))
                clearance = snapshot.navigation_front_min(center_enabled)
                caution = self.runtime.arbiter.caution_cm if center_enabled else self.runtime.arbiter.degraded_caution_cm
                return json.dumps({
                    "status": "ok", "distances_cm": {k: data[k] for k in ("fl", "f", "fr", "rl", "rr")},
                    "front_min_cm": clearance if data["coverage"]["front"] else None,
                    "rear_min_cm": data["rear_min"],
                    "front_clear": data["coverage"]["front"] and clearance >= caution,
                    "rear_clear": data["coverage"]["rear"] and snapshot.rear_min >= self.runtime.arbiter.caution_cm,
                    "sensors": data,
                }, allow_nan=False)

            elif name == "capture_image":
                snapshot = self.runtime.vision.get_snapshot()
                if not snapshot.healthy or snapshot.age > 1.0:
                    return json.dumps({
                        "status": "error",
                        "message": "No fresh camera frame is available.",
                        "camera_error": snapshot.error,
                    })
                jpeg = self.runtime.vision.get_jpeg()
                vision_provider = getattr(self.runtime, "groq_vision", None)
                description = vision_provider.describe(jpeg) if vision_provider else ""
                if not description:
                    return json.dumps({
                        "status": "error",
                        "message": "The current camera frame could not be analyzed.",
                    })
                identity = snapshot.identity
                return json.dumps(
                    {
                        "status": "ok",
                        "camera_healthy": snapshot.healthy,
                        "backend": snapshot.backend,
                        "description": description,
                        "face_count": len(snapshot.faces),
                        "detected_person": {
                            "name": identity.name or "Unknown",
                            "status": identity.status,
                            "confidence": round(float(identity.confidence or 0.0), 2),
                        },
                    }
                )

            elif name == "speak_text":
                text = str(args.get("text", ""))[:300]
                queued = self._speak_once(text)
                return json.dumps({"status": "ok" if queued else "error", "message": f"Speech queued: {text}" if queued else "Speech unavailable or queue full"})

            elif name == "remember_fact":
                fact = str(args.get("fact", ""))
                user_id = int(args.get("user_id", 1))
                if self.runtime.repository.get_user(user_id) is None:
                    return json.dumps({
                        "status": "error",
                        "message": f"Unknown user_id {user_id}; enroll or recognize a person first.",
                    })
                self.runtime.repository.remember(user_id, fact)
                return json.dumps({"status": "ok", "message": f"Remembered: {fact}"})

            elif name == "recall_memories":
                user_id = int(args.get("user_id", 1))
                if self.runtime.repository.get_user(user_id) is None:
                    return json.dumps({
                        "status": "error",
                        "message": f"Unknown user_id {user_id}; enroll or recognize a person first.",
                    })
                facts = self.runtime.repository.memories_for(user_id)
                return json.dumps({"status": "ok", "count": len(facts), "memories": facts[:10]})

            elif name == "get_rover_status":
                health = self.runtime.health()
                state = self.runtime.driver.get_simulation_state()
                return json.dumps(
                    {
                        "status": "ok",
                        "mode": str(self.runtime.arbiter.mode.value),
                        "simulation_mode": self.runtime.driver.is_simulation,
                        "health": health,
                        "position": {
                            "x_cm": round(state.get("x", 0.0), 1),
                            "y_cm": round(state.get("y", 0.0), 1),
                            "heading_deg": round(state.get("theta", 0.0) * 57.2958, 1),
                        },
                    }
                )

            elif name == "perform_animation":
                anim = str(args.get("name", "wiggle")).lower()
                animations: dict[str, list[tuple[float, float, float, float]]] = {
                    "wiggle": [(0.0, direction * 0.6, 0.0, 0.15) for _ in range(5) for direction in (1, -1)],
                    "shiver": [(direction * 0.4, 0.0, 0.0, 0.05) for _ in range(12) for direction in (1, -1)],
                    "spin": [(0.0, 0.0, 0.6, 1.5)],
                }
                sequence = animations.get(anim)
                if sequence is None:
                    return json.dumps({"status": "error", "message": f"Unknown animation: {anim}"})
                completed, reason = self._execute_motion_sequence(sequence)
                return json.dumps({
                    "status": "ok" if completed else "blocked",
                    "animation": anim,
                    "reason": reason,
                    **getattr(self, "_motion_feedback", {}),
                })

            else:
                return json.dumps({"status": "error", "message": f"Unknown tool name: {name}"})

        except Exception as exc:
            return json.dumps({"status": "error", "message": f"Tool execution error: {exc}"})

    def route_intent(self, text: str) -> str:
        """Lightweight semantic router to decide if command is an ACTION or CHAT."""
        normalized = " ".join(text.lower().split())
        action_terms = (
            "move", "drive", "go forward", "go backward", "reverse", "turn left",
            "turn right", "spin", "stop", "halt", "explore", "look around",
            "take a photo", "capture", "sensor", "dance", "showcase", "remember",
            "describe the environment", "describe my surroundings", "what do you see",
            "what can you see", "what is in front of you",
        )
        if any(term in normalized for term in action_terms):
            return "ACTION"
        if not self._client:
            return "CHAT"
        try:
            completion = self._client.chat.completions.create(
                model=self.router_model,
                messages=[
                    {
                        "role": "system",
                        "content": "You are a router. Classify the user intent. If they want the robot to move, stop, look around, explore, remember facts, or perform any physical task, output exactly: ACTION. If it is just a greeting, a question, or casual chat, output exactly: CHAT. Output NOTHING else."
                    },
                    {"role": "user", "content": text},
                ],
                temperature=0.1,
                max_completion_tokens=96,
                reasoning_effort="low",
            )
            result = (completion.choices[0].message.content or "CHAT").strip().upper()
            return "ACTION" if result == "ACTION" else "CHAT"
        except Exception as exc:
            self.error = f"Groq router failed: {str(exc)[:180]}"
            return "CHAT"

    def chat(self, text: str, user_name: str | None, memories: list[str]) -> str:
        """Fast cloud-based conversational fallback when tool execution is not needed."""
        if not self._client:
            return "My cloud brain is disconnected."

        system = (
            "You are AURUS, which stands for AUtonomous Robotic Ubiquitous System. "
            "You are an advanced interactive robot built by the TechTitans team. "
            "Your hardware components include a Raspberry Pi 4 brain, "
            "4 mecanum wheels with L298N drivers, HC-SR04 ultrasonic sensors for obstacle avoidance, "
            "and a high-resolution camera. "
            "Your software features include Agentic routing via Groq, autonomous visual exploration using Llama Vision, "
            "flawless speech recognition via Whisper, highly realistic Edge TTS, face recognition, and persistent memory. "
            "You can explore autonomously, avoid obstacles, recognize people, and showcase your mobility. "
            "Keep responses concise, warm, and natural for text-to-speech (max 2 sentences)."
        )
        if user_name:
            system += f" The user's name is {user_name}."
        if memories:
            system += f" Here are some facts you know about them: {'; '.join(memories)}"

        try:
            completion = self._client.chat.completions.create(
                model=self.chat_model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": text},
                ],
                temperature=0.6,
                max_completion_tokens=180,
                reasoning_effort="low",
            )
            return (completion.choices[0].message.content or "").strip()
        except Exception as exc:
            self.error = f"Groq chat failed: {str(exc)[:180]}"
            return "My cloud request failed. Please check the Groq connection and try again."
