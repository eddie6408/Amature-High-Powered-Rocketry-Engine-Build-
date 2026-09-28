"""Software-in-the-loop, hardware-in-the-loop interface and fault injection.

    FLIGHT SIMULATOR -> VIRTUAL SENSOR DATA -> FLIGHT SOFTWARE -> TELEMETRY
"""

from aerodyne.sil.faults import FaultInjection, FaultKind
from aerodyne.sil.runner import SILResult, SILRunner
from aerodyne.sil.virtual_sensors import SensorNoise, VirtualSensors

__all__ = ["FaultInjection", "FaultKind", "SILResult", "SILRunner", "SensorNoise",
           "VirtualSensors"]
