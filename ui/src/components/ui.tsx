import type { ReactNode } from "react";

export type Status = "PASS" | "GO" | "OK" | "WARN" | "GO WITH WARNINGS" | "DEGRADED" | "FAIL" | "NO-GO" |
  "FAILED" | "NOT RUN" | "INFO" | "UNKNOWN" | string;

const CLASS: Record<string, string> = {
  PASS: "h-OK", GO: "h-OK", OK: "h-OK", VALIDATED: "h-OK", READY: "h-OK",
  WARN: "h-DEGRADED", "GO WITH WARNINGS": "h-DEGRADED", DEGRADED: "h-DEGRADED", "NOT RUN": "h-DEGRADED",
  FAIL: "h-FAILED", "NO-GO": "h-FAILED", FAILED: "h-FAILED", DEVIATION: "h-DEGRADED",
  INFO: "h-UNKNOWN", UNKNOWN: "h-UNKNOWN",
};
const ICON: Record<string, string> = { "h-OK": "✓", "h-DEGRADED": "!", "h-FAILED": "✕", "h-UNKNOWN": "i" };

/** Status is never colour alone: icon + label, colour reserved for status. */
export function StatusPill({ status, label }: { status: Status; label?: string }) {
  const cls = CLASS[status] ?? "h-UNKNOWN";
  return (
    <span className={`pill ${cls}`}>
      <span className="icon" aria-hidden="true">{ICON[cls]}</span>
      {label ?? status}
    </span>
  );
}

export function KindBadge({ kind }: { kind: string }) {
  return <span className={`kind kind-${kind}`} title="data provenance">{kind}</span>;
}

export function Card({ title, children, actions, className = "" }: {
  title?: ReactNode; children: ReactNode; actions?: ReactNode; className?: string }) {
  return (
    <section className={`card ${className}`}>
      {(title || actions) && (
        <div className="card-head">
          {title && <h2>{title}</h2>}
          {actions && <div className="actions">{actions}</div>}
        </div>
      )}
      {children}
    </section>
  );
}

export function Stat({ label, value, unit, sub }: { label: string; value: ReactNode; unit?: string; sub?: ReactNode }) {
  return (
    <div className="tile">
      <div className="label">{label}</div>
      <div className="value">{value}{unit && <span className="unit">{unit}</span>}</div>
      {sub && <div className="sub">{sub}</div>}
    </div>
  );
}

export function ErrorBox({ error }: { error: string | null }) {
  return error ? <div className="error" role="alert">{error}</div> : null;
}

export function NumberField({ label, value, unit, onChange, optional, step = "any", disabled }: {
  label: string; value: number | null | undefined; unit?: string; optional?: boolean; step?: string;
  onChange: (v: number | null) => void; disabled?: boolean }) {
  return (
    <label className="field">
      <span>{label}{unit && <em> ({unit})</em>}</span>
      <input type="number" step={step} disabled={disabled}
             value={value === null || value === undefined ? "" : String(value)}
             placeholder={optional ? "—" : undefined}
             onChange={(e) => {
               const t = e.target.value;
               if (t === "") onChange(optional ? null : 0);
               else if (Number.isFinite(Number(t))) onChange(Number(t));
             }} />
    </label>
  );
}

export function TextField({ label, value, onChange, disabled }: {
  label: string; value: string; onChange: (v: string) => void; disabled?: boolean }) {
  return (
    <label className="field">
      <span>{label}</span>
      <input type="text" value={value} disabled={disabled} onChange={(e) => onChange(e.target.value)} />
    </label>
  );
}

export function SelectField({ label, value, options, onChange, disabled }: {
  label: string; value: string; options: Array<string | [string, string]>; onChange: (v: string) => void;
  disabled?: boolean }) {
  return (
    <label className="field">
      <span>{label}</span>
      <select value={value} disabled={disabled} onChange={(e) => onChange(e.target.value)}>
        {options.map((o) => {
          const [v, l] = Array.isArray(o) ? o : [o, o];
          return <option key={v} value={v}>{l}</option>;
        })}
      </select>
    </label>
  );
}

export function Progress({ done, total, label }: { done: number; total: number; label: string }) {
  const pct = total ? Math.round((100 * done) / total) : 0;
  return (
    <div className="progress" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
      <div className="bar" style={{ width: `${pct}%` }} />
      <span>{label} {done}/{total || "?"}</span>
    </div>
  );
}
