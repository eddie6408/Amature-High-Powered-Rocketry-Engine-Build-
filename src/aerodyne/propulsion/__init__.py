"""Propulsion as an *input* to the engineering system.

Scope: commercially manufactured / certified motors, published performance
data and properly measured thrust data. This package characterizes and
simulates motor performance; it deliberately contains no propellant
formulation, motor manufacturing or ignition-system construction content.
"""

from aerodyne.propulsion.analyzer import MotorAnalysisResult, analyze_thrust_data
from aerodyne.propulsion.database import MotorDatabase
from aerodyne.propulsion.formats import read_eng, read_thrust_csv, write_eng
from aerodyne.propulsion.motor import MotorMetadata, MotorPerformance, impulse_class

__all__ = [
    "MotorAnalysisResult", "analyze_thrust_data", "MotorDatabase", "read_eng",
    "read_thrust_csv", "write_eng", "MotorMetadata", "MotorPerformance", "impulse_class",
]
