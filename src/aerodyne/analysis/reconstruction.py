"""Post-flight reconstruction from actual sensor data.

Forward Kalman filter + Rauch-Tung-Striebel smoother over the barometer and
accelerometer, phase detection, GNSS track and gyro attitude. All outputs are
DERIVED from measurements (or HYPOTHETICAL if the input data was synthetic).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from aerodyne.analysis.ingestion import FlightDataset
from aerodyne.core.provenance import DataKind
from aerodyne.dynamics import quaternion as quat

G = 9.80665
EARTH_R = 6371000.0


@dataclass
class ReconstructedFlight:
    t: np.ndarray
    altitude: np.ndarray            # m AGL (smoothed)
    velocity: np.ndarray            # m/s vertical (smoothed)
    acceleration: np.ndarray        # m/s^2 axial specific force (measured)
    altitude_sigma: np.ndarray
    phases: dict[str, float]        # liftoff, burnout, apogee, landing (s)
    position_en: np.ndarray | None  # (M,2) GNSS east/north from pad, with its own timebase
    position_t: np.ndarray | None
    attitude: np.ndarray | None
    kind: DataKind
    notes: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, float | None]:
        i = int(np.argmax(self.altitude))
        land = self.phases.get("landing")
        bo = self.phases.get("burnout")
        out = {
            "apogee_agl_m": float(self.altitude[i]),
            "apogee_sigma_m": float(self.altitude_sigma[i]),
            "time_to_apogee_s": float(self.t[i]),
            "max_vertical_velocity_mps": float(np.max(self.velocity)),
            "max_axial_accel_mps2": float(np.max(self.acceleration)),
            "burnout_time_s": bo,
            "burnout_velocity_mps": float(np.interp(bo, self.t, self.velocity)) if bo else None,
            "burnout_altitude_agl_m": float(np.interp(bo, self.t, self.altitude)) if bo else None,
            "flight_time_s": land,
            "landing_east_m": None,
            "landing_north_m": None,
            "kind": self.kind.value,
        }
        if self.position_en is not None and len(self.position_en):
            out["landing_east_m"] = float(self.position_en[-1, 0])
            out["landing_north_m"] = float(self.position_en[-1, 1])
        return out


class FlightReconstructionEngine:
    def __init__(self, dataset: FlightDataset, accel_sigma: float = 1.0, baro_sigma: float = 1.0,
                 rate: float = 100.0) -> None:
        self.ds = dataset
        self.accel_sigma = accel_sigma
        self.baro_sigma = baro_sigma
        self.rate = rate

    def run(self) -> ReconstructedFlight:
        ds = self.ds
        kinds = {c.kind for c in ds.channels.values()}
        kind = DataKind.HYPOTHETICAL if DataKind.HYPOTHETICAL in kinds else DataKind.DERIVED
        notes = list(ds.notes)
        if ds.time_offset == 0.0:
            if "accel_axial" in ds.channels:
                ds.align_to_liftoff()
            else:
                ds.align_to_liftoff_baro()
        tb, zb = ds.t("baro_alt"), ds.v("baro_alt")
        ground = float(np.median(zb[tb < -0.5])) if np.any(tb < -0.5) else float(zb[0])
        if not np.any(tb < -0.5):
            notes.append("no pre-launch baro data: ground reference taken from first sample")
        t = np.arange(max(tb[0], ds.t("accel")[0] if "accel" in ds.channels else tb[0]),
                      tb[-1], 1.0 / self.rate)
        have_acc = "accel" in ds.channels
        if have_acc:
            acc_vec = np.column_stack([np.interp(t, ds.t("accel"), ds.v("accel")[:, k])
                                       for k in range(3)])
            a_axial = acc_vec[:, 0]
        else:
            a_axial = np.zeros_like(t)
            notes.append("no accelerometer: reconstruction is baro-only")
        # vertical acceleration: use gyro attitude if available, else axial
        a_vert, att = self._vertical_accel(t, acc_vec if have_acc else None)

        # phase detection (needed for descent handling)
        phases = self._phases(t, a_axial, tb, zb - ground)
        t_apo_rough = phases.get("apogee_rough", t[-1])

        # forward KF
        n = len(t)
        xs = np.zeros((n, 2)); Ps = np.zeros((n, 2, 2))
        xp = np.zeros((n, 2)); Pp = np.zeros((n, 2, 2))
        x = np.array([0.0, 0.0]); P = np.diag([4.0, 1.0])
        bi = 0
        # outlier gate on baro samples; wider without an accelerometer, where the
        # prediction cannot follow the boost and a tight gate would reject good data
        gate = 8.0 if have_acc else 30.0
        dt = 1.0 / self.rate
        F = np.array([[1, dt], [0, 1]])
        B = np.array([0.5 * dt * dt, dt])
        for k in range(n):
            descent = t[k] > t_apo_rough
            u = 0.0 if (descent or not have_acc) else a_vert[k] - G
            # without an accelerometer the boost is an unmodelled 10+ g manoeuvre
            s = 6.0 if descent else (self.accel_sigma if have_acc else 40.0)
            if k > 0:
                x = F @ x + B * u
                P = F @ P @ F.T + np.outer(B, B) * s * s
            xp[k], Pp[k] = x, P
            while bi < len(tb) and tb[bi] <= t[k]:
                z = zb[bi] - ground
                S = P[0, 0] + self.baro_sigma ** 2
                if abs(z - x[0]) / math.sqrt(S) < gate:
                    K = P[:, 0] / S
                    x = x + K * (z - x[0])
                    P = P - np.outer(K, P[0, :])
                bi += 1
            xs[k], Ps[k] = x, P
        # RTS smoother
        xsm = xs.copy(); Psm = Ps.copy()
        for k in range(n - 2, -1, -1):
            C = Ps[k] @ F.T @ np.linalg.inv(Pp[k + 1])
            xsm[k] = xs[k] + C @ (xsm[k + 1] - xp[k + 1])
            Psm[k] = Ps[k] + C @ (Psm[k + 1] - Pp[k + 1]) @ C.T
        alt, vel = xsm[:, 0], xsm[:, 1]
        i_apo = int(np.argmax(alt))
        if not have_acc:
            phases["burnout"] = float(t[int(np.argmax(vel[:i_apo + 1]))])
            notes.append("burnout estimated from the peak of baro-derived velocity (no accelerometer)")
        phases["apogee"] = float(t[i_apo])
        phases.pop("apogee_rough", None)
        # landing: first time after apogee that altitude stays within 5 m of the final level for 3 s
        final = float(np.median(alt[-int(2 * self.rate):]))
        after = np.nonzero((t > t[i_apo]) & (np.abs(alt - final) < 5.0) & (np.abs(vel) < 2.0))[0]
        if len(after) and t[-1] - t[after[0]] > 3.0:
            phases["landing"] = float(t[after[0]])

        pos_en = pos_t = None
        if "gnss" in ds.channels:
            g = ds.v("gnss")
            tg = ds.t("gnss")
            pre = tg < -0.5
            lat0 = float(np.median(g[pre, 0])) if np.any(pre) else g[0, 0]
            lon0 = float(np.median(g[pre, 1])) if np.any(pre) else g[0, 1]
            north = np.radians(g[:, 0] - lat0) * EARTH_R
            east = np.radians(g[:, 1] - lon0) * EARTH_R * math.cos(math.radians(lat0))
            pos_en, pos_t = np.column_stack([east, north]), tg
            if "landing" in phases:
                sel = tg >= phases["landing"]
                if np.any(sel):
                    pos_en = np.vstack([pos_en[~sel], pos_en[sel].mean(axis=0)])
                    pos_t = np.concatenate([tg[~sel], [tg[sel][0]]])
        notes.append("altitude_sigma is the smoother's statistical uncertainty; it excludes "
                     "systematic baro errors (port pressure, transonic effects, calibration)")
        return ReconstructedFlight(
            t=t, altitude=alt, velocity=vel, acceleration=a_axial,
            altitude_sigma=np.sqrt(np.maximum(Psm[:, 0, 0], 0)), phases=phases,
            position_en=pos_en, position_t=pos_t, attitude=att, kind=kind, notes=notes)

    def _vertical_accel(self, t: np.ndarray, acc: np.ndarray | None):
        if acc is None:
            return np.zeros_like(t), None
        ds = self.ds
        pad = t < 0
        f0 = acc[pad].mean(axis=0) if np.any(pad) else acc[0]
        q = quat.from_two_vectors(f0, np.array([0.0, 0.0, 1.0]))
        if "gyro" not in ds.channels:
            R = quat.to_matrix(q)
            return (acc @ R.T)[:, 2], None
        gyro = np.column_stack([np.interp(t, ds.t("gyro"), ds.v("gyro")[:, k]) for k in range(3)])
        bias = gyro[pad].mean(axis=0) if np.any(pad) else np.zeros(3)
        qs = np.zeros((len(t), 4))
        av = np.zeros(len(t))
        for k in range(len(t)):
            if k > 0 and t[k] > 0:
                w = gyro[k] - bias
                ang = float(np.linalg.norm(w)) * (t[k] - t[k - 1])
                if ang > 1e-12:
                    q = quat.normalize(quat.multiply(q, quat.from_axis_angle(w, ang)))
            qs[k] = q
            av[k] = (quat.to_matrix(q) @ acc[k])[2]
        return av, qs

    @staticmethod
    def _phases(t, a_axial, tb, zb_agl) -> dict[str, float]:
        ph: dict[str, float] = {"liftoff": 0.0}
        boost = np.nonzero((t > 0) & (a_axial < 0))[0]
        if len(boost):
            ph["burnout"] = float(t[boost[0]])
        # rough apogee from a median-filtered baro trace (outlier-robust)
        k = 5
        zf = np.array([np.median(zb_agl[max(0, i - k):i + k + 1]) for i in range(len(zb_agl))])
        ph["apogee_rough"] = float(tb[int(np.argmax(zf))])
        return ph
