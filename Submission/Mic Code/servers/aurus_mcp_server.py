"""
AURUS MCP Server — Exposes robot capabilities as MCP tools.

This server wraps the existing AURUS hardware drivers and services into
standardized MCP tools that any AI agent can call.  It auto-detects
simulation mode (on PC) vs real hardware (on Raspberry Pi).

Launch:
    python servers/aurus_mcp_server.py          # stdio mode (for IDE integration)

The server manages its own MecanumDriver, ProximitySensor, and TTSService
instances and must be run independently of the main AURUS runtime. The motor
driver enforces exclusive process ownership of the GPIO outputs.
"""

from __future__ import annotations

import atexit
import base64
import json
import os
import sys
import threading
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Path setup — ensure we can import from the project root
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

# FastMCP moved between SDK versions — try all known import paths.
# MCP 1.x:  mcp.server.fastmcp.FastMCP
# MCP 2.x:  mcp.fastmcp.FastMCP  OR  mcp.FastMCP (top-level)
FastMCP = None
for _import_path, _module, _attr in [
    ("mcp.server.fastmcp", "mcp.server.fastmcp", "FastMCP"),
    ("mcp.fastmcp",        "mcp.fastmcp",        "FastMCP"),
    ("mcp",                "mcp",                "FastMCP"),
]:
    try:
        import importlib as _il
        _mod = _il.import_module(_module)
        FastMCP = getattr(_mod, _attr, None)
        if FastMCP is not None:
            break
    except ImportError:
        pass

if FastMCP is None:
    print("[AURUS MCP] Error: Cannot locate FastMCP in the installed 'mcp' package.", file=sys.stderr)
    print("[AURUS MCP] Tried: mcp.server.fastmcp, mcp.fastmcp, mcp", file=sys.stderr)
    print("[AURUS MCP] Install a compatible version: pip install 'mcp>=1.0.0,<2.0.0'", file=sys.stderr)
    sys.exit(1)


from src.hardware.motors import MecanumDriver
from src.hardware.sensors import ProximitySensor
from src.memory.repository import MemoryRepository
from src.core.models import RobotMode
from src.services.motion_arbiter import MotionArbiter
from src.services.sensor_sampler import SensorSampler

# Optional imports — degrade gracefully when dependencies are missing
try:
    from src.services.tts_service import TTSService
    _HAS_TTS = True
except ImportError:
    _HAS_TTS = False

# ---------------------------------------------------------------------------
# Hardware singletons — initialized once when the server starts
# ---------------------------------------------------------------------------
_driver: MecanumDriver | None = None
_sensor: ProximitySensor | None = None
_repo: MemoryRepository | None = None
_tts: TTSService | None = None
_sampler: SensorSampler | None = None
_arbiter: MotionArbiter | None = None
_motion_lock = threading.Lock()


def _init_hardware() -> None:
    """Lazily initialize hardware singletons on first tool call."""
    global _driver, _sensor, _repo, _tts, _sampler, _arbiter

    if _driver is not None:
        return  # Already initialized

    try:
        _driver = MecanumDriver()
        _sensor = ProximitySensor(_driver)
        _repo = MemoryRepository(PROJECT_ROOT / "aurus_memory.db")
        _sampler = SensorSampler(_sensor)
        _sampler.sample_once()
        _sampler.start()
        _arbiter = MotionArbiter(_driver, _sampler)
        _arbiter.set_dashboard_connected(True)
        _arbiter.start()

        if _HAS_TTS:
            _tts = TTSService(os.getenv("PIPER_MODEL_PATH"))
            _tts.start()
    except Exception:
        _shutdown_hardware()
        _driver = None
        _sensor = None
        _repo = None
        _tts = None
        _sampler = None
        _arbiter = None
        raise


def _shutdown_hardware() -> None:
    actions = []
    if _arbiter is not None:
        actions.extend((
            lambda: _arbiter.emergency_stop("MCP server shutdown"),
            _arbiter.stop,
        ))
    if _sampler is not None:
        actions.append(_sampler.stop)
    if _tts is not None:
        actions.append(_tts.stop)
    if _driver is not None:
        actions.append(_driver.cleanup)
    for action in actions:
        try:
            action()
        except Exception:
            pass


