import time
import sys
import threading
import math
import os
from pathlib import Path
import tempfile

# Try importing RPi.GPIO; if it fails, run in simulation mode
try:
    import RPi.GPIO as GPIO
    IS_SIMULATION = False
except ImportError:
    IS_SIMULATION = True
    GPIO = None

from src import config

class MecanumDriver:
    # Maximum time (seconds) motors can run without a new drive() call (P1 #7 watchdog)
    MOTOR_WATCHDOG_TIMEOUT = 5.0

    def __init__(self):
        self.is_simulation = IS_SIMULATION
        self.lock = threading.Lock()
        self.speed_multiplier = config.MOTOR_SPEED_MULTIPLIER
        self.output_scale = config.MOTOR_OUTPUT_SCALE
        
        # Virtual simulation coordinates
        self.sim_x = 0.0  # in cm
        self.sim_y = 0.0  # in cm
        self.sim_theta = 0.0  # in radians
        self.sim_vx = 0.0
        self.sim_vy = 0.0
        self.sim_omega = 0.0
        self.last_sim_update = time.time()
        
        # Motors list for loop-based cleanup
        self.pwm_channels = {}

        # P0 #4: Animation cancellation — prevents stacking and allows clean interrupts
        self._animation_cancel = threading.Event()
        self._animation_cancel.set()  # Start in cancelled state (no animation running)
        self._animation_lock = threading.Lock()  # Protects animation replacement
        self._animation_thread = None

        # P1 #7: Motor watchdog — auto-stop motors if no drive() call within timeout
        self._last_drive_time = time.time()
        self._motors_active = False  # Track if motors are currently driving
        self._cleaned = False
        self._hardware_lock_file = None

        if not self.is_simulation:
            self._acquire_hardware_lock()
            try:
                self._setup_hardware()
            except Exception:
                self._release_hardware_lock()
                raise
        else:
            print("--- RUNNING IN MOTOR SIMULATION MODE ---")
        print(
            f"[MotorDriver] vector multiplier={self.speed_multiplier:.2f}; "
            f"uniform output scale={self.output_scale:.2f}"
        )
            
        # Start a simulation coordinate updating thread
        self.running = True
        self.sim_thread = threading.Thread(target=self._sim_loop, daemon=True)
        self.sim_thread.start()

    def _acquire_hardware_lock(self):
        """Prevent two AURUS processes from owning the same GPIO motor pins."""
        try:
            import fcntl
        except ImportError:
            return

        lock_path = Path(tempfile.gettempdir()) / "aurus-motor-hardware.lock"
        handle = lock_path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            raise RuntimeError(
                "AURUS motor hardware is already owned by another process. "
                "Stop the other runtime or MCP server before starting this one."
            ) from exc
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
        handle.flush()
        self._hardware_lock_file = handle

    def _release_hardware_lock(self):
        handle = self._hardware_lock_file
        self._hardware_lock_file = None
        if handle is None:
            return
        try:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except (ImportError, OSError):
            pass
        handle.close()

    def _setup_hardware(self):
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)

        # Pin lists
        all_pins = [
            config.MOTOR_FL_ENA, config.MOTOR_FL_IN1, config.MOTOR_FL_IN2,
            config.MOTOR_FR_ENB, config.MOTOR_FR_IN3, config.MOTOR_FR_IN4,
            config.MOTOR_RL_ENA, config.MOTOR_RL_IN1, config.MOTOR_RL_IN2,
            config.MOTOR_RR_ENB, config.MOTOR_RR_IN3, config.MOTOR_RR_IN4
        ]

        for pin in all_pins:
            try:
                GPIO.setup(pin, GPIO.OUT, initial=GPIO.LOW)
            except Exception as exc:
                try:
                    GPIO.cleanup(pin)
                    GPIO.setup(pin, GPIO.OUT, initial=GPIO.LOW)
                except Exception:
                    print("\n" + "="*70)
                    print("[AURUS HARDWARE ERROR] GPIO PINS ARE BUSY OR LOCKED BY ANOTHER PROCESS!")
                    print("Why this happens: Another Python process (like 'aurus_mcp_server.py' from")
                    print("your MCP test, or an unclosed run.py) is actively holding the motor pins.")
                    print("On Raspberry Pi 4, Linux locks GPIO pins to only ONE running script at a time.")
                    print("\nTO FIX INSTANTLY, RUN THIS COMMAND IN YOUR TERMINAL:")
                    print("  sudo killall -9 python3 python")
                    print("="*70 + "\n")
                    raise exc

        # Initialize PWM at 100Hz
        self.pwm_channels['FL'] = GPIO.PWM(config.MOTOR_FL_ENA, 100)
        self.pwm_channels['FR'] = GPIO.PWM(config.MOTOR_FR_ENB, 100)
        self.pwm_channels['RL'] = GPIO.PWM(config.MOTOR_RL_ENA, 100)
        self.pwm_channels['RR'] = GPIO.PWM(config.MOTOR_RR_ENB, 100)

        for channel in self.pwm_channels.values():
            channel.start(0)

    def _set_motor(self, in1, in2, pwm_ch, speed):
        # speed is from -1.0 to 1.0
        duty = self._pwm_duty(speed)

        if speed > 0.01:
            if not self.is_simulation:
                GPIO.output(in1, GPIO.HIGH)
                GPIO.output(in2, GPIO.LOW)
                self.pwm_channels[pwm_ch].ChangeDutyCycle(duty)
        elif speed < -0.01:
            if not self.is_simulation:
                GPIO.output(in1, GPIO.LOW)
                GPIO.output(in2, GPIO.HIGH)
                self.pwm_channels[pwm_ch].ChangeDutyCycle(duty)
        else:
            if not self.is_simulation:
                GPIO.output(in1, GPIO.LOW)
                GPIO.output(in2, GPIO.LOW)
                self.pwm_channels[pwm_ch].ChangeDutyCycle(0)

    def _pwm_duty(self, speed):
        """Apply one final output scale without changing wheel selection or direction."""
        val = abs(speed)
        if val > 0.01:
            # L298N voltage drop + mecanum static friction causes motor stalls below ~85% PWM duty cycle.
            # Remap active speed commands into the effective working motor range (85 to 100 PWM duty cycle)
            duty = int(85 + (val * 15))
            duty = max(85, min(100, duty))
            return max(0, min(100, int(round(duty * self.output_scale))))
        else:
            return 0

    def set_speed_multiplier(self, multiplier):
        with self.lock:
            self.speed_multiplier = max(0.1, min(1.0, multiplier))

    def drive(self, vx, vy, omega):
        """
        Drive the Mecanum wheels.
        vx: forward/backward velocity (-1.0 to 1.0)
        vy: strafe left/right velocity (-1.0 to 1.0)
        omega: spin left/right velocity (-1.0 to 1.0)
        """
        with self.lock:
            # Track motor activity for watchdog (P1 #7)
            is_moving = (vx != 0 or vy != 0 or omega != 0)
            self._motors_active = is_moving
            if is_moving:
                self._last_drive_time = time.time()

            # Apply global speed scaling
            vx_scaled = vx * self.speed_multiplier
            vy_scaled = vy * self.speed_multiplier
            omega_scaled = omega * self.speed_multiplier

            # Adjust strafe direction using multiplier and boost by 1.35x to overcome lateral roller friction
            vy_adjusted = vy_scaled * config.MOTOR_STRAFE_DIR * 1.35

            # Kinematics formula
            v_fl = vx_scaled + vy_adjusted + omega_scaled
            v_fr = vx_scaled - vy_adjusted - omega_scaled
            v_rl = vx_scaled - vy_adjusted + omega_scaled
            v_rr = vx_scaled + vy_adjusted - omega_scaled

            # Normalize speeds if any exceeds 1.0
            max_val = max(abs(v_fl), abs(v_fr), abs(v_rl), abs(v_rr))
            if max_val > 1.0:
                v_fl /= max_val
                v_fr /= max_val
                v_rl /= max_val
                v_rr /= max_val

            # Store simulated velocities (keep original unscaled for simulation coordinate update)
            self.sim_vx = vx
            self.sim_vy = vy
            self.sim_omega = omega

            # Apply front/rear motor swaps in software if configured
            v_fl_out = v_fl
            v_fr_out = v_fr
            v_rl_out = v_rl
            v_rr_out = v_rr

            if config.MOTOR_SWAP_FL_RL:
                v_fl_out, v_rl_out = v_rl_out, v_fl_out
            if config.MOTOR_SWAP_FR_RR:
                v_fr_out, v_rr_out = v_rr_out, v_fr_out

            if not self.is_simulation:
                # Front L298N #1 (FL & FR)
                self._set_motor(config.MOTOR_FL_IN1, config.MOTOR_FL_IN2, 'FL', v_fl_out * config.MOTOR_FL_DIR)
                self._set_motor(config.MOTOR_FR_IN3, config.MOTOR_FR_IN4, 'FR', v_fr_out * config.MOTOR_FR_DIR)
                # Rear L298N #2 (RL & RR)
                self._set_motor(config.MOTOR_RL_IN1, config.MOTOR_RL_IN2, 'RL', v_rl_out * config.MOTOR_RL_DIR)
                self._set_motor(config.MOTOR_RR_IN3, config.MOTOR_RR_IN4, 'RR', v_rr_out * config.MOTOR_RR_DIR)
            else:
                # Log simulated action
                pass

    def stop(self):
        """Stop all motors and cancel any running animation."""
        self._cancel_animation()
        animation = self._animation_thread
        if animation and animation.is_alive() and animation is not threading.current_thread():
            animation.join(timeout=0.4)
        self.drive(0, 0, 0)

    def _cancel_animation(self):
        """Signal any running animation to stop immediately (P0 #4)."""
        self._animation_cancel.set()

    def _start_animation(self, target, name):
        """Cancel and join the previous animation before starting its replacement."""
        with self._animation_lock:
            self._animation_cancel.set()
            previous = self._animation_thread
            if previous and previous.is_alive() and previous is not threading.current_thread():
                previous.join(timeout=0.4)
                if previous.is_alive():
                    return
            self._animation_cancel.clear()
            self._animation_thread = threading.Thread(target=target, name=name, daemon=True)
            self._animation_thread.start()

    def wiggle(self, duration=1.5):
        """Happy wiggle animation (rapid side strafes).
        P0 #4: Uses animation lock to prevent stacking and cancel event for clean interrupts."""
        def run_wiggle():
            try:
                end_time = time.time() + duration
                step = 0.15
                while time.time() < end_time:
                    if self._animation_cancel.is_set():
                        break
                    self.drive(0, 0.6, 0)
                    time.sleep(step)
                    if self._animation_cancel.is_set():
                        break
                    self.drive(0, -0.6, 0)
                    time.sleep(step)
            finally:
                self.drive(0, 0, 0)  # Don't call stop() to avoid recursive cancel
                self._animation_cancel.set()
        self._start_animation(run_wiggle, "motor-wiggle")

    def shiver(self, duration=1.2):
        """Scared shiver animation (high-speed microscopic forward/backward wiggles).
        P0 #4: Uses animation lock to prevent stacking and cancel event for clean interrupts."""
        def run_shiver():
            try:
                end_time = time.time() + duration
                step = 0.05
                while time.time() < end_time:
                    if self._animation_cancel.is_set():
                        break
                    self.drive(0.4, 0, 0)
                    time.sleep(step)
                    if self._animation_cancel.is_set():
                        break
                    self.drive(-0.4, 0, 0)
                    time.sleep(step)
            finally:
                self.drive(0, 0, 0)
                self._animation_cancel.set()
        self._start_animation(run_shiver, "motor-shiver")

    def spin(self, duration=1.5, direction=1):
        """Spin in place.
        P0 #4: Uses animation lock to prevent stacking and cancel event for clean interrupts."""
        def run_spin():
            try:
                self.drive(0, 0, 0.6 * direction)
                start = time.time()
                while time.time() - start < duration:
                    if self._animation_cancel.is_set():
                        break
                    time.sleep(0.05)
            finally:
                self.drive(0, 0, 0)
                self._animation_cancel.set()
        self._start_animation(run_spin, "motor-spin")

    def _sim_loop(self):
        """Updates simulated coordinates of the Rover and runs motor watchdog."""
        while self.running:
            now = time.time()
            dt = now - self.last_sim_update
            self.last_sim_update = now
            
            with self.lock:
                # P1 #7: Motor watchdog — auto-stop if drive timeout exceeded
                if self._motors_active and (now - self._last_drive_time) > self.MOTOR_WATCHDOG_TIMEOUT:
                    print("[Watchdog] Motor timeout — auto-stopping motors for safety.")
                    self.sim_vx = 0.0
                    self.sim_vy = 0.0
                    self.sim_omega = 0.0
                    self._motors_active = False
                    if not self.is_simulation:
                        # Zero all motor outputs on hardware
                        for pwm_ch in self.pwm_channels.values():
                            pwm_ch.ChangeDutyCycle(0)

                # Compute displacement in local coordinate frame
                # Assume max speed is 30 cm/s
                dx_local = self.sim_vx * 30.0 * dt
                dy_local = self.sim_vy * 30.0 * dt
                # Rotate local displacement to global coordinate frame
                self.sim_theta += self.sim_omega * 2.0 * dt  # 2 rad/s max rotation
                
                # Normalize theta
                self.sim_theta = (self.sim_theta + math.pi) % (2 * math.pi) - math.pi
                
                cos_t = math.cos(self.sim_theta)
                sin_t = math.sin(self.sim_theta)
                
                # standard rotation matrix
                dx_global = dx_local * cos_t - dy_local * sin_t
                dy_global = dx_local * sin_t + dy_local * cos_t
                
                self.sim_x += dx_global
                self.sim_y += dy_global
                
                # Impose boundaries on the simulation room (-150 to 150 cm)
                self.sim_x = max(-150.0, min(150.0, self.sim_x))
                self.sim_y = max(-150.0, min(150.0, self.sim_y))
                
            time.sleep(0.05)

    def get_simulation_state(self):
        """Returns the simulated physical state of the Rover"""
        with self.lock:
            return {
                "x": self.sim_x,
                "y": self.sim_y,
                "theta": self.sim_theta,
                "vx": self.sim_vx,
                "vy": self.sim_vy,
                "omega": self.sim_omega
            }

    def reset_simulation(self):
        with self.lock:
            self.sim_x = 0.0
            self.sim_y = 0.0
            self.sim_theta = 0.0
            self.sim_vx = 0.0
            self.sim_vy = 0.0
            self.sim_omega = 0.0

    def cleanup(self):
        if self._cleaned:
            return
        self._cleaned = True
        self.running = False
        try:
            try:
                self.stop()
            finally:
                animation = self._animation_thread
                if animation and animation.is_alive() and animation is not threading.current_thread():
                    animation.join(timeout=1.0)
                if self.sim_thread.is_alive():
                    self.sim_thread.join(timeout=1.0)
                if not self.is_simulation and GPIO is not None:
                    # rpi-lgpio's PWM destructor calls stop() again. Release
                    # every PWM object while the GPIO chip handle is still
                    # valid, before the process-wide GPIO cleanup closes it.
                    while self.pwm_channels:
                        _, channel = self.pwm_channels.popitem()
                        try:
                            channel.stop()
                        except Exception:
                            pass
                        del channel
                    GPIO.cleanup()
        finally:
            self._release_hardware_lock()
