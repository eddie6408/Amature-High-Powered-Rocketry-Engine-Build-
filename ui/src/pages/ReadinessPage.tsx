import { useEffect, useState } from "react";

import { api, waitJob, type Job, type Mission } from "../api";
import { Card, ErrorBox, Progress, SelectField, StatusPill } from "../components/ui";
import { go } from "../router";

interface Check { id: string; name: string; status: string; value: string; requirement: string; critical: boolean;
  evidence: string; action: string }
interface Report { id: string; created: string; status: string; checks: Check[]; blocking: string[]; note: string;
  evidence_runs: Record<string, string | null> }
interface SilRun { id: string; created: string; backend: string; summary: { passed: number; total: number };
  results: Array<{ scenario: string; issues: string[]; final_state: string | null; transitions: Array<[number, string, string]>;
                   faults_detected: string[] }> }

export function ReadinessPage({ missionId }: { missionId?: string }) {
  const [missions, setMissions] = useState<Mission[]>([]);
  const [report, setReport] = useState<Report | null>(null);
  const [sil, setSil] = useState<SilRun | null>(null);
  const [backend, setBackend] = useState("c-app");
  const [job, setJob] = useState<Job | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => { api.get<Mission[]>("/api/missions").then(setMissions); }, []);
  useEffect(() => { if (!missionId && missions.length) go("readiness", missions[0].id); }, [missionId, missions]);
  useEffect(() => {
    setReport(null); setSil(null);
    if (!missionId) return;
    api.get<Array<{ id: string; kind: string }>>(`/api/runs?mission=${missionId}`).then(async (runs) => {
      const r = runs.find((x) => x.kind === "readiness");
      const s = runs.find((x) => x.kind === "sil");
      if (r) setReport(await api.get<Report>(`/api/runs/${r.id}`));
      if (s) setSil(await api.get<SilRun>(`/api/runs/${s.id}`));
    });
  }, [missionId]);

  const runSil = async () => {
    setErr(null);
    try {
      const { job: jid } = await api.post<{ job: string }>(`/api/missions/${missionId}/sil`, { backend });
      const j = await waitJob(jid, setJob);
      if (j.status === "error") setErr(j.error);
      else if (j.result_id) setSil(await api.get<SilRun>(`/api/runs/${j.result_id}`));
    } catch (e) { setErr((e as Error).message); }
    setJob(null);
  };
  const runReview = async () => {
    setErr(null); setBusy(true);
    try { setReport(await api.post<Report>(`/api/missions/${missionId}/readiness`)); } catch (e) { setErr((e as Error).message); }
    setBusy(false);
  };

  return (
    <div className="page stack">
      <div className="toolbar">
        <h1>Test &amp; readiness</h1>
        <SelectField label="Mission" value={missionId ?? ""} onChange={(v) => go("readiness", v)}
                     options={missions.map((m) => [m.id, m.name] as [string, string])} />
      </div>
      <p className="lead">Everything that can be checked before cutting a single part: the flight software is flown
        through the fault suite, then every prediction is reviewed against the mission limits. Results computed for a
        different design, motor or mission are never reused.</p>
      <ErrorBox error={err} />
      <Card title="Flight software fault suite (software-in-the-loop)" actions={
        <>
          <SelectField label="Flight software" value={backend} onChange={setBackend}
                       options={[["c-app", "C application (firmware)"], ["c", "C state machine + filter"], ["python", "Python reference"]]} />
          <button className="primary" onClick={runSil} disabled={!!job || !missionId}>Run suite</button>
        </>}>
        {job && <Progress done={job.done} total={job.total} label="scenarios" />}
        {sil && (
          <>
            <div className="note">Run {sil.id} · backend {sil.backend} · <StatusPill status={sil.summary.passed === sil.summary.total ? "PASS" : "FAIL"}
              label={`${sil.summary.passed}/${sil.summary.total} pass`} /></div>
            <table>
              <thead><tr><th>Scenario</th><th>Result</th><th>State sequence</th><th>Faults detected</th></tr></thead>
              <tbody>
                {sil.results.map((r) => (
                  <tr key={r.scenario}>
                    <td>{r.scenario}</td>
                    <td><StatusPill status={r.issues.length ? "FAIL" : "PASS"} />{r.issues.map((i) => <div key={i} className="small">{i}</div>)}</td>
                    <td className="small">{r.transitions.map(([t, s]) => `${s}@${t.toFixed(1)}s`).join(" → ")}</td>
                    <td className="small">{r.faults_detected.join(", ") || "none"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
      </Card>
      <Card title="Readiness review" actions={<button className="primary" onClick={runReview} disabled={busy || !missionId}>{busy ? "Reviewing…" : "Run review"}</button>}>
        {report && (
          <>
            <div className={`banner ${report.status === "GO" ? "go" : report.status === "NO-GO" ? "nogo" : "warn"}`}>
              <StatusPill status={report.status} /> {report.blocking.length > 0 && <>blocking: {report.blocking.join(", ")}</>}
            </div>
            <table>
              <thead><tr><th>Check</th><th>Status</th><th>Value</th><th>Requirement</th><th>Evidence</th><th>Action</th></tr></thead>
              <tbody>
                {report.checks.map((c) => (
                  <tr key={c.id}>
                    <td>{c.name}{c.critical && <span className="muted"> · critical</span>}</td>
                    <td><StatusPill status={c.status} /></td>
                    <td>{c.value}</td><td className="small">{c.requirement}</td>
                    <td className="small">{c.evidence}</td><td className="small">{c.action}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="note">{report.note} Review {report.id}.</div>
          </>
        )}
      </Card>
    </div>
  );
}
