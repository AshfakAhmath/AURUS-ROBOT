#!/usr/bin/env python3
import unittest
import math
import time
import sys
import os

# Adjust path to root directory
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src import config
from src.hardware.motors import MecanumDriver
from src.hardware.sensors import ProximitySensor

class TestRoverLocomotion(unittest.TestCase):
    def setUp(self):
        self.driver = MecanumDriver()
        # Ensure we run in simulation mode for testing
        self.driver.is_simulation = True
        self.driver.reset_simulation()

    def tearDown(self):
        self.driver.cleanup()

    def test_initial_state(self):
        state = self.driver.get_simulation_state()
        self.assertEqual(state["x"], 0.0)
        self.assertEqual(state["y"], 0.0)
        self.assertEqual(state["theta"], 0.0)

    def test_drive_forward(self):
        # Drive forward
        self.driver.drive(1.0, 0, 0)
        state = self.driver.get_simulation_state()
        self.assertEqual(state["vx"], 1.0)
        self.assertEqual(state["vy"], 0.0)
        self.assertEqual(state["omega"], 0.0)

    def test_drive_strafe(self):
        # Strafe right
        self.driver.drive(0, 1.0, 0)
        state = self.driver.get_simulation_state()
        self.assertEqual(state["vx"], 0.0)
        self.assertEqual(state["vy"], 1.0)
        self.assertEqual(state["omega"], 0.0)

    def test_boundaries(self):
        # Directly manipulate coordinates to check room boundary clamping
        self.driver.sim_x = 200.0
        self.driver.sim_y = -200.0
        
        # Wait for simulation thread step
        import time
        time.sleep(0.1)
        
        state = self.driver.get_simulation_state()
        self.assertTrue(-150.0 <= state["x"] <= 150.0)
        self.assertTrue(-150.0 <= state["y"] <= 150.0)

class TestRoverSensors(unittest.TestCase):
    def setUp(self):
        self.driver = MecanumDriver()
        self.driver.is_simulation = True
        self.driver.reset_simulation()
        self.sensor = ProximitySensor(self.driver)

    def tearDown(self):
        self.driver.cleanup()

    def test_sensor_simulation_raycast(self):
        # At (0,0) facing 0.0 (East)
        # Front sensor should measure distance to the right wall (x = 150.0)
        # Sonar is offset by 10cm from center, so start is at x = 10.0
        # Distance to wall is 150 - 10 = 140.0 cm
        # There's a circle obstacle at (50, 50) and (0, 100) and (-60, -40).
        # Let's see: the front ray is along y = 0, x > 10.
        # None of the circular obstacles cross y = 0 for x > 10:
        # Obstacle 1: (50, 50) r=25: distance from center to y=0 is 50, which is > r. No intersection.
        # Obstacle 3: (0, 100) r=15: center is at x=0, no intersection.
        # So it should hit the east wall at 140cm.
        dist = self.sensor.read_front()
        self.assertAlmostEqual(dist, 140.0, delta=1.5)

    def test_rear_left_sensor_raycast(self):
        # Rear-left sensor faces angle = theta + 3*pi/4 (≈135° from East = NW in world)
        # From origin facing East: rear-left is roughly toward (-1, +1) direction.
        # Should hit a wall before 150cm.
        dist = self.sensor.read_rear_left()
        self.assertGreater(dist, 2.0)
        self.assertLessEqual(dist, 250.0)

    def test_rear_right_sensor_raycast(self):
        # Rear-right sensor faces angle = theta - 3*pi/4 (≈-135° from East = SW in world)
        dist = self.sensor.read_rear_right()
        self.assertGreater(dist, 2.0)
        self.assertLessEqual(dist, 250.0)

    def test_front_left_sensor_raycast(self):
        # Front-left sensor faces angle = theta + pi/4 (≈45° from East = NE in world)
        dist = self.sensor.read_front_left()
        self.assertGreater(dist, 2.0)
        self.assertLessEqual(dist, 250.0)

    def test_front_right_sensor_raycast(self):
        # Front-right sensor faces angle = theta - pi/4 (≈-45° from East = SE in world)
        dist = self.sensor.read_front_right()
        self.assertGreater(dist, 2.0)
        self.assertLessEqual(dist, 250.0)

    def test_read_all_returns_five_keys(self):
        """Verify that read_all() returns a dict with all 5 sensor keys."""
        data = self.sensor.read_all()
        self.assertIn("fl", data)
        self.assertIn("f", data)
        self.assertIn("fr", data)
        self.assertIn("rl", data)
        self.assertIn("rr", data)
        # All should be positive distances
        for key, val in data.items():
            self.assertGreater(val, 0.0, f"Sensor '{key}' should return positive distance")

    def test_front_min_matches_individual(self):
        """Verify read_front_min() returns min of FL, F, FR."""
        fl = self.sensor.read_front_left()
        f = self.sensor.read_front()
        fr = self.sensor.read_front_right()
        front_min = self.sensor.read_front_min()
        # Due to noise, read_front_min() re-reads, so we check it's within a reasonable range
        expected_min = min(fl, f, fr)
        self.assertAlmostEqual(front_min, expected_min, delta=2.0)

    def test_sensor_obstacle_intersection(self):
        # Move rover to (20, 50) facing 0 (East)
        # Front sensor starts at (30, 50) pointing East (dx=1, dy=0)
        # Obstacle center at (50, 50) with radius 25
        # Intersection with circle: x = 50 - 25 = 25.
        # Wait, front sensor is at x=30, which is already INSIDE the circle center=50, r=25 (from x=25 to x=75)
        # Let's place it at (-20, 50) facing East.
        # Front sensor starts at (-10, 50) pointing East.
        # Circle is at (50, 50) with radius 25. Intersection is at x = 50 - 25 = 25.
        # Distance = 25 - (-10) = 35 cm.
        self.driver.sim_x = -20.0
        self.driver.sim_y = 50.0
        self.driver.sim_theta = 0.0
        
        dist = self.sensor.read_front()
        # Should be roughly 35 cm
        self.assertAlmostEqual(dist, 35.0, delta=1.5)