atexit.register(_shutdown_hardware)


def _execute_motion_sequence(
    steps: list[tuple[float, float, float, float]],
) -> tuple[bool, str]:
    """Execute standalone MCP motion through the shared safety arbiter."""
    _init_hardware()
    if not _motion_lock.acquire(blocking=False):
        return False, "another motion command is already running"
    try:
        if not _arbiter.set_mode(RobotMode.PERFORMING):
            return False, "emergency stop is latched"
        for vx, vy, omega, duration in steps:
            if not _arbiter.command(
                "standalone-mcp", vx, vy, omega, ttl=max(0.1, duration + 0.1), priority=60
            ):
                return False, "motion command was rejected"
            deadline = time.monotonic() + duration
            while time.monotonic() < deadline:
                if _arbiter.estopped:
                    return False, "emergency stop is latched"
                if _arbiter.mode != RobotMode.PERFORMING:
                    return False, "motion was interrupted"
                time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
                decision = _arbiter.get_decision()
                if decision.requested.source == "standalone-mcp" and not decision.allowed:
                    return False, decision.reason
        return True, "completed"
    finally:
        _arbiter.halt("standalone-mcp-stop")
        if not _arbiter.estopped and _arbiter.mode == RobotMode.PERFORMING:
            _arbiter.set_mode(RobotMode.IDLE)
        _motion_lock.release()


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------
mcp = FastMCP(
    "AURUS Robot",
    instructions=(
        "You are controlling AURUS, a 4-wheel mecanum rover with 5 ultrasonic "
        "sensors, a camera, a speaker, and persistent memory.  Use these tools "
        "to move the rover, read sensors, capture images, speak, and store "
        "memories.  Always call stop_rover() when motion is complete.  Always "
        "read sensors before moving to ensure the path is clear."
    ),
)


# ── Movement Tools ─────────────────────────────────────────────────────────


@mcp.tool()
def move_rover(
    direction: str,
    speed: float = 1.0,
    duration: float = 1.0,
) -> str:
    """Move AURUS in a direction for a given duration, then stop.

    Args:
        direction: One of 'forward', 'backward', 'left', 'right',
                   'forward_left', 'forward_right', 'backward_left',
                   'backward_right'.
        speed: Speed from 0.0 to 1.0 (default 1.0 — maximum torque for mecanum wheels).
        duration: Seconds to drive (default 1.0).

    Returns:
        JSON status with simulated position if in simulation mode.
    """
    _init_hardware()
    speed = max(0.0, min(1.0, speed))
    duration = max(0.1, min(10.0, duration))

    direction_map = {
        "forward":        ( speed,  0.0,    0.0),
        "backward":       (-speed,  0.0,    0.0),
        "left":           ( 0.0,    speed,  0.0),
        "right":          ( 0.0,   -speed,  0.0),
        "forward_left":   ( speed,  speed,  0.0),
        "forward_right":  ( speed, -speed,  0.0),
        "backward_left":  (-speed,  speed,  0.0),
        "backward_right": (-speed, -speed,  0.0),
    }

    velocities = direction_map.get(direction.lower().replace(" ", "_"))
    if velocities is None:
        return json.dumps({
            "status": "error",
            "message": f"Unknown direction '{direction}'. Use one of: {', '.join(direction_map.keys())}",
        })

    vx, vy, omega = velocities
    completed, reason = _execute_motion_sequence([(vx, vy, omega, duration)])

    state = _driver.get_simulation_state()
    res = {
        "status": "ok" if completed else "blocked",
        "direction": direction,
        "speed": speed,
        "duration_s": duration,
        "simulation_mode": _driver.is_simulation,
        "message": (f"Moved {direction} for {duration}s at {int(speed*100)}% power." if completed else f"Movement stopped: {reason}.") + (" (IN SIMULATION MODE - NO PHYSICAL MOTOR MOVEMENT)" if _driver.is_simulation else ""),
        "position": {
            "x_cm": round(state["x"], 1),
            "y_cm": round(state["y"], 1),
            "heading_deg": round(state["theta"] * 57.2958, 1),
        },
    }
    if _driver.is_simulation:
        res["warning"] = "Running in PC/Simulation mode. If running on Pi, check RPi.GPIO installation and pin permissions."
    return json.dumps(res)


