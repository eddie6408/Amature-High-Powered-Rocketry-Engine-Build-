"""Interoperability with other rocketry tools (OpenRocket, RASAero, ...).

Imported designs and results are reference/validation inputs; AERODYNE never
assumes another simulator is correct and reports differences instead.
"""

from aerodyne.interop.openrocket import OrkImport, read_ork

__all__ = ["OrkImport", "read_ork"]
