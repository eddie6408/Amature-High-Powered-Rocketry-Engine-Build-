"""Project workspace: the persistent, file-based home of a rocketry project.

A workspace is a directory (git-friendly JSON + write-once raw data):

    aerodyne.json            project metadata
    registry.json            vehicles, revisions, flown configurations (immutable once flown)
    motors.json              motor database (every dataset with source + quality)
    missions/<id>.json       simulation setups: vehicle rev + motor + site + wind + limits
    runs/<id>/run.json       saved simulation / Monte Carlo / SIL / readiness results
    flights/<id>/flight.json flight record; flights/<id>/raw/* write-once measured data
"""

from aerodyne.workspace.design import Design, design_from_payload, design_to_payload
from aerodyne.workspace.mission import Mission
from aerodyne.workspace.store import Workspace, WorkspaceError

__all__ = ["Design", "Mission", "Workspace", "WorkspaceError", "design_from_payload",
           "design_to_payload"]
