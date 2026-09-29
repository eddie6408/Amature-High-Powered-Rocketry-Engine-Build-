import type * as CesiumType from "cesium";
import { useEffect, useRef, useState } from "react";

import { api, type Mission } from "../api";
import { fmt } from "../components/scale";
import { Card, ErrorBox, NumberField, SelectField, TextField } from "../components/ui";
import { type Dispersion, fractionInside } from "../dispersion";
import { fmtCoords, locationError, parseCoord } from "../geo";
import { go } from "../router";
import { dispersionLayer, imageryFor, loadCesium, shadeRelief, type Source, SOURCES, store, stored, terrainProvider } from "./EarthView";

export interface Site { id: string; name: string; latitude: number; longitude: number; altitude_msl?: number; waiver_ceiling_agl_m?: number;
  waiver_ref?: string; field_radius_m?: number; rail_length_m?: number; club?: string; notes?: string }
interface Draft { id?: string; name: string; lat: string; lon: string; altitude_msl: number | null; waiver_ceiling_agl_m: number | null;
  waiver_ref: string; field_radius_m: number | null; rail_length_m: number | null; club: string; notes: string }

const FT = 0.3048;
const empty: Draft = { name: "", lat: "", lon: "", altitude_msl: null, waiver_ceiling_agl_m: null, waiver_ref: "", field_radius_m: null,
  rail_length_m: null, club: "", notes: "" };
const toDraft = (s: Site): Draft => ({ id: s.id, name: s.name, lat: String(s.latitude), lon: String(s.longitude), altitude_msl: s.altitude_msl ?? null,
  waiver_ceiling_agl_m: s.waiver_ceiling_agl_m ?? null, waiver_ref: s.waiver_ref ?? "", field_radius_m: s.field_radius_m ?? null,
  rail_length_m: s.rail_length_m ?? null, club: s.club ?? "", notes: s.notes ?? "" });

