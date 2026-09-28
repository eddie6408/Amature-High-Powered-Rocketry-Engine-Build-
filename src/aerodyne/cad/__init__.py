"""CAD integration: geometry and mass-property ingestion from CAD tools.

AERODYNE does not replace CAD. It ingests
* triangulated geometry (STL, ASCII or binary) and computes exact mesh mass
  properties for a given material density;
* mass-property tables exported from Fusion 360 / SolidWorks / FreeCAD
  ("physical properties" reports) as CSV;
and turns them into :class:`CadPart` components for the vehicle model.
"""

from aerodyne.cad.mass_import import read_mass_properties_csv
from aerodyne.cad.stl import CadMassProperties, MeshMassProperties, mesh_mass_properties, read_stl
from aerodyne.vehicle.components import CadPart

__all__ = ["CadMassProperties", "CadPart", "MeshMassProperties", "mesh_mass_properties",
           "read_mass_properties_csv", "read_stl"]
