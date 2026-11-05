"""Bounded MCP sequences with durable, structured local cancellation evidence."""

import time

from src.core.models import RobotMode


def execute_sequence(arbiter, steps, source):
    feedback = {
        "obstacle_direction": None, "obstacle_distance_cm": None,
        "movement_cancelled": True, "motors_stopped": False,
        "speed_limited": False,
    }
    if not steps:
        return False, "empty motion sequence", feedback
    if not arbiter.set_mode(RobotMode.PERFORMING):
        return False, "emergency stop is latched", feedback
    previous = arbiter.get_last_blocked() if hasattr(arbiter, "get_last_blocked") else None
    def run_steps():
        for vx, vy, omega, duration in steps:
            if not arbiter.command(source, vx, vy, omega, ttl=0.2, priority=60):
                return False, "motion command was rejected"
            # Preflight also guarantees even a short burst gets a local safety decision.
            arbiter.tick()
            deadline = time.monotonic() + duration
            while True:
                blocked = arbiter.get_last_blocked()
                if blocked is not None and blocked is not previous:
                    feedback.update({key: blocked.as_dict()[key] for key in feedback if key != "speed_limited"})
                    return False, blocked.reason
                if arbiter.estopped or arbiter.mode != RobotMode.PERFORMING:
                    return False, "motion was interrupted or emergency stopped"
                decision = arbiter.get_decision()
                if decision.requested.source == source and decision.allowed:
                    feedback["speed_limited"] |= any(
                        abs(final) < abs(requested) for final, requested in zip(
                            (decision.final_vx, decision.final_vy, decision.final_omega),
                            (vx, vy, omega),
                        )
                    )
                if time.monotonic() >= deadline:
                    break
                # Preserve the runtime deadman: a stalled executor cannot leave
                # a long distance command powered until its full duration ends.
                if not arbiter.command(source, vx, vy, omega, ttl=0.2, priority=60):
                    return False, "motion command was rejected"
                time.sleep(min(0.025, max(0.0, deadline - time.monotonic())))
        return True, "completed"

    completed, reason = False, "motion execution failed"
    try:
        completed, reason = run_steps()
    except Exception as exc:
        reason = f"motion execution failed: {str(exc)[:120]}"
    finally:
        try:
            arbiter.halt(source + "-stop")
            feedback["motors_stopped"] = True
        except Exception as exc:
            feedback["motors_stopped"] = False
            completed = False
            reason += f"; stop output failed: {str(exc)[:120]}"
        if not arbiter.estopped and arbiter.mode == RobotMode.PERFORMING:
            try:
                arbiter.set_mode(RobotMode.IDLE)
            except Exception:
                completed = False
                feedback["motors_stopped"] = False
                reason += "; idle stop output failed"
    feedback["movement_cancelled"] = not completed
    return completed, reason, feedback
