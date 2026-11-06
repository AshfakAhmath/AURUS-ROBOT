"""Thin Flask-SocketIO adapter for the explicit AURUS runtime."""

from __future__ import annotations

import os
import threading
import time
import hmac

from flask import Flask, Response, jsonify, render_template, request
from flask_socketio import SocketIO, emit

from src.services.gesture_service import GESTURES


def create_app(runtime):
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.getenv("AURUS_SESSION_SECRET", "aurus-local-evaluation")
    socketio = SocketIO(
        app,
        async_mode="threading",
        cors_allowed_origins=None,
        ping_interval=5,
        ping_timeout=10,
    )
    clients: set[str] = set()
    clients_lock = threading.Lock()
    telemetry_stop = threading.Event()
    sensor_operators: set[str] = set()
    sensor_token = os.getenv("AURUS_SENSOR_OPERATOR_TOKEN", "")
    browser_speakers: set[str] = set()
    active_text_requests = 0
    update_demo_dashboard = getattr(runtime, "set_demo_dashboard_client", None)

    def event_sink(event, data):
        # Demo browser audio belongs to the operator who started the run.
        recipient = data.get("_recipient")
        socketio.emit(event, {key: value for key, value in data.items() if key != "_recipient"}, to=recipient)
    runtime.set_event_sink(event_sink)

    def payload(data):
        return data if isinstance(data, dict) else {}

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/face")
    def face_display():
        return render_template("face.html")

    @app.get("/face/state")
    def face_state():
        with clients_lock:
            operator_busy = active_text_requests > 0
        expression = runtime.face_expression(operator_busy)
        camera = runtime.camera_control()
        camera_generation = camera["generation"] if camera["enabled"] and camera["state"] == "on" else None
        response = jsonify(expression=expression,
                           transcript=runtime.tts.speaking_text if expression == "speaking" else "",
                           camera_generation=camera_generation)
        response.cache_control.no_store = True
        return response

    @app.get("/health")
    def health():
        return jsonify(runtime.health())

    @app.post("/face/gesture")
    def face_gesture():
        data = payload(request.get_json(silent=True))
        generation = data.get("generation")
        if type(generation) is not int:
            return jsonify(message="Wait for the live camera before starting a scan."), 400
        automatic = data.get("automatic", False)
        after = data.get("after")
        if type(automatic) is not bool or (
            after is not None and not (automatic and isinstance(after, str) and after in GESTURES)
        ):
            return jsonify(message="Invalid Expo gesture scan options."), 400
        result, status = runtime.gestures.scan(generation, automatic=automatic, after=after)
        response = jsonify(result)
        response.cache_control.no_store = True
        return response, status

    @app.get("/video_feed")
    def video_feed():
        def frames():
            while True:
                frame = runtime.vision.get_jpeg()
                if frame:
                    yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
                time.sleep(0.1)

        return Response(frames(), mimetype="multipart/x-mixed-replace; boundary=frame")

    @socketio.on("connect")
    def on_connect():
        with clients_lock:
            clients.add(request.sid)
            if callable(update_demo_dashboard):
                update_demo_dashboard(request.sid, True)
            runtime.dashboard_connected(True)
        emit("system_health", runtime.health())
        emit("telemetry", runtime.telemetry())
        if callable(getattr(runtime, "camera_control", None)):
            emit("camera_configuration", runtime.camera_control())

    @socketio.on("disconnect")
    def on_disconnect():
        with clients_lock:
            clients.discard(request.sid)
            if callable(update_demo_dashboard):
                update_demo_dashboard(request.sid, False)
            sensor_operators.discard(request.sid)
            browser_speakers.discard(request.sid)
            speech = getattr(runtime, "speech", None)
            if speech is not None and not browser_speakers:
                speech.browser_playback_active.clear()
            runtime.dashboard_connected(bool(clients))
        clear_caption = getattr(getattr(runtime, "tts", None), "clear_browser_audio", None)
        if callable(clear_caption):
            clear_caption(request.sid)
        if not callable(update_demo_dashboard):
            demo = getattr(runtime, "demo", None)
            if demo is not None:
                demo.owner_disconnected(request.sid)

    @socketio.on("demo_audio_finished")
    def demo_audio_finished(data):
        data = payload(data)
        runtime.tts.acknowledge_demo(data.get("playback_id"), request.sid, data.get("success") is True)

    @socketio.on("browser_audio_state")
    def browser_audio_state(data):
        speech = getattr(runtime, "speech", None)
        if speech is None:
            return
        data = payload(data)
        tracker = getattr(getattr(runtime, "tts", None), "track_browser_audio", None)
        if callable(tracker) and isinstance(data.get("audio_id"), str):
            tracker(request.sid, data["audio_id"], data.get("playing") is True and data.get("finished") is not True)
        if data.get("playing") is False:
            clear_caption = getattr(getattr(runtime, "tts", None), "clear_browser_audio", None)
            if callable(clear_caption):
                clear_caption(request.sid)
        with clients_lock:
            if payload(data).get("playing") is True:
                browser_speakers.add(request.sid)
            else:
                browser_speakers.discard(request.sid)
            if browser_speakers:
                speech.browser_playback_active.set()
            else:
                speech.browser_playback_active.clear()

    @socketio.on("authorize_sensor_control")
    def authorize_sensor_control(data):
        supplied = payload(data).get("token", "")
        if sensor_token and isinstance(supplied, str) and hmac.compare_digest(supplied.encode(), sensor_token.encode()):
            with clients_lock:
                sensor_operators.add(request.sid)
            emit("sensor_control_authorized", {"authorized": True})
        else:
            with clients_lock:
                sensor_operators.discard(request.sid)
            emit("sensor_control_authorized", {"authorized": False})
            emit("command_error", {"message": "Sensor control authorization rejected. Configure AURUS_SENSOR_OPERATOR_TOKEN on the Pi and enter its value."})

    @socketio.on("set_sensor_enabled")
    def set_sensor_enabled(data):
        with clients_lock:
            authorized = request.sid in sensor_operators
        if not authorized:
            emit("command_error", {"message": "Unauthorized sensor configuration change."})
            return
        data = payload(data)
        try:
            if data.get("restore_defaults") is True:
                runtime.configure_sensor(restore=True)
            else:
                runtime.configure_sensor(data.get("sensor"), data.get("enabled"))
        except (TypeError, ValueError) as exc:
            emit("command_error", {"message": str(exc)})

    @socketio.on("manual_drive")
    def manual_drive(data):
        try:
            data = payload(data)
            accepted = runtime.manual_drive(
                float(data.get("vx", 0.0)),
                float(data.get("vy", 0.0)),
                float(data.get("omega", 0.0)),
                int(data.get("sequence", 0)),
            )
            if not accepted:
                emit("command_error", {"message": "Manual command rejected by mode, order, or safety state."})
        except (TypeError, ValueError) as exc:
            emit("command_error", {"message": f"Invalid drive command: {exc}"})

    @socketio.on("stop")
    def emergency_stop(_data=None):
        runtime.emergency_stop()
        socketio.emit("system_health", runtime.health())

    @socketio.on("clear_estop")
    def clear_estop(_data=None):
        runtime.clear_estop()
        socketio.emit("system_health", runtime.health())

    @socketio.on("set_mode")
    def set_mode(data):
        mode = str(payload(data).get("mode", "idle"))
        if not runtime.set_mode(mode):
            camera_off = (mode.lower() in ("explore", "exploring")
                          and callable(getattr(runtime, "camera_control", None))
                          and not runtime.camera_control()["enabled"])
            emit("command_error", {"message": "Turn the camera on before starting Explore." if camera_off else f"Mode '{mode}' rejected. Clear E-STOP first if latched."})

    @socketio.on("set_audio_output")
    def set_audio_output(data):
        mode = str(payload(data).get("mode", "auto"))
        if not runtime.set_audio_output(mode):
            message = "Stop the FIT Expo demo before changing speaker output." if getattr(runtime, "demo_active", False) else f"Invalid audio output mode '{mode}'. Choose 'pi', 'browser', or 'auto'."
            emit("command_error", {"message": message})
        else:
            socketio.emit("system_health", runtime.health())

    @socketio.on("set_camera_enabled")
    def set_camera_enabled(data):
        enabled = payload(data).get("enabled")
        if type(enabled) is not bool:
            emit("command_error", {"message": "Camera enabled must be true or false."})
            return
        try:
            state = runtime.set_camera_enabled(enabled)
        except ValueError as exc:
            emit("command_error", {"message": str(exc)})
            return
        socketio.emit("camera_configuration", state)
        socketio.emit("system_health", runtime.health())

    @socketio.on("start_listening")
    def start_listening(_data=None):
        if runtime.start_listening():
            emit("conversation", {
                "source": "system",
                "transcript": "",
                "response": f"Listening for {int(runtime.speech.record_seconds)} seconds…",
                "provider": "local",
                "fallback_reason": ""
            })

    @socketio.on("browser_wake_detected")
    def browser_wake_detected(_data=None):
        if not runtime.browser_wake_detected(request.sid):
            emit("command_error", {"message": "AURUS cannot listen right now. Finish the current activity or check E-STOP."})

    @socketio.on("browser_listen_timeout")
    def browser_listen_timeout(_data=None):
        runtime.browser_listening_finished(request.sid)

    def run_text_request(handler, text, owner_sid):
        nonlocal active_text_requests
        with clients_lock:
            if owner_sid not in clients:
                return
            active_text_requests += 1
        try:
            handler(text, owner_sid=owner_sid)
        finally:
            with clients_lock:
                active_text_requests -= 1

    @socketio.on("send_text")
    def send_text(data):
        data = payload(data)
        text = str(data.get("text", "")).strip()
        source = str(data.get("source", "text"))
        if source not in ("text", "browser-mic"):
            source = "text"
        if text:
            handler = runtime.handle_browser_speech if source == "browser-mic" else runtime.handle_text
            socketio.start_background_task(run_text_request, handler, text, request.sid)

    @socketio.on("detect_objects")
    def detect_objects(_data=None):
        return runtime.detect_objects(speak_result=True)

    @socketio.on("run_mcp_agent")
    def run_mcp_agent(data):
        command = str(payload(data).get("text", "")).strip()
        if command:
            socketio.start_background_task(runtime.mcp_agent.execute_command, command)

    @socketio.on("enroll_person")
    def enroll_person(data):
        name = str(payload(data).get("name", "")).strip()
        try:
            status = runtime.start_enrollment(name)
            socketio.emit("enrollment_progress", status.as_dict())
        except ValueError as exc:
            emit("command_error", {"message": str(exc)})

    @socketio.on("remember_fact")
    def remember_fact(data):
        if getattr(runtime, "demo_active", False):
            emit("command_error", {"message": "Stop the FIT Expo demo before storing a fact."})
            return
        fact = str(payload(data).get("text", "")).strip()
        try:
            ok, message = runtime.remember_fact(fact)
            socketio.emit("conversation", {"source": "memory", "transcript": fact, "response": message, "provider": "local", "fallback_reason": "" if ok else "identity required"})
        except ValueError as exc:
            emit("command_error", {"message": str(exc)})

    @socketio.on("perform_showcase")
    def perform_showcase(_data=None):
        if not runtime.start_exhibition_demo(request.sid):
            emit("command_error", {"message": "FIT Expo demo unavailable. Stop other activity, check E-STOP, and check that Piper or eSpeak is ready."})

    @socketio.on("stop_showcase")
    def stop_showcase(_data=None):
        runtime.stop_exhibition_demo()

    def telemetry_loop():
        last_enrollment = None
        while not telemetry_stop.wait(0.2):
            payload = runtime.telemetry()
            socketio.emit("telemetry", payload)
            enrollment = payload["enrollment"]
            marker = (enrollment["active"], enrollment["accepted"], enrollment["complete"], enrollment["error"])
            if marker != last_enrollment:
                socketio.emit("enrollment_progress", enrollment)
                last_enrollment = marker

    def start_background_tasks():
        telemetry_stop.clear()
        socketio.start_background_task(telemetry_loop)

    def stop_background_tasks():
        telemetry_stop.set()

    return app, socketio, start_background_tasks, stop_background_tasks
