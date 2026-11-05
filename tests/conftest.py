"""All automated tests must remain simulation-only, including on a Pi."""

import pytest

from src.hardware import motors, sensors


@pytest.fixture(autouse=True)
def simulation_only(monkeypatch):
    monkeypatch.setattr(motors, "IS_SIMULATION", True)
    monkeypatch.setattr(sensors, "IS_SIMULATION", True)
