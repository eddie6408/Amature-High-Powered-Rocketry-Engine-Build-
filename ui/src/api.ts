/** Thin JSON client for the AERODYNE application server. */
export class ApiError extends Error {}

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method,
    headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  const text = await res.text();
  let data: unknown = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    throw new ApiError(`${res.status}: ${text.slice(0, 200)}`);
  }
  if (!res.ok) throw new ApiError((data as { error?: string })?.error ?? `HTTP ${res.status}`);
  return data as T;
}

export const api = {
  get: <T>(p: string) => call<T>("GET", p),
  post: <T>(p: string, b: unknown = {}) => call<T>("POST", p, b),
  put: <T>(p: string, b: unknown) => call<T>("PUT", p, b),
};

export async function fileToBase64(f: File): Promise<string> {
  const buf = new Uint8Array(await f.arrayBuffer());
  let s = "";
  for (let i = 0; i < buf.length; i += 0x8000) s += String.fromCharCode(...buf.subarray(i, i + 0x8000));
  return btoa(s);
}

export interface Job { id: string; kind: string; status: "running" | "done" | "error"; done: number;
  total: number; error: string | null; result_id?: string }

/** Poll a background job until it finishes. */
export async function waitJob(id: string, onProgress: (j: Job) => void): Promise<Job> {
  for (;;) {
    const j = await api.get<Job>(`/api/jobs/${id}`);
    onProgress(j);
    if (j.status !== "running") return j;
    await new Promise((r) => setTimeout(r, 700));
  }
}

// ---- shared types -------------------------------------------------------------
export interface Revision { label: string; status: string; change_note: string; parent: string | null;
  flights: string[]; config_hash: string; updated_at: string; author: string }
export interface VehicleSummary { vehicle_id: string; name: string; revisions: Revision[] }
export interface Component { type: string; name: string; x: number; [k: string]: unknown }
export interface DesignPayload {
  vehicle: { name: string; components: Component[]; launch_lug_drag_area: number; surface_roughness: number };
  recovery: { body_cd_area: number; devices: Record<string, unknown>[] } | null;
  avionics: Record<string, unknown>;
  notes: string;
}
export interface Motor { key: string; designation: string; manufacturer: string; classification: string;
  total_impulse_Ns: number; burn_time_s: number; average_thrust_N: number; peak_thrust_N: number;
  total_mass_kg: number; propellant_mass_kg: number | null; source: string; source_date: string;
  data_quality: string }
export interface Mission { id: string; name: string; vehicle_id: string; revision: string; motor_key: string;
  site: Record<string, number>; wind: Record<string, unknown>; atmosphere: Record<string, number>;
  limits: Record<string, number | null>; uncertainty: Record<string, number> }
export type Summary = Record<string, number | string | null>;