@mcp.tool()
def spin_rover(
    direction: str = "left",
    speed: float = 1.0,
    duration: float = 1.0,
) -> str:
    """Spin AURUS in place (rotate without translating).

    Args:
        direction: 'left' or 'right'.
        speed: Rotation speed from 0.0 to 1.0 (default 1.0 — maximum torque for mecanum wheels).
        duration: Seconds to spin.
    """
    _init_hardware()
    if direction.lower() not in ("left", "right"):
        return json.dumps({"status": "error", "message": f"Unknown spin direction '{direction}'."})
    speed = max(0.0, min(1.0, speed))
    duration = max(0.1, min(10.0, duration))
    omega = speed if direction.lower() == "left" else -speed

    completed, reason = _execute_motion_sequence([(0.0, 0.0, omega, duration)])

    state = _driver.get_simulation_state()
    res = {
        "status": "ok" if completed else "blocked",
        "spin_direction": direction,
        "duration_s": duration,
        "simulation_mode": _driver.is_simulation,
        "message": (f"Spun {direction} for {duration}s at {int(speed*100)}% power." if completed else f"Spin stopped: {reason}.") + (" (IN SIMULATION MODE - NO PHYSICAL MOTOR MOVEMENT)" if _driver.is_simulation else ""),
        "heading_deg": round(state["theta"] * 57.2958, 1),
    }
    if _driver.is_simulation:
        res["warning"] = "Running in PC/Simulation mode. If running on Pi, check RPi.GPIO installation and pin permissions."
    return json.dumps(res)


@mcp.tool()
def stop_rover() -> str:
    """Immediately stop all motors.  Call this after any movement is complete
    or if you need an emergency stop."""
    _init_hardware()
    _arbiter.halt("standalone-mcp-stop")
    if not _arbiter.estopped:
        _arbiter.set_mode(RobotMode.IDLE)
    return json.dumps({"status": "ok", "message": "All motors stopped."})


@mcp.tool()
def perform_animation(name: str) -> str:
    """Play a predefined motion animation on AURUS.

    Args:
        name: One of 'wiggle' (happy), 'shiver' (scared), 'spin' (curious).
    """
    _init_hardware()
    animations: dict[str, list[tuple[float, float, float, float]]] = {
        "wiggle": [(0.0, direction * 0.6, 0.0, 0.15) for _ in range(5) for direction in (1, -1)],
        "shiver": [(direction * 0.4, 0.0, 0.0, 0.05) for _ in range(12) for direction in (1, -1)],
        "spin": [(0.0, 0.0, 0.6, 1.5)],
    }
    sequence = animations.get(name.lower())
    if sequence is None:
        return json.dumps({
            "status": "error",
            "message": f"Unknown animation '{name}'. Use one of: {', '.join(animations.keys())}",
        })
    completed, reason = _execute_motion_sequence(sequence)
    return json.dumps({
        "status": "ok" if completed else "blocked",
        "animation": name,
        "message": f"Played '{name}' animation." if completed else f"Animation stopped: {reason}.",
    })


# ── Sensor Tools ───────────────────────────────────────────────────────────


@mcp.tool()
def read_sensors() -> str:
    """Read all 5 ultrasonic distance sensors.

    Returns a JSON object with distances in centimeters:
    - fl: Front-Left (angled +45°)
    - f:  Front-Center (straight ahead)
    - fr: Front-Right (angled -45°)
    - rl: Rear-Left (angled +135°)
    - rr: Rear-Right (angled -135°)

    Values near 400 cm indicate nothing detected (max range / timeout).
    Values below 15 cm indicate an obstacle is dangerously close.
    """
    _init_hardware()
    snapshot = _sampler.get_snapshot()
    readings = {
        "fl": snapshot.fl,
        "f": snapshot.f,
        "fr": snapshot.fr,
        "rl": snapshot.rl,
        "rr": snapshot.rr,
    }
    front_min = min(readings["fl"], readings["f"], readings["fr"])
    rear_min = min(readings["rl"], readings["rr"])
    return json.dumps({
        "status": "ok",
        "simulation_mode": _sensor.is_simulation,
        "healthy": snapshot.healthy and not snapshot.is_stale(),
        "error": snapshot.error,
        "distances_cm": {k: round(v, 1) for k, v in readings.items()},
        "front_min_cm": round(front_min, 1),
        "rear_min_cm": round(rear_min, 1),
        "front_clear": front_min > 40.0,
        "rear_clear": rear_min > 40.0,
    })


