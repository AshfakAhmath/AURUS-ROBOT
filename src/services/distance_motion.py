"""Time estimates at a measured fixed motor setting; no odometry."""

import math
import os

from src import config


def prepare_movement(direction, speed=1.0, duration=1.0, distance_cm=None):
    speed, duration = float(speed), float(duration)
    if not math.isfinite(speed) or not math.isfinite(duration):
        raise ValueError("Speed and duration must be finite numbers")
    speed = max(0.0, min(1.0, speed))
    if distance_cm is None:
        return speed, max(0.1, min(10.0, duration)), None
    if direction not in ("forward", "backward", "left", "right"):
        raise ValueError("Distance movement supports forward, backward, left and right only")
    distance = float(distance_cm)
    if not math.isfinite(distance) or distance <= 0:
        raise ValueError("distance_cm must be finite and greater than zero")
    try:
        rate = float(os.environ[f"AURUS_DISTANCE_{direction.upper()}_CM_S"])
        motor_speed = float(os.environ["AURUS_DISTANCE_MOTOR_SPEED"])
        output_scale = float(os.environ["AURUS_DISTANCE_OUTPUT_SCALE"])
        vector_multiplier = float(os.environ["AURUS_DISTANCE_VECTOR_MULTIPLIER"])
        if not all(math.isfinite(v) and v > 0 for v in (rate, motor_speed, output_scale, vector_multiplier)):
            raise ValueError()
        if not 0 < motor_speed <= 1:
            raise ValueError()
    except (KeyError, ValueError):
        raise ValueError("Distance movement is not calibrated yet. Please use a timed movement command.") from None
    if not (math.isclose(speed, motor_speed) and math.isclose(output_scale, config.MOTOR_OUTPUT_SCALE)
            and math.isclose(vector_multiplier, config.MOTOR_SPEED_MULTIPLIER)):
        raise ValueError("Distance calibration does not match the requested motor speed/output settings. Use the calibrated speed or recalibrate.")
    seconds = distance / rate
    if not 0.1 <= seconds <= 10.0:
        raise ValueError("Estimated duration is outside the 0.1–10 second safety limit. Request a shorter distance or use timed movement.")
    return speed, seconds, distance


def distance_feedback(distance, duration, completed, feedback):
    if distance is None:
        return {}
    estimate = distance if completed and not feedback.get("speed_limited") else None
    return {
        "requested_distance_cm": distance, "duration_s": duration,
        "estimated_distance_cm": estimate, "actual_distance_cm": None,
        "distance_is_estimated": True,
        "message": (
            f"I completed the estimated {distance:g}-centimeter movement."
            if estimate is not None else
            "Movement was interrupted; actual travelled distance is unknown."
            if not completed else
            "Timed movement finished with safety speed limiting; travelled distance is unknown."
        ),
    }