class TestDriveModes(unittest.TestCase):
    """Tests for the drive mode system's safety properties."""

    def setUp(self):
        self.driver = MecanumDriver()
        self.driver.is_simulation = True
        self.driver.reset_simulation()
        self.sensor = ProximitySensor(self.driver)

    def tearDown(self):
        self.driver.cleanup()

    def test_mode_transition_stops_motors(self):
        """Verify that driving velocities are zeroed after a simulated mode switch."""
        # Simulate driving forward
        self.driver.drive(1.0, 0, 0)
        state = self.driver.get_simulation_state()
        self.assertEqual(state["vx"], 1.0)

        # Simulate what set_drive_mode does: stop motors
        self.driver.stop()
        state = self.driver.get_simulation_state()
        self.assertEqual(state["vx"], 0.0)
        self.assertEqual(state["vy"], 0.0)
        self.assertEqual(state["omega"], 0.0)

    def test_config_drive_modes_valid(self):
        """Verify that config defines all three expected modes."""
        self.assertIn("manual", config.DRIVE_MODES)
        self.assertIn("autonomous", config.DRIVE_MODES)
        self.assertIn("voice_command", config.DRIVE_MODES)

    def test_autonomous_obstacle_detection_threshold(self):
        """Place rover where front_min sensor reads < AUTO_OBSTACLE_REACT_CM.
           Verify the reading is below the threshold so the autonomous loop would spin."""
        self.driver.sim_x = -10.0
        self.driver.sim_y = 50.0
        self.driver.sim_theta = 0.0

        dist = self.sensor.read_front_min()
        # Distance should be ~25 cm, which is < AUTO_OBSTACLE_REACT_CM (35)
        self.assertLess(dist, config.AUTO_OBSTACLE_REACT_CM)
        # But > PROXIMITY_ALARM_CM (15), so it would spin instead of emergency stopping
        self.assertGreater(dist, config.PROXIMITY_ALARM_CM)

    def test_autonomous_emergency_threshold(self):
        """Place rover very close to an obstacle so front distance < PROXIMITY_ALARM_CM.
           The autonomous loop should trigger an emergency stop at this distance."""
        # Rover at (20, 50) facing East → sensor at (30, 50)
        # Obstacle at (50, 50) r=25 → surface at x=25, sensor at x=30 is INSIDE.
        # Ray starts inside sphere, t1 < 0 so we get t2 (exit intersection) which is positive.
        # Actually, we need to be right AT the surface. Let's use (14, 50):
        # Sensor at (24, 50), obstacle surface at x=25. Distance = 1 cm.
        self.driver.sim_x = 14.0
        self.driver.sim_y = 50.0
        self.driver.sim_theta = 0.0

        dist = self.sensor.read_front()
        # Should be very small (< 15cm alarm threshold)
        self.assertLess(dist, config.PROXIMITY_ALARM_CM)

    def test_voice_burst_safety_blocked(self):
        """Verify that if the rover is near an obstacle, read_front_min would block."""
        self.driver.sim_x = 14.0
        self.driver.sim_y = 50.0
        self.driver.sim_theta = 0.0

        dist_front_min = self.sensor.read_front_min()
        # Pre-flight check: forward should be blocked
        should_block = dist_front_min < config.PROXIMITY_ALARM_CM
        self.assertTrue(should_block, f"Front min distance {dist_front_min} should be < {config.PROXIMITY_ALARM_CM}")