# ── Camera Tool ────────────────────────────────────────────────────────────


@mcp.tool()
def capture_image() -> str:
    """Capture a photo from the camera.

    Supports:
    - Pi Camera Module via rpicam-jpeg or libcamera-still
    - USB Webcams via OpenCV (cv2)
    - Synthetic test frame fallback if no camera hardware is available

    Returns:
        JSON with status, file path, and base64-encoded image data.
    """
    _init_hardware()
    import subprocess
    import tempfile
    import shutil

    capture_path = Path(tempfile.gettempdir()) / "aurus_mcp_capture.jpg"

    # Strategy 1: Picamera2 Python library (Native Pi Camera IMX219 / Module 3)
    try:
        from picamera2 import Picamera2
        cam = Picamera2()
        cfg = cam.create_still_configuration(main={"size": (640, 480)})
        cam.configure(cfg)
        cam.start()
        time.sleep(0.3)
        cam.capture_file(str(capture_path))
        cam.stop()
        cam.close()
        image_bytes = capture_path.read_bytes()
        b64 = base64.b64encode(image_bytes).decode("ascii")
        return json.dumps({
            "status": "ok",
            "backend": "picamera2_python",
            "simulation_mode": False,
            "file_path": str(capture_path),
            "image_size_bytes": len(image_bytes),
            "image_base64": b64,
        })
    except Exception:
        pass  # Fall through to subprocess rpicam-jpeg / OpenCV / synthetic

    # Strategy 2: Try rpicam-jpeg command line tool
    cmd = None
    if shutil.which("rpicam-jpeg"):
        cmd = ["rpicam-jpeg", "-o", str(capture_path), "-t", "500", "--width", "640", "--height", "480", "-n"]
    elif shutil.which("rpicam-still"):
        cmd = ["rpicam-still", "-o", str(capture_path), "-t", "500", "--width", "640", "--height", "480", "-n"]
    elif shutil.which("libcamera-jpeg"):
        cmd = ["libcamera-jpeg", "-o", str(capture_path), "-t", "500", "--width", "640", "--height", "480", "-n"]
    elif shutil.which("libcamera-still"):
        cmd = ["libcamera-still", "-o", str(capture_path), "-t", "500", "--width", "640", "--height", "480", "-n"]

    if cmd:
        try:
            subprocess.run(cmd, check=True, timeout=10, capture_output=True)
            image_bytes = capture_path.read_bytes()
            b64 = base64.b64encode(image_bytes).decode("ascii")
            return json.dumps({
                "status": "ok",
                "backend": cmd[0],
                "simulation_mode": False,
                "file_path": str(capture_path),
                "image_size_bytes": len(image_bytes),
                "image_base64": b64,
            })
        except Exception:
            pass  # Fall through to OpenCV / synthetic fallback

    # Strategy 2: Try OpenCV (USB webcam on Pi or PC)
    try:
        import cv2
        cap = cv2.VideoCapture(0)
        if cap.isOpened():
            ret, frame = cap.read()
            cap.release()
            if ret and frame is not None:
                cv2.imwrite(str(capture_path), frame)
                image_bytes = capture_path.read_bytes()
                b64 = base64.b64encode(image_bytes).decode("ascii")
                return json.dumps({
                    "status": "ok",
                    "backend": "opencv_webcam",
                    "simulation_mode": False,
                    "file_path": str(capture_path),
                    "image_size_bytes": len(image_bytes),
                    "image_base64": b64,
                })
    except Exception:
        pass

    # Strategy 3: Synthetic Test Image Fallback (when no camera hardware is attached)
    try:
        from PIL import Image, ImageDraw
        img = Image.new("RGB", (640, 480), color=(30, 30, 40))
        draw = ImageDraw.Draw(img)
        draw.rectangle([20, 20, 620, 460], outline=(0, 255, 200), width=3)
        draw.text((220, 230), "AURUS CAMERA SIMULATION MODE", fill=(255, 255, 255))
        draw.text((250, 260), time.strftime("%Y-%m-%d %H:%M:%S"), fill=(0, 255, 200))
        img.save(str(capture_path), "JPEG")
        image_bytes = capture_path.read_bytes()
        b64 = base64.b64encode(image_bytes).decode("ascii")
        return json.dumps({
            "status": "ok",
            "backend": "synthetic_simulation",
            "simulation_mode": True,
            "message": "No camera hardware detected — generated simulated test frame.",
            "file_path": str(capture_path),
            "image_size_bytes": len(image_bytes),
            "image_base64": b64,
        })
    except Exception as exc:
        return json.dumps({
            "status": "error",
            "message": f"Camera failed on all backends: {exc}",
        })


