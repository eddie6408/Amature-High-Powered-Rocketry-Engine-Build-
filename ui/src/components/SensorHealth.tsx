import type { Health, SensorSlot } from "../telemetry";

const NAMES: Record<SensorSlot, string> = {
  imu_accel: "Accelerometer", imu_gyro: "Gyroscope", baro: "Barometer", gnss: "GNSS",
  battery: "Battery monitor", temperature: "Temperature", storage: "Storage", radio: "Radio",
};
const ICON: Record<Health, string> = { OK: "✓", DEGRADED: "!", FAILED: "✕", UNKNOWN: "?" };

export function SensorHealth({ health }: { health: Record<SensorSlot, Health> | null }) {
  return (
    <div className="card">
      <h2>Sensor health</h2>
      {!health ? (
        <div className="empty">No data</div>
      ) : (
        <ul className="health">
          {(Object.keys(NAMES) as SensorSlot[]).map((k) => (
            <li key={k} className={`h-${health[k]}`}>
              <span className="icon" aria-hidden="true">{ICON[health[k]]}</span>
              <span className="name">{NAMES[k]}</span>
              <span className="status">{health[k]}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
