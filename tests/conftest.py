import pytest

from aerodyne.dynamics.simulator import FlightSimulator
from aerodyne.examples import example_config


@pytest.fixture(scope="session")
def nominal_sim():
    return FlightSimulator(example_config()).run()
