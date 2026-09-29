"""File-based workspace store."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aerodyne.config.management import ConfigurationError, FlightConfiguration, VehicleRegistry
from aerodyne.core.provenance import DataQuality, RecordStatus
from aerodyne.propulsion.database import MotorDatabase
from aerodyne.propulsion.formats import read_eng
from aerodyne.propulsion.motor import MotorPerformance, synthetic_motor
from aerodyne.workspace.design import Design, design_from_payload, design_to_payload
from aerodyne.workspace.mission import Mission

FORMAT_VERSION = 1
_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


class WorkspaceError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _safe_name(name: str) -> str:
    n = _SAFE.sub("_", name).strip("._")
    if not n:
        raise WorkspaceError("invalid file name")
    return n[:120]


def _write_json(path: Path, obj: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, sort_keys=True, default=str))
    os.replace(tmp, path)          # atomic on POSIX: never a half-written record


class Workspace:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.lock = threading.RLock()
        meta = self.root / "aerodyne.json"
        if not meta.exists():
            raise WorkspaceError(f"{self.root} is not an AERODYNE workspace (run `aerodyne init`)")
        self.meta = json.loads(meta.read_text())
        self.registry = VehicleRegistry.load(self.root / "registry.json")
        self.motors = MotorDatabase.load(self.root / "motors.json")

    # ---- lifecycle -------------------------------------------------------------------
    @classmethod
    def init(cls, root: str | Path, name: str, author: str = "unknown",
             with_example: bool = True) -> "Workspace":
        r = Path(root)
        r.mkdir(parents=True, exist_ok=True)
        if (r / "aerodyne.json").exists():
            raise WorkspaceError(f"{r} already contains a workspace")
        for sub in ("missions", "runs", "flights"):
            (r / sub).mkdir(exist_ok=True)
        _write_json(r / "aerodyne.json", {"name": name, "author": author, "created": _now(),
                                          "format_version": FORMAT_VERSION})
        VehicleRegistry().save(r / "registry.json")
        db = MotorDatabase()
        db.add(synthetic_motor())            # labelled HYPOTHETICAL: for learning the tools only
        db.save(r / "motors.json")
        ws = cls(r)
        if with_example:
            from aerodyne.examples import example_recovery, example_vehicle

            rec = ws.create_vehicle("Example 66 mm dual-deploy", Design(example_vehicle(),
                                    example_recovery(), notes="example design - not flight ready"),
                                    author=author)
            ws.save_mission(Mission(id="example", name="Example mission (synthetic motor)",
                                    vehicle_id=rec["vehicle_id"], revision="REV-A",
                                    motor_key=next(iter(ws.motor_keys())),
                                    site={"altitude_msl": 100.0, "latitude": 35.0,
                                          "longitude": -117.0, "rail_length": 1.8,
                                          "elevation_deg": 87.0, "azimuth_deg": 270.0},
                                    wind={"model": "power_law", "speed": 4.0, "from_deg": 270.0}))
        return ws

    def _save_registry(self) -> None:
        self.registry.save(self.root / "registry.json")

    def _save_motors(self) -> None:
        self.motors.save(self.root / "motors.json")

    # ---- vehicles ----------------------------------------------------------------------
    def create_vehicle(self, name: str, design: Design, author: str = "unknown") -> dict[str, Any]:
        with self.lock:
            rec = self.registry.create_vehicle(name, design_to_payload(design), author=author)
            self._save_registry()
            return self.vehicle_summary(rec.vehicle_id)

    def vehicle_summary(self, vehicle_id: str) -> dict[str, Any]:
        rec = self.registry.get(vehicle_id)
        return {"vehicle_id": rec.vehicle_id, "name": rec.name,
                "revisions": [{"label": r.label, "status": r.meta.status.value,
                               "change_note": r.change_note, "parent": r.parent,
                               "flights": list(r.flights), "config_hash": r.config_hash,
                               "updated_at": r.meta.updated_at.isoformat(), "author": r.meta.author}
                              for r in rec.revisions]}

    def list_vehicles(self) -> list[dict[str, Any]]:
        return [self.vehicle_summary(v.vehicle_id) for v in self.registry.vehicles()]

    def design(self, vehicle_id: str, revision: str | None = None) -> Design:
        rec = self.registry.get(vehicle_id)
        rev = rec.latest if revision is None else rec.revision(revision)
        return design_from_payload(rev.payload)

    def revision_payload(self, vehicle_id: str, revision: str) -> dict[str, Any]:
        return self.registry.get(vehicle_id).revision(revision).payload

    def save_design(self, vehicle_id: str, revision: str, design: Design) -> dict[str, Any]:
        """Edit an ACTIVE (unreleased, unflown) revision in place."""
        with self.lock:
            try:
                self.registry.update_in_place(vehicle_id, revision, design_to_payload(design))
            except ConfigurationError as exc:
                raise WorkspaceError(str(exc)) from exc
            self._save_registry()
            return self.vehicle_summary(vehicle_id)

    def revise(self, vehicle_id: str, design: Design, note: str, author: str = "unknown") -> dict[str, Any]:
        with self.lock:
            try:
                rev = self.registry.revise(vehicle_id, design_to_payload(design), note, author=author)
            except ConfigurationError as exc:
                raise WorkspaceError(str(exc)) from exc
            self._save_registry()
            return {"revision": rev.label, **self.vehicle_summary(vehicle_id)}

    # ---- motors ------------------------------------------------------------------------------
    def motor_keys(self) -> list[str]:
        return list(self.motors._motors.keys())

    def motor(self, key: str) -> MotorPerformance:
        try:
            return self.motors.by_key(key)
        except KeyError:
            raise WorkspaceError(f"unknown motor {key}") from None

    # launch-site library ---------------------------------------------------------------------
    SITE_FIELDS = {"name": str, "latitude": float, "longitude": float, "altitude_msl": float,
                   "waiver_ceiling_agl_m": float, "waiver_ref": str, "field_radius_m": float,
                   "rail_length_m": float, "club": str, "notes": str}

    def list_sites(self) -> list[dict]:
        p = self.root / "sites.json"
        return json.loads(p.read_text()) if p.is_file() else []

    def site(self, site_id: str) -> dict:
        for s in self.list_sites():
            if s["id"] == site_id:
                return s
        raise KeyError(site_id)

    def save_site(self, data: dict) -> dict:
        out: dict = {}
        for k, typ in self.SITE_FIELDS.items():
            v = data.get(k)
            if v in (None, ""):
                continue
            try:
                out[k] = typ(v)
            except (TypeError, ValueError) as exc:
                raise WorkspaceError(f"site {k}: {exc}") from exc
        if not out.get("name"):
            raise WorkspaceError("a site needs a name")
        if "latitude" not in out or "longitude" not in out:
            raise WorkspaceError("a site needs latitude and longitude")
        if not (-90 <= out["latitude"] <= 90 and -180 <= out["longitude"] <= 180):
            raise WorkspaceError("latitude must be -90..90 and longitude -180..180")
        for k in ("waiver_ceiling_agl_m", "field_radius_m", "rail_length_m"):
            if k in out and out[k] <= 0:
                raise WorkspaceError(f"{k} must be positive")
        with self.lock:
            sites = self.list_sites()
            sid = data.get("id") or _safe_name(out["name"].lower().replace(" ", "-"))[:40] or "site"
            if not data.get("id"):
                base, i = sid, 2
                while any(x["id"] == sid for x in sites):
                    sid, i = f"{base}-{i}", i + 1
            out["id"] = sid
            sites = [x for x in sites if x["id"] != sid] + [out]
            _write_json(self.root / "sites.json", sorted(sites, key=lambda x: x["name"].lower()))
        return out

    def delete_site(self, site_id: str) -> dict:
        with self.lock:
            sites = self.list_sites()
            if not any(x["id"] == site_id for x in sites):
                raise KeyError(site_id)
            _write_json(self.root / "sites.json", [x for x in sites if x["id"] != site_id])
        return {"deleted": site_id}

    # flyer profile (name, organisation, member number, certification level)
    PROFILE_FIELDS = {"name": str, "organization": str, "member_number": str, "cert_level": int, "cert_org": str}

    def profile(self) -> dict:
        p = self.root / "profile.json"
        return json.loads(p.read_text()) if p.is_file() else {}

    def save_profile(self, data: dict) -> dict:
        out: dict = {}
        for k, typ in self.PROFILE_FIELDS.items():
            v = data.get(k)
            if v in (None, ""):
                continue
            out[k] = typ(v)
        if "cert_level" in out and not 0 <= out["cert_level"] <= 3:
            raise WorkspaceError("certification level must be 0 to 3")
        with self.lock:
            _write_json(self.root / "profile.json", out)
        return out

    def import_eng(self, text: str, quality: DataQuality, source: str, source_date: str) -> list[str]:
        import tempfile

        with self.lock:
            with tempfile.NamedTemporaryFile("w", suffix=".eng", delete=False) as fh:
                fh.write(text)
                tmp = fh.name
            try:
                motors = read_eng(tmp, data_quality=quality, source=source, source_date=source_date)
            finally:
                os.unlink(tmp)
            keys = []
            for m in motors:
                try:
                    keys.append(self.motors.add(m))
                except ValueError:
                    keys.append(m.data_hash[:16])   # identical dataset already present
            self._save_motors()
            return keys

    def add_motor(self, m: MotorPerformance) -> str:
        with self.lock:
            k = self.motors.add(m)
            self._save_motors()
            return k

    # ---- missions -------------------------------------------------------------------------------
    def save_mission(self, m: Mission) -> Mission:
        with self.lock:
            m.id = _safe_name(m.id or uuid.uuid4().hex[:8])
            self.registry.get(m.vehicle_id).revision(m.revision)   # must exist
            self.motor(m.motor_key)
            _write_json(self.root / "missions" / f"{m.id}.json", m.to_dict())
            return m

    def mission(self, mission_id: str) -> Mission:
        p = self.root / "missions" / f"{_safe_name(mission_id)}.json"
        if not p.exists():
            raise WorkspaceError(f"unknown mission {mission_id}")
        return Mission.from_dict(json.loads(p.read_text()))

    def list_missions(self) -> list[Mission]:
        return [Mission.from_dict(json.loads(p.read_text()))
                for p in sorted((self.root / "missions").glob("*.json"))]

    # ---- runs ------------------------------------------------------------------------------------
    def save_run(self, kind: str, mission_id: str | None, data: dict[str, Any]) -> str:
        with self.lock:
            run_id = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}-{kind}-{uuid.uuid4().hex[:6]}"
            d = self.root / "runs" / run_id
            d.mkdir(parents=True)
            if "kind" in data or "id" in data:
                raise WorkspaceError("run data must not redefine 'id' or 'kind'")
            _write_json(d / "run.json", {"id": run_id, "kind": kind, "mission_id": mission_id,
                                         "created": _now(),
                                         "software_version": _software_version(), **data})
            return run_id

    def run(self, run_id: str) -> dict[str, Any]:
        p = self.root / "runs" / _safe_name(run_id) / "run.json"
        if not p.exists():
            raise WorkspaceError(f"unknown run {run_id}")
        return json.loads(p.read_text())

    def list_runs(self, mission_id: str | None = None, kind: str | None = None) -> list[dict[str, Any]]:
        out = []
        for p in sorted((self.root / "runs").glob("*/run.json"), reverse=True):
            r = json.loads(p.read_text())
            if (mission_id and r.get("mission_id") != mission_id) or (kind and r.get("kind") != kind):
                continue
            out.append({k: r.get(k) for k in ("id", "kind", "mission_id", "created", "summary",
                                               "status")})
        return out

    # ---- flights ---------------------------------------------------------------------------------
    def create_flight(self, flight_id: str, vehicle_id: str, revision: str, motor_key: str,
                      date: str, mission_id: str | None = None, notes: str = "",
                      hardware: dict[str, str] | None = None) -> dict[str, Any]:
        with self.lock:
            fid = _safe_name(flight_id)
            d = self.root / "flights" / fid
            if d.exists():
                raise WorkspaceError(f"flight {fid} already exists")
            rev = self.registry.get(vehicle_id).revision(revision)
            self.motor(motor_key)
            hw = {"hardware_version": "unspecified", "firmware_version": "unspecified",
                  "firmware_hash": "", "firmware_commit": "", "sensor_config": "unspecified",
                  "sensor_config_hash": "", "telemetry_protocol": "none", **(hardware or {})}
            cfg = FlightConfiguration(vehicle_id=vehicle_id, revision=revision,
                                      vehicle_config_hash=rev.config_hash, **hw)
            self.registry.record_flight(fid, cfg)      # marks the revision FLOWN (immutable)
            self._save_registry()
            (d / "raw").mkdir(parents=True)
            rec = {"flight_id": fid, "vehicle_id": vehicle_id, "revision": revision,
                   "motor_key": motor_key, "motor": self.motor(motor_key).summary(),
                   "date": date, "mission_id": mission_id, "notes": notes,
                   "configuration": dict(cfg.__dict__), "configuration_hash": cfg.config_hash,
                   "raw_files": [], "created": _now()}
            _write_json(d / "flight.json", rec)
            return rec

    def flight(self, flight_id: str) -> dict[str, Any]:
        p = self.root / "flights" / _safe_name(flight_id) / "flight.json"
        if not p.exists():
            raise WorkspaceError(f"unknown flight {flight_id}")
        return json.loads(p.read_text())

    def list_flights(self) -> list[dict[str, Any]]:
        return [json.loads(p.read_text()) for p in sorted((self.root / "flights").glob("*/flight.json"))]

    def add_raw_file(self, flight_id: str, name: str, data: bytes, source: str = "") -> dict[str, Any]:
        """Store measured data write-once. Existing raw files can never be replaced."""
        with self.lock:
            rec = self.flight(flight_id)
            fname = _safe_name(name)
            path = self.root / "flights" / rec["flight_id"] / "raw" / fname
            if path.exists():
                raise WorkspaceError(f"raw file {fname} already exists; raw data is never overwritten")
            path.write_bytes(data)
            path.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)   # read-only
            entry = {"name": fname, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
                     "imported": _now(), "source": source, "kind": "MEASURED"}
            rec["raw_files"].append(entry)
            _write_json(self.root / "flights" / rec["flight_id"] / "flight.json", rec)
            return entry

    def raw_path(self, flight_id: str, name: str) -> Path:
        rec = self.flight(flight_id)
        entry = next((f for f in rec["raw_files"] if f["name"] == name), None)
        if entry is None:
            raise WorkspaceError(f"flight {flight_id} has no raw file {name}")
        p = self.root / "flights" / rec["flight_id"] / "raw" / entry["name"]
        if hashlib.sha256(p.read_bytes()).hexdigest() != entry["sha256"]:
            raise WorkspaceError(f"raw file {name} fails its SHA-256 check - data was altered")
        return p

    def save_flight_analysis(self, flight_id: str, analysis: dict[str, Any]) -> None:
        with self.lock:
            rec = self.flight(flight_id)
            d = self.root / "flights" / rec["flight_id"]
            _write_json(d / "analysis.json", {"updated": _now(), **analysis})
            rec["analysis_summary"] = {k: analysis.get(k) for k in ("status", "actual_kind")}
            rec["analysis_summary"]["apogee_agl_m"] = (analysis.get("actual") or {}).get("apogee_agl_m")
            _write_json(d / "flight.json", rec)

    def flight_analysis(self, flight_id: str) -> dict[str, Any] | None:
        p = self.root / "flights" / _safe_name(flight_id) / "analysis.json"
        return json.loads(p.read_text()) if p.exists() else None

    def status(self) -> dict[str, Any]:
        flown = sum(1 for v in self.registry.vehicles() for r in v.revisions
                    if r.meta.status == RecordStatus.FLOWN)
        return {"name": self.meta["name"], "root": str(self.root),
                "vehicles": len(self.registry.vehicles()), "flown_revisions": flown,
                "motors": len(self.motors), "missions": len(self.list_missions()),
                "flights": len(self.list_flights())}


def _software_version() -> str:
    from aerodyne import __version__

    return __version__
