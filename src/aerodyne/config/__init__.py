"""Engineering configuration management: vehicle IDs, revisions, flight configurations."""

from aerodyne.config.management import (
    ConfigurationError,
    FlightConfiguration,
    Revision,
    VehicleRecord,
    VehicleRegistry,
    revision_label,
)

__all__ = [
    "ConfigurationError",
    "FlightConfiguration",
    "Revision",
    "VehicleRecord",
    "VehicleRegistry",
    "revision_label",
]