class TestAURUSBrain(unittest.TestCase):
    """P2 #15: Tests for aurus_brain.py — fallback brain, memory, state formatting."""

    def test_local_fallback_greetings(self):
        """Verify local fallback brain returns structured responses for greetings."""
        from src.ai.aurus_brain import local_fallback_brain
        for keyword in ["hello", "hi", "hey", "greetings"]:
            result = local_fallback_brain(keyword)
            self.assertIn("speech", result)
            self.assertIn("emotion", result)
            self.assertIn("action", result)
            self.assertIn("inner_thought", result)
            self.assertTrue(len(result["speech"]) > 0, f"Empty speech for keyword '{keyword}'")

    def test_local_fallback_keywords(self):
        """Verify fallback brain matches various keyword categories."""
        from src.ai.aurus_brain import local_fallback_brain
        test_cases = {
            "trick": "happy",       # Tricks should be happy
            "dance": "happy",
            "scared": "scared",
            "sad": "sad",
            "spin": "happy",
            "who are you": "happy",
        }
        for keyword, expected_emotion in test_cases.items():
            result = local_fallback_brain(keyword)
            self.assertEqual(result["emotion"], expected_emotion,
                           f"Keyword '{keyword}' expected emotion '{expected_emotion}', got '{result['emotion']}'")

    def test_local_fallback_default(self):
        """Verify unknown inputs get a default response (not an error)."""
        from src.ai.aurus_brain import local_fallback_brain
        result = local_fallback_brain("xyzzy quantum flux capacitor")
        self.assertIn("speech", result)
        self.assertIn("emotion", result)

    def test_conversation_memory_add_and_trim(self):
        """Verify conversation memory stores and trims entries."""
        from src.ai.aurus_brain import ConversationMemory
        mem = ConversationMemory(max_turns=3)
        for i in range(10):
            mem.add_user(f"msg {i}")
            mem.add_model(f"reply {i}")
        # Should be trimmed to max_turns * 2 = 6 entries
        self.assertLessEqual(len(mem.history), 6)

    def test_conversation_memory_get_last_user(self):
        """Verify get_last_user_message returns the most recent user message."""
        from src.ai.aurus_brain import ConversationMemory
        mem = ConversationMemory()
        mem.add_user("first")
        mem.add_model("reply1")
        mem.add_user("second")
        self.assertEqual(mem.get_last_user_message(), "second")

    def test_conversation_memory_empty(self):
        """Verify empty memory returns appropriate defaults."""
        from src.ai.aurus_brain import ConversationMemory
        mem = ConversationMemory()
        self.assertEqual(mem.get_turn_count(), 0)
        self.assertIsNone(mem.get_last_user_message())
        self.assertEqual(mem.get_formatted_context(), "[No prior conversation]")

    def test_format_rover_state(self):
        """Verify state formatter produces expected output."""
        from src.ai.aurus_brain import format_rover_state
        state = {
            "happiness": 0.75, "curiosity": 0.5, "fear": 0.1,
            "expression": "happy", "drive_mode": "manual",
            "_start_time": time.time() - 120, "last_interaction": time.time() - 5
        }
        sensor_data = {"fl": 100, "f": 150, "fr": 80, "rl": 200, "rr": 200}
        result = format_rover_state(state, sensor_data)
        self.assertIn("happiness=0.75", result)
        self.assertIn("Front:", result)
        self.assertIn("manual", result)

    def test_brain_think_fallback(self):
        """Verify AURUSBrain.think() falls back to local brain when no Gemini client."""
        from src.ai.aurus_brain import AURUSBrain
        brain = AURUSBrain(gemini_client=None)
        result = brain.think("hello", {"happiness": 0.5}, {"f": 100})
        self.assertIn("speech", result)
        self.assertIn("emotion", result)
        self.assertIn("action", result)

    def test_brain_idle_thought_timing(self):
        """Verify idle thoughts respect the timing interval."""
        from src.ai.aurus_brain import AURUSBrain
        brain = AURUSBrain(gemini_client=None)
        brain.last_idle_thought_time = time.time()  # Just generated one
        state = {"happiness": 0.5, "curiosity": 0.5, "last_interaction": time.time()}
        sensor_data = {"fl": 100, "f": 150, "fr": 80, "rl": 200, "rr": 200}
        # Should return None because interval hasn't passed
        result = brain.generate_idle_thought(state, sensor_data)
        self.assertIsNone(result)

    def test_brain_obstacle_reaction(self):
        """Verify obstacle reactions return properly formatted responses."""
        from src.ai.aurus_brain import AURUSBrain
        brain = AURUSBrain(gemini_client=None)
        result = brain.react_to_obstacle("front", 10)
        self.assertIn("speech", result)
        self.assertEqual(result["emotion"], "scared")
        self.assertIn("10", result["speech"])  # Distance should be in speech

    def test_brain_treat_reaction(self):
        """Verify treat reactions return happy responses."""
        from src.ai.aurus_brain import AURUSBrain
        brain = AURUSBrain(gemini_client=None)
        result = brain.react_to_treat()
        self.assertIn("speech", result)
        self.assertEqual(result["emotion"], "happy")

    def test_brain_memory_summary(self):
        """Verify memory summary provides correct metadata."""
        from src.ai.aurus_brain import AURUSBrain
        brain = AURUSBrain(gemini_client=None)
        brain.think("hello", {"happiness": 0.5}, {"f": 100})
        summary = brain.get_memory_summary()
        self.assertEqual(summary["turn_count"], 1)
        self.assertEqual(summary["last_user"], "hello")
        self.assertGreaterEqual(summary["uptime_minutes"], 0)


