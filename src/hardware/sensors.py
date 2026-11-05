import time
import math
import random
import statistics
import threading
from src import config

# Try importing RPi.GPIO
try:
    import RPi.GPIO as GPIO
    IS_SIMULATION = False
except ImportError:
    IS_SIMULATION = True
    GPIO = None

class ProximitySensor:
    """Manages 5 HC-SR04 ultrasonic sensors: front-left, front, front-right, rear-left, rear-right.
    Falls back to raycasting simulation when RPi.GPIO is unavailable."""

    MIN_SENSOR_CYCLE_S = 0.065  # Datasheet recommends >60 ms per transducer.
    MIN_PING_GAP_S = 0.040  # Stagger different transducers; no rapid burst polling.
    ECHO_IDLE_TIMEOUT_S = 0.005
    ECHO_RISE_TIMEOUT_S = 0.020
    ECHO_FALL_TIMEOUT_S = 0.030  # 4 m needs about 23.3 ms, beyond the old 20 ms.

    def __init__(self, motor_driver=None):
        self.is_simulation = IS_SIMULATION
        self.motor_driver = motor_driver
        self._ping_lock = threading.Lock()
        self._last_ping_at = None
        self._last_sensor_ping = {}

        # Virtual obstacles in the simulated environment: list of dicts: {"type": "circle", "x": cx, "y": cy, "r": radius}
        self.obstacles = [
            {"type": "circle", "x": 50.0, "y": 50.0, "r": 25.0},
            {"type": "circle", "x": -60.0, "y": -40.0, "r": 20.0},
            {"type": "circle", "x": 0.0, "y": 100.0, "r": 15.0}
        ]

        if not self.is_simulation:
            self._setup_hardware()
        else:
            print("--- RUNNING IN SENSOR SIMULATION MODE (5 sensors) ---")

    def _setup_hardware(self):
        GPIO.setmode(GPIO.BCM)

        # All 5 sensor pin pairs: (TRIG, ECHO)
        sensor_pins = [
            (config.SENSOR_FL_TRIG_PIN, config.SENSOR_FL_ECHO_PIN),
            (config.SENSOR_FRONT_TRIG_PIN, config.SENSOR_FRONT_ECHO_PIN),
            (config.SENSOR_FR_TRIG_PIN, config.SENSOR_FR_ECHO_PIN),
            (config.SENSOR_RL_TRIG_PIN, config.SENSOR_RL_ECHO_PIN),
            (config.SENSOR_RR_TRIG_PIN, config.SENSOR_RR_ECHO_PIN),
        ]

        for trig, echo in sensor_pins:
            try:
                GPIO.setup(trig, GPIO.OUT, initial=GPIO.LOW)
                GPIO.setup(echo, GPIO.IN)
            except Exception as exc:
                try:
                    GPIO.cleanup([trig, echo])
                    GPIO.setup(trig, GPIO.OUT, initial=GPIO.LOW)
                    GPIO.setup(echo, GPIO.IN)
                except Exception:
                    print("\n" + "="*70)
                    print("[AURUS HARDWARE ERROR] SENSOR GPIO PINS ARE BUSY OR LOCKED!")
                    print("Another Python process is holding the ultrasonic sensor pins.")
                    print("\nTO FIX INSTANTLY, RUN THIS COMMAND IN YOUR TERMINAL:")
                    print("  sudo killall -9 python3 python")
                    print("="*70 + "\n")
                    raise exc

    def _read_physical_sensor(self, trig_pin, echo_pin):
        """Measure one complete new echo; busy/timeout signals are never clearance."""
        with self._ping_lock:
            now = time.monotonic()
            due = now
            if self._last_ping_at is not None:
                due = max(due, self._last_ping_at + self.MIN_PING_GAP_S)
            previous = self._last_sensor_ping.get((trig_pin, echo_pin))
            if previous is not None:
                due = max(due, previous + self.MIN_SENSOR_CYCLE_S)
            if due > now:
                time.sleep(due - now)

            GPIO.output(trig_pin, GPIO.LOW)
            time.sleep(0.000002)
            deadline = time.monotonic() + self.ECHO_IDLE_TIMEOUT_S
            while GPIO.input(echo_pin) == GPIO.HIGH:
                if time.monotonic() >= deadline:
                    raise TimeoutError("echo high before trigger (sensor busy or stuck)")

            # Only a LOW -> triggered HIGH -> LOW cycle is a new measurement.
            triggered = time.monotonic()
            self._last_ping_at = triggered
            self._last_sensor_ping[(trig_pin, echo_pin)] = triggered
            GPIO.output(trig_pin, GPIO.HIGH)
            try:
                time.sleep(0.00001)
            finally:
                GPIO.output(trig_pin, GPIO.LOW)

            deadline = time.monotonic() + self.ECHO_RISE_TIMEOUT_S
            while GPIO.input(echo_pin) == GPIO.LOW:
                if time.monotonic() >= deadline:
                    raise TimeoutError("missing echo (rise timeout)")
            pulse_start = time.monotonic()
            deadline = pulse_start + self.ECHO_FALL_TIMEOUT_S
            while GPIO.input(echo_pin) == GPIO.HIGH:
                if time.monotonic() >= deadline:
                    raise TimeoutError("echo pulse did not finish (fall timeout)")
            pulse_end = time.monotonic()
            distance = (pulse_end - pulse_start) * 17150
            if not math.isfinite(distance) or not 0 < distance <= 400:
                raise ValueError("echo pulse outside valid 0–400 cm measurement range")
            return round(distance, 2)

    def _read_physical_filtered(self, trig_pin, echo_pin, samples=1):
        """One fresh ping per sampler pass; explicit multi-sample calls stay spaced.

        Three immediate pings made a single transient fail the whole old burst,
        and spacing all 15 pings correctly would exceed the 0.3 s freshness bound.
        The sampler continues monitoring repeated inconsistent readings.
        """
        if samples < 1:
            raise ValueError("At least one sample is required")
        readings = []
        for _ in range(samples):
            readings.append(self._read_physical_sensor(trig_pin, echo_pin))
        return round(statistics.median(readings), 2)

    def _raycast_simulation(self, angle_offset):
        """
        Simulates raycasting from Rover's position in a given direction.
        angle_offset: angular offset from rover heading (radians).
            0        = Front Center
            +π/4     = Front-Left
            -π/4     = Front-Right
            +3π/4    = Rear-Left
            -3π/4    = Rear-Right
        """
        if not self.motor_driver:
            return 150.0  # Default if no motor driver reference

        state = self.motor_driver.get_simulation_state()
        rx, ry, rtheta = state["x"], state["y"], state["theta"]

        # Rover center offset: assume sonar sensors are mounted 10cm from center
        sensor_dist = 10.0
        sensor_angle = rtheta + angle_offset

        px = rx + sensor_dist * math.cos(sensor_angle)
        py = ry + sensor_dist * math.sin(sensor_angle)

        dx = math.cos(sensor_angle)
        dy = math.sin(sensor_angle)

        # Distance to room boundaries ([-150, 150] cm)
        t_wall = float('inf')

        # X walls (vertical at x = -150 and x = 150)
        if dx > 0:
            t_wall = min(t_wall, (150.0 - px) / dx)
        elif dx < 0:
            t_wall = min(t_wall, (-150.0 - px) / dx)

        # Y walls (horizontal at y = -150 and y = 150)
        if dy > 0:
            t_wall = min(t_wall, (150.0 - py) / dy)
        elif dy < 0:
            t_wall = min(t_wall, (-150.0 - py) / dy)

        # Distance to circular obstacles
        t_obs = float('inf')
        for obs in self.obstacles:
            # Circle line intersection
            vx = px - obs["x"]
            vy = py - obs["y"]
            r = obs["r"]

            b = 2.0 * (vx * dx + vy * dy)
            c = vx * vx + vy * vy - r * r

            discriminant = b * b - 4.0 * c
            if discriminant >= 0:
                t1 = (-b - math.sqrt(discriminant)) / 2.0
                t2 = (-b + math.sqrt(discriminant)) / 2.0

                # We need the smallest positive intersection distance
                if t1 > 0:
                    t_obs = min(t_obs, t1)
                elif t2 > 0:
                    t_obs = min(t_obs, t2)

        return min(t_wall, t_obs)

    def _read_simulated(self, angle_offset):
        """Read a simulated sensor with noise."""
        dist = self._raycast_simulation(angle_offset)
        return max(2.0, round(dist + random.uniform(-0.5, 0.5), 1))

    # --- Individual Sensor Read Methods ---

    def read_front_left(self):
        """Read the front-left sensor (angled +45° from heading)."""
        if self.is_simulation:
            return self._read_simulated(config.SENSOR_ANGLE_FL)
        else:
            return self._read_physical_filtered(config.SENSOR_FL_TRIG_PIN, config.SENSOR_FL_ECHO_PIN)

    def read_front(self):
        """Read the front center sensor (straight ahead)."""
        if self.is_simulation:
            return self._read_simulated(config.SENSOR_ANGLE_FRONT)
        else:
            return self._read_physical_filtered(config.SENSOR_FRONT_TRIG_PIN, config.SENSOR_FRONT_ECHO_PIN)

    def read_front_right(self):
        """Read the front-right sensor (angled -45° from heading)."""
        if self.is_simulation:
            return self._read_simulated(config.SENSOR_ANGLE_FR)
        else:
            return self._read_physical_filtered(config.SENSOR_FR_TRIG_PIN, config.SENSOR_FR_ECHO_PIN)

    def read_rear_left(self):
        """Read the rear-left sensor (angled +135° from heading)."""
        if self.is_simulation:
            return self._read_simulated(config.SENSOR_ANGLE_RL)
        else:
            return self._read_physical_filtered(config.SENSOR_RL_TRIG_PIN, config.SENSOR_RL_ECHO_PIN)

    def read_rear_right(self):
        """Read the rear-right sensor (angled -135° from heading)."""
        if self.is_simulation:
            return self._read_simulated(config.SENSOR_ANGLE_RR)
        else:
            return self._read_physical_filtered(config.SENSOR_RR_TRIG_PIN, config.SENSOR_RR_ECHO_PIN)

    # --- Convenience Aggregate Methods ---

    def read_all(self):
        """Read all 5 sensors and return as a dict."""
        return {
            "fl": self.read_front_left(),
            "f": self.read_front(),
            "fr": self.read_front_right(),
            "rl": self.read_rear_left(),
            "rr": self.read_rear_right()
        }

    def read_front_min(self):
        """Return the minimum distance across all 3 front sensors (FL, F, FR).
        Used for obstacle avoidance — triggers on whichever front sensor is closest.
        P2 #14: Uses read_all() to avoid redundant sensor reads."""
        data = self.read_all()
        return min(data["fl"], data["f"], data["fr"])

    def read_rear_min(self):
        """Return the minimum distance across both rear sensors (RL, RR).
        Used for rear obstacle avoidance — triggers on whichever rear sensor is closest.
        P2 #14: Uses read_all() to avoid redundant sensor reads."""
        data = self.read_all()
        return min(data["rl"], data["rr"])

    def get_obstacles(self):
        return self.obstacles