export function SitesPage({ siteId }: { siteId?: string }) {
  const [sites, setSites] = useState<Site[]>([]);
  const [missions, setMissions] = useState<Mission[]>([]);
  const [draft, setDraft] = useState<Draft>(empty);
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [mission, setMission] = useState("");
  const [disp, setDisp] = useState<Dispersion | null>(null);
  const load = () => api.get<Site[]>("/api/sites").then(setSites);
  useEffect(() => { load(); api.get<Mission[]>("/api/missions").then(setMissions); }, []);
  const site = sites.find((s) => s.id === siteId) ?? null;
  useEffect(() => { setDraft(site ? toDraft(site) : empty); setMsg(null); }, [siteId, sites.length]);
  useEffect(() => {
    setDisp(null);
    if (mission) api.get<Dispersion>(`/api/missions/${mission}/dispersion`).then(setDisp).catch(() => undefined);
  }, [mission]);
  const set = (p: Partial<Draft>) => setDraft((d) => ({ ...d, ...p }));
  const lat = parseCoord(draft.lat, "lat"), lon = parseCoord(draft.lon, "lon");

  const save = async () => {
    setErr(null);
    try {
      const out = await api.post<Site>("/api/sites", { ...draft, latitude: lat, longitude: lon });
      await load(); go("sites", out.id); setMsg("Saved");
    } catch (e) { setErr((e as Error).message); }
  };
  const remove = async () => {
    if (!site || !window.confirm(`Delete the site "${site.name}"? Missions keep their copied coordinates.`)) return;
    try { await api.del(`/api/sites/${site.id}`); await load(); go("sites"); } catch (e) { setErr((e as Error).message); }
  };
  const apply = async () => {
    if (!site || !mission) return;
    setErr(null);
    try {
      await api.post(`/api/missions/${mission}/apply-site`, { site_id: site.id });
      setMissions(await api.get<Mission[]>("/api/missions"));
      setDisp(await api.get<Dispersion>(`/api/missions/${mission}/dispersion`));
      setMsg(`Applied to the mission: position, rail length, waiver ceiling and field radius. Re-run its simulations.`);
    } catch (e) { setErr((e as Error).message); }
  };
  const here = () => {
    if (!("geolocation" in navigator)) { setErr(locationError("unsupported")); return; }
    if (!window.isSecureContext) { setErr(locationError("insecure")); return; }
    navigator.geolocation.getCurrentPosition(
      (p) => set({ lat: p.coords.latitude.toFixed(6), lon: p.coords.longitude.toFixed(6),
                   altitude_msl: p.coords.altitude !== null && (p.coords.altitudeAccuracy ?? 99) <= 15 ? Math.round(p.coords.altitude) : draft.altitude_msl }),
      (e) => setErr(locationError(e.code)), { enableHighAccuracy: true, timeout: 20000 });
  };
  const using = missions.filter((m) => (m as Mission & { site_id?: string }).site_id === site?.id);
  const radius = draft.field_radius_m;
  const inside = disp && radius ? fractionInside(disp.points, radius) : null;

  return (
    <div className="page stack">
      <div className="two-col">
        <Card title="Sites" actions={<button onClick={() => { go("sites"); setDraft(empty); }}>+ New</button>}>
          {sites.length === 0 ? <div className="empty">No sites yet. Add your club's field.</div> : (
            <ul className="list">
              {sites.map((s) => (
                <li key={s.id} className={s.id === siteId ? "active" : ""}>
                  <a href={`#/sites/${s.id}`}><strong>{s.name}</strong>
                    <span className="muted"> · {s.waiver_ceiling_agl_m ? `${fmt(s.waiver_ceiling_agl_m / FT)} ft waiver` : "no waiver set"}</span></a>
                </li>
              ))}
            </ul>
          )}
        </Card>
        <div className="stack">
          <Card title={site ? site.name : "New site"}>
            <div className="presets"><button onClick={here}>◎ Use my location</button></div>
            <div className="form grid-form">
              <TextField label="Name" value={draft.name} onChange={(v) => set({ name: v })} />
              <TextField label="Club / range" value={draft.club} onChange={(v) => set({ club: v })} />
              <TextField label="Latitude" value={draft.lat} onChange={(v) => set({ lat: v })} />
              <TextField label="Longitude" value={draft.lon} onChange={(v) => set({ lon: v })} />
              <NumberField label="Pad altitude (above sea level)" unit="m" optional value={draft.altitude_msl} onChange={(v) => set({ altitude_msl: v })} />
              <NumberField label="Waiver ceiling (AGL)" unit="m" optional value={draft.waiver_ceiling_agl_m} onChange={(v) => set({ waiver_ceiling_agl_m: v })} />
              <TextField label="Waiver / COA reference" value={draft.waiver_ref} onChange={(v) => set({ waiver_ref: v })} />
              <NumberField label="Recovery field radius" unit="m" optional value={draft.field_radius_m} onChange={(v) => set({ field_radius_m: v })} />
              <NumberField label="Rail length" unit="m" optional value={draft.rail_length_m} onChange={(v) => set({ rail_length_m: v })} />
            </div>
            <textarea rows={2} className="wide" placeholder="Notes (hazards, access, pad layout, contacts)" value={draft.notes} onChange={(e) => set({ notes: e.target.value })} />
            <div className="note">
              {lat !== null && lon !== null ? fmtCoords(lat, lon) : "Coordinates: decimal degrees or 40°7'24\"N"}
              {draft.waiver_ceiling_agl_m ? ` · ceiling ${fmt(draft.waiver_ceiling_agl_m / FT)} ft AGL` : ""}
            </div>
            <div className="presets">
              <button className="primary" onClick={save} disabled={!draft.name.trim() || lat === null || lon === null}>Save site</button>
              {site && <button className="danger" onClick={remove}>Delete</button>}
              {msg && <span className="ok-line">{msg}</span>}
            </div>
            <ErrorBox error={err} />
          </Card>
          {site && (
            <Card title="Missions at this site">
              <div className="form grid-form">
                <SelectField label="Mission" value={mission} onChange={setMission}
                             options={[["", "—"], ...missions.map((m) => [m.id, `${m.name} · ${m.vehicle_id} ${m.revision}`] as [string, string])]} />
                <div className="field"><span>&nbsp;</span><button onClick={apply} disabled={!mission}>Apply this site to the mission</button></div>
              </div>
              {using.length > 0 && <div className="note">Using this site: {using.map((m) => <a key={m.id} href={`#/missions/${m.id}`}>{m.name} </a>)}</div>}
              {disp && (
                <div className="note">
                  {disp.run_id ? <>Monte Carlo: {disp.points.length} landings{disp.current ? "" : " (stale: re-run for the current design)"}
                    {inside !== null && <> · <strong>{fmt(inside * 100)}%</strong> inside the {fmt(radius!)} m field</>}
                    {disp.ellipse && <> · 95 % ellipse {fmt(disp.ellipse.semi_major_m)} × {fmt(disp.ellipse.semi_minor_m)} m</>}</>
                    : <>No Monte Carlo yet for this mission. <a href={`#/missions/${mission}`}>Run one</a> to see the landing ellipse.</>}
                </div>
              )}
            </Card>
          )}
          {lat !== null && lon !== null && (
            <Card title="Site map">
              <SiteMap lat={lat} lon={lon} alt={draft.altitude_msl ?? 0} fieldRadius={radius} disp={disp} />
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}

function SiteMap({ lat, lon, alt, fieldRadius, disp }: { lat: number; lon: number; alt: number; fieldRadius: number | null; disp: Dispersion | null }) {
  const host = useRef<HTMLDivElement>(null);
  const viewerRef = useRef<CesiumType.Viewer | null>(null);
  const cesiumRef = useRef<typeof CesiumType | null>(null);
  const [source, setSource] = useState<Source>(() => (stored("aerodyne-earth-source", "satellite") as Source) === "google3d" ? "satellite"
    : stored("aerodyne-earth-source", "satellite") as Source);
  const [ready, setReady] = useState(0);
  const [warn, setWarn] = useState<string | null>(null);
  useEffect(() => {
    if (!host.current) return;
    let viewer: CesiumType.Viewer | null = null, gone = false;
    loadCesium().then((Cesium) => {
      if (gone || !host.current) return;
      cesiumRef.current = Cesium;
      const imagery = imageryFor(Cesium, source);
      viewer = new Cesium.Viewer(host.current, {
        baseLayer: imagery ? new Cesium.ImageryLayer(imagery) : false, terrainProvider: terrainProvider(Cesium, () => undefined),
        animation: false, timeline: false, baseLayerPicker: false, geocoder: false, homeButton: false, sceneModePicker: false,
        navigationHelpButton: false, fullscreenButton: false, infoBox: false, selectionIndicator: false });
      viewerRef.current = viewer;
      viewer.scene.globe.depthTestAgainstTerrain = false;
      if (source === "relief") shadeRelief(Cesium, viewer, lon);
      imagery?.errorEvent.addEventListener(() => { shadeRelief(Cesium, viewer!, lon); setWarn("Imagery unavailable (no internet?); showing shaded terrain."); });
      setReady((r) => r + 1);
    }).catch((e) => setWarn((e as Error).message));
    return () => { gone = true; if (viewer && !viewer.isDestroyed()) viewer.destroy(); viewerRef.current = null; };
  }, [source]);
  useEffect(() => {
    const v = viewerRef.current, Cesium = cesiumRef.current;
    if (!v || !Cesium) return;
    const ds = dispersionLayer(Cesium, lat, lon, disp, fieldRadius);
    ds.entities.add({ position: Cesium.Cartesian3.fromDegrees(lon, lat), point: { pixelSize: 9, color: Cesium.Color.fromCssColorString("#ff5a4f"),
      outlineColor: Cesium.Color.WHITE, outlineWidth: 2, heightReference: Cesium.HeightReference.CLAMP_TO_GROUND, disableDepthTestDistance: Number.POSITIVE_INFINITY },
      label: { text: "PAD", font: "700 12px Inter, system-ui, sans-serif", fillColor: Cesium.Color.WHITE, outlineColor: Cesium.Color.fromCssColorString("#061018"),
        outlineWidth: 3, style: Cesium.LabelStyle.FILL_AND_OUTLINE, pixelOffset: new Cesium.Cartesian2(0, 16),
        heightReference: Cesium.HeightReference.CLAMP_TO_GROUND, disableDepthTestDistance: Number.POSITIVE_INFINITY } });
    v.dataSources.add(ds);
    const reach = Math.max(600, fieldRadius ?? 0, ...(disp?.points ?? []).map(([e, n]) => Math.hypot(e, n)));
    // aim at the ground (terrain heights are close to the site altitude above sea level)
    v.camera.flyToBoundingSphere(new Cesium.BoundingSphere(Cesium.Cartesian3.fromDegrees(lon, lat, alt), reach * 1.15),
      { offset: new Cesium.HeadingPitchRange(0, Cesium.Math.toRadians(-62), reach * 3.2), duration: 0.8 });
    return () => { if (!v.isDestroyed()) v.dataSources.remove(ds, true); };
  }, [ready, lat, lon, alt, fieldRadius, disp]);
  return (
    <div className="earth sitemap">
      <div ref={host} className="earth-canvas" />
      <div className="earth-controls">
        <select value={source} aria-label="Map source" onChange={(e) => { const s = e.target.value as Source; setSource(s); store("aerodyne-earth-source", s); }}>
          {SOURCES.filter(([k]) => k !== "google3d").map(([k, l]) => <option key={k} value={k}>{l}</option>)}
        </select>
      </div>
      {warn && <div className="earth-notes"><div className="warn">{warn}</div></div>}
      <div className="map-legend">
        <span><i className="lg-pad" />Pad</span>{fieldRadius ? <span><i className="lg-field" />Recovery field</span> : null}
        {disp?.points.length ? <span><i className="lg-pt" />Monte Carlo landings</span> : null}
        {disp?.ellipse ? <span><i className="lg-ell" />95 % ellipse</span> : null}
      </div>
    </div>
  );
}