class TestMotorAnimationCancellation(unittest.TestCase):
    """P2 #15: Tests for motor animation cancellation (P0 #4 fix)."""

    def setUp(self):
        self.driver = MecanumDriver()
        self.driver.is_simulation = True
        self.driver.reset_simulation()

    def tearDown(self):
        self.driver.cleanup()

    def test_stop_cancels_animation(self):
        """Verify that stop() sets the animation cancel event."""
        self.driver._animation_cancel.clear()  # Simulate running animation
        self.driver.stop()
        self.assertTrue(self.driver._animation_cancel.is_set())

    def test_animation_cancel_event_initial_state(self):
        """Verify animation cancel event starts in set (cancelled) state."""
        fresh_driver = MecanumDriver()
        fresh_driver.is_simulation = True
        self.assertTrue(fresh_driver._animation_cancel.is_set())
        fresh_driver.cleanup()

    def test_wiggle_then_stop(self):
        """Verify wiggle can be interrupted by stop()."""
        import time as t
        self.driver.wiggle(5.0)  # Long wiggle
        t.sleep(0.1)  # Let it start
        self.driver.stop()  # Should cancel immediately
        t.sleep(0.2)  # Wait for thread to finish
        state = self.driver.get_simulation_state()
        # After stop, velocities should be zero
        self.assertEqual(state["vx"], 0.0)
        self.assertEqual(state["vy"], 0.0)

    def test_replacing_animation_joins_previous_thread(self):
        """Verify a replacement cannot overlap the previous animation."""
        self.driver.wiggle(0.5)
        previous = self.driver._animation_thread
        self.driver.spin(0.1)
        self.assertFalse(previous.is_alive())
        self.assertIsNot(self.driver._animation_thread, previous)
        self.driver.stop()


class TestMotorWatchdog(unittest.TestCase):
    """P2 #15: Tests for motor watchdog timer (P1 #7 fix)."""

    def setUp(self):
        self.driver = MecanumDriver()
        self.driver.is_simulation = True
        self.driver.reset_simulation()

    def tearDown(self):
        self.driver.cleanup()

    def test_watchdog_timeout_constant(self):
        """Verify watchdog timeout is set to a reasonable value."""
        self.assertGreater(MecanumDriver.MOTOR_WATCHDOG_TIMEOUT, 0)
        self.assertLessEqual(MecanumDriver.MOTOR_WATCHDOG_TIMEOUT, 10.0)

    def test_drive_updates_last_drive_time(self):
        """Verify that drive() updates the last drive timestamp."""
        before = time.time()
        self.driver.drive(1.0, 0, 0)
        self.assertGreaterEqual(self.driver._last_drive_time, before)
        self.assertTrue(self.driver._motors_active)

    def test_stop_clears_motors_active(self):
        """Verify that stop() clears the motors_active flag."""
        self.driver.drive(1.0, 0, 0)
        self.assertTrue(self.driver._motors_active)
        self.driver.stop()
        self.assertFalse(self.driver._motors_active)


if __name__ == "__main__":
    unittest.main()
