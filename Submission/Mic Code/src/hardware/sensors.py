import time
import math
import random
import statistics
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

    def __init__(self, motor_driver=None):
        self.is_simulation = IS_SIMULATION
        self.motor_driver = motor_driver
        
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
        """Read distance from a physical HC-SR04 sensor. Returns distance in cm."""
        # Trigger high for 10us
        GPIO.output(trig_pin, GPIO.HIGH)
        time.sleep(0.00001)
        GPIO.output(trig_pin, GPIO.LOW)

        pulse_start = time.time()
        pulse_end = time.time()

        # Wait for Echo to go high (timeout after 0.02s)
        timeout = time.time() + 0.02
        while GPIO.input(echo_pin) == 0:
            pulse_start = time.time()
            if pulse_start > timeout:
                return 400.0  # Return max range on timeout

        # Wait for Echo to go low (timeout after 0.02s)
        timeout = time.time() + 0.02
        while GPIO.input(echo_pin) == 1:
            pulse_end = time.time()
            if pulse_end > timeout:
                return 400.0

        pulse_duration = pulse_end - pulse_start
        # Speed of sound is 34300 cm/s. Distance is speed * time / 2
        distance = pulse_duration * 17150
        return round(distance, 2)

    def _read_physical_filtered(self, trig_pin, echo_pin, samples=3):
        """P1 #6: Read a physical sensor with median-of-N filtering to reject noise spikes.
        Takes N samples and returns the median, which is robust against single outliers."""
        readings = []
        for _ in range(samples):
            readings.append(self._read_physical_sensor(trig_pin, echo_pin))
            time.sleep(0.005)  # Small inter-sample delay (5ms)
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
