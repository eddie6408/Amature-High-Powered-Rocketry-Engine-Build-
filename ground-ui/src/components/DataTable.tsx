import type { TelemetryPacket } from "../telemetry";
import { STATES } from "../telemetry";
import { fmt } from "./scale";

/** Table view: the accessible, tooltip-free route to every plotted value. */
export function DataTable({ packets }: { packets: TelemetryPacket[] }) {
  const rows = packets.slice(-200).reverse();
  return (
    <div className="card">
      <h2>Packets <span className="unit">(latest 200, newest first)</span></h2>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>seq</th><th>t (s)</th><th>state</th><th>alt (m)</th><th>vel (m/s)</th>
              <th>accel (m/s²)</th><th>batt (V)</th><th>lat</th><th>lon</th><th>sats</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((p) => (
              <tr key={p.sequence}>
                <td>{p.sequence}</td>
                <td>{fmt(p.timestampMs / 1000, 2)}</td>
                <td>{STATES[p.systemStatus] ?? p.systemStatus}</td>
                <td>{fmt(p.altitude, 1)}</td>
                <td>{fmt(p.velocity, 1)}</td>
                <td>{Number.isFinite(p.acceleration) ? fmt(p.acceleration, 1) : "—"}</td>
                <td>{fmt(p.batteryMv / 1000, 2)}</td>
                <td>{p.gnssFix ? p.latitude.toFixed(6) : "—"}</td>
                <td>{p.gnssFix ? p.longitude.toFixed(6) : "—"}</td>
                <td>{p.gnssSats}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
