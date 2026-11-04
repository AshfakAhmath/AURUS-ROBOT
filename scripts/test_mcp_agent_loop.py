#!/usr/bin/env python3
"""Verification script for Groq MCP Agent tool integration."""

import sys
from pathlib import Path

# Ensure project root is in path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.services.mcp_agent_service import MCPAgentService, AURUS_MCP_TOOLS


class MockSnapshot:
    def __init__(self):
        self.fl = 50.0
        self.f = 65.0
        self.fr = 55.0
        self.rl = 120.0
        self.rr = 110.0
        self.front_min = 50.0
        self.rear_min = 110.0
        self.healthy = True
        self.backend = "mock-vision"
        self.identity = type("Identity", (), {"name": "Test Person", "status": "known", "confidence": 0.95})()

    def navigation_front_min(self, center_enabled=True):
        return min(self.fl, self.f, self.fr) if center_enabled else min(self.fl, self.fr)


class MockRuntime:
    def __init__(self):
        self.events = []
        self.sensor_sampler = type("Sampler", (), {"get_snapshot": lambda self, *a: MockSnapshot()})()
        self.vision = type("Vision", (), {"get_snapshot": lambda self, *a: MockSnapshot()})()
        self.driver = type("Driver", (), {"get_simulation_state": lambda self, *a: {"x": 10.0, "y": 5.0, "theta": 0.0}, "is_simulation": True, "wiggle": lambda self, *a, **kw: None, "shiver": lambda self, *a, **kw: None, "spin": lambda self, *a, **kw: None})()
        self.arbiter = type("Arbiter", (), {"mode": type("Mode", (), {"value": "idle"})(), "estopped": False, "front_sensor_enabled": True, "caution_cm": 35.0, "command": lambda self, *args, **kwargs: True, "halt": lambda self, *args, **kwargs: None, "set_mode": lambda self, mode: True})()
        self.tts = type("TTS", (), {"speak": lambda self, txt: True})()
        self.repository = type("Repo", (), {"get_user": lambda self, uid: {"id": uid}, "memories_for": lambda self, uid: ["User likes robotics"], "remember": lambda self, uid, f: None})()
        self.health = lambda *a: {"runtime": True, "motors": True, "mcp_agent": True}

    def emit(self, event, payload):
        self.events.append((event, payload))
        print(f"[Emit -> {event}]: {payload}")


def main():
    print("=" * 60)
    print("Testing AURUS Groq MCP Agent Service Integration")
    print("=" * 60)
    print(f"Loaded {len(AURUS_MCP_TOOLS)} MCP Tool JSON Schemas.")

    runtime = MockRuntime()
    agent = MCPAgentService(runtime, api_key="", model="openai/gpt-oss-120b")
    print(f"Agent service initialized. Ready state: {agent.ready}")

    # Test local dispatch of sensor reading tool
    print("\n[Test 1] Dispatching 'read_sensors' tool...")
    res_sensors = agent._dispatch_tool("read_sensors", {})
    print(f"Result: {res_sensors}")
    assert "distances_cm" in res_sensors and "front_clear" in res_sensors, "Sensor tool check failed"

    # Test local dispatch of rover status tool
    print("\n[Test 2] Dispatching 'get_rover_status' tool...")
    res_status = agent._dispatch_tool("get_rover_status", {})
    print(f"Result: {res_status}")
    assert "simulation_mode" in res_status, "Status tool check failed"

    # Test local dispatch of memory tool
    print("\n[Test 3] Dispatching 'recall_memories' tool...")
    res_mem = agent._dispatch_tool("recall_memories", {"user_id": 1})
    print(f"Result: {res_mem}")
    assert "memories" in res_mem, "Memory tool check failed"

    print("\n[PASS] All MCP Agent local dispatch unit tests passed successfully!")
    print("=" * 60)


if __name__ == "__main__":
    main()
