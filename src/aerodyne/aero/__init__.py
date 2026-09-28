"""Aerodynamics engine. Multiple interchangeable models behind one interface:
analytical (Barrowman + component drag build-up), tabulated (CFD / RASAero /
OpenRocket exports / wind tunnel), and cross-model comparison."""

from aerodyne.aero.analytical import AnalyticalAeroModel, barrowman_cp
from aerodyne.aero.model import AeroCoefficients, AeroModel, FlightCondition, ScaledAeroModel
from aerodyne.aero.tables import TableAeroModel, compare_models

__all__ = [
    "AnalyticalAeroModel", "barrowman_cp", "AeroCoefficients", "AeroModel", "FlightCondition",
    "ScaledAeroModel", "TableAeroModel", "compare_models",
]