# ── Speech Tool ────────────────────────────────────────────────────────────


@mcp.tool()
def speak_text(text: str) -> str:
    """Make AURUS speak a sentence aloud using text-to-speech.

    On Raspberry Pi: uses Piper or eSpeak to play audio through the speaker.
    On PC: may use winsound or degrade to visual-only.

    Args:
        text: The sentence to speak (max 400 characters).
    """
    _init_hardware()
    if _tts is None:
        return json.dumps({"status": "error", "message": "TTS service not available."})

    ok = _tts.speak(text)
    return json.dumps({
        "status": "ok" if ok else "error",
        "backend": _tts.backend,
        "text": text[:400],
        "message": "Speech queued." if ok else (_tts.error or "TTS queue full."),
    })


# ── Memory Tools ───────────────────────────────────────────────────────────


@mcp.tool()
def remember_fact(fact: str, user_id: int = 1) -> str:
    """Store a fact in AURUS persistent memory (SQLite).

    Args:
        fact: The text to remember (e.g. 'The user likes coffee').
        user_id: ID of the person this fact is about (default 1).
    """
    _init_hardware()
    if _repo.get_user(user_id) is None:
        return json.dumps({
            "status": "error",
            "message": f"Unknown user_id {user_id}; enroll or recognize a person first.",
        })
    _repo.remember(user_id, fact)
    return json.dumps({"status": "ok", "message": f"Remembered: {fact}"})


@mcp.tool()
def recall_memories(user_id: int = 1) -> str:
    """Retrieve all stored memories/facts about a person.

    Args:
        user_id: ID of the person to recall facts about (default 1).
    """
    _init_hardware()
    if _repo.get_user(user_id) is None:
        return json.dumps({
            "status": "error",
            "message": f"Unknown user_id {user_id}; enroll or recognize a person first.",
        })
    facts = _repo.memories_for(user_id)
    return json.dumps({
        "status": "ok",
        "user_id": user_id,
        "count": len(facts),
        "memories": facts[:20],  # Cap at 20 to avoid huge responses
    })


# ── Status Tool ────────────────────────────────────────────────────────────


@mcp.tool()
def get_rover_status() -> str:
    """Get the current status of AURUS including simulation position,
    motor state, and sensor health."""
    _init_hardware()
    state = _driver.get_simulation_state()
    return json.dumps({
        "status": "ok",
        "simulation_mode": _driver.is_simulation,
        "position": {
            "x_cm": round(state["x"], 1),
            "y_cm": round(state["y"], 1),
            "heading_deg": round(state["theta"] * 57.2958, 1),
        },
        "velocity": {
            "vx": round(state["vx"], 2),
            "vy": round(state["vy"], 2),
            "omega": round(state["omega"], 2),
        },
        "tts_available": _tts is not None and _tts.healthy if _tts else False,
        "tts_backend": _tts.backend if _tts else "none",
    })


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print(f"[AURUS MCP] Starting server from {PROJECT_ROOT}")
    print(f"[AURUS MCP] Hardware will initialize on first tool call.")
    mcp.run()
