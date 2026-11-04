"""Thin Flask-SocketIO adapter for the explicit AURUS runtime."""

from __future__ import annotations

import os
import threading
import time

from flask import Flask, Response, jsonify, render_template, request
from flask_socketio import SocketIO, emit


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

    runtime.set_event_sink(lambda event, payload: socketio.emit(event, payload))

    def payload(data):
        return data if isinstance(data, dict) else {}

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/health")
    def health():
        return jsonify(runtime.health())

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
            runtime.dashboard_connected(True)
        emit("system_health", runtime.health())
        emit("telemetry", runtime.telemetry())

    @socketio.on("disconnect")
    def on_disconnect():
        with clients_lock:
            clients.discard(request.sid)
            runtime.dashboard_connected(bool(clients))

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
            emit("command_error", {"message": f"Mode '{mode}' rejected. Clear E-STOP first if latched."})

    @socketio.on("set_audio_output")
    def set_audio_output(data):
        mode = str(payload(data).get("mode", "auto"))
        if not runtime.set_audio_output(mode):
            emit("command_error", {"message": f"Invalid audio output mode '{mode}'. Choose 'pi', 'browser', or 'auto'."})
        else:
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
        if not runtime.browser_wake_detected():
            emit("command_error", {"message": "Wake acknowledgement is unavailable while E-STOP is latched."})

    @socketio.on("browser_listen_timeout")
    def browser_listen_timeout(_data=None):
        runtime.browser_listening_finished()

    @socketio.on("send_text")
    def send_text(data):
        data = payload(data)
        text = str(data.get("text", "")).strip()
        source = str(data.get("source", "text"))
        if source not in ("text", "browser-mic"):
            source = "text"
        if text:
            handler = runtime.handle_browser_speech if source == "browser-mic" else runtime.handle_text
            socketio.start_background_task(handler, text)

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
        fact = str(payload(data).get("text", "")).strip()
        try:
            ok, message = runtime.remember_fact(fact)
            socketio.emit("conversation", {"source": "memory", "transcript": fact, "response": message, "provider": "local", "fallback_reason": "" if ok else "identity required"})
        except ValueError as exc:
            emit("command_error", {"message": str(exc)})

    @socketio.on("perform_showcase")
    def perform_showcase(_data=None):
        if not runtime.behavior.start_showcase():
            emit("command_error", {"message": "Showcase rejected or already running."})

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
