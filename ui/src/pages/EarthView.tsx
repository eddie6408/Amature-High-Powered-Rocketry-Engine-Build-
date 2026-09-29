/**
 * Earth 3D view for the launch simulator: the simulated flight drawn over real terrain and
 * imagery at the launch location, with a pad camera (standing near the pad, tracking the rocket
 * up), a chase camera and a free overview. CesiumJS is bundled with the app (served from
 * /cesium/); map tiles need internet unless the terrain was cached beforehand.
 */
import type * as CesiumType from "cesium";
import { useEffect, useRef, useState } from "react";

import { api, type Job } from "../api";
import type { Shape } from "../components/Profile";
import { fmt } from "../components/scale";

type C = typeof CesiumType;
type Cam = "pad" | "chase" | "overview";
type Source = "satellite" | "google3d" | "map" | "relief";

interface Frames { t: number[]; east: number[]; north: number[]; up: number[]; speed: number[]; mach: number[]; thrust: number[];
  ax_e: number[]; ax_n: number[]; ax_u: number[]; phase: number[] }
interface Res { frames: Frames; phases: string[]; events: Array<[number, string]>; length_m: number; diameter_m: number; peak_thrust: number;
  recovery: Array<{ name: string; diameter: number }>; site: { rail_length: number; elevation_deg: number; azimuth_deg: number } }
interface Pad { profile: Shape[]; length_m: number; diameter_m: number; site: { rail_length: number } }
interface Wx { rail_elevation_deg: number; launch_into_wind: boolean; rail_azimuth_deg: number; wind_from_deg: number }
export interface EarthProps { res: Res | null; pad: Pad | null; t: number; stage: string; tMinus: number; weather: Wx;
  location: { latitude: number; longitude: number; altitude_msl: number } | null }

const SOURCES: Array<[Source, string]> = [["satellite", "Satellite"], ["google3d", "Google 3D"], ["map", "Street map"], ["relief", "Terrain only (offline)"]];

/* ------------------------------------------------------------------ loading */
let loading: Promise<C> | null = null;
function loadCesium(): Promise<C> {
  const w = window as unknown as { Cesium?: C; CESIUM_BASE_URL?: string };
  if (w.Cesium) return Promise.resolve(w.Cesium);
  loading ??= new Promise<C>((resolve, reject) => {
    w.CESIUM_BASE_URL = "/cesium/";
    const css = document.createElement("link");
    css.rel = "stylesheet";
    css.href = "/cesium/Widgets/widgets.css";
    document.head.appendChild(css);
    const s = document.createElement("script");
    s.src = "/cesium/Cesium.js";
    s.onload = () => (w.Cesium ? resolve(w.Cesium) : reject(new Error("3D engine did not initialise")));
    s.onerror = () => { loading = null; reject(new Error("3D engine not found: rebuild the web app (cd ui && npm run build)")); };
    document.head.appendChild(s);
  });
  return loading;
}

function stored(key: string, fallback: string): string {
  try { return localStorage.getItem(key) ?? fallback; } catch { return fallback; }
}
function store(key: string, v: string) {
  try { localStorage.setItem(key, v); } catch { /* not remembered in private windows */ }
}

/* --------------------------------------------------- terrain (cached, via server) */
const tileCache = new Map<string, Float32Array | null>();
async function terrariumTile(z: number, x: number, y: number): Promise<Float32Array | null> {
  const key = `${z}/${x}/${y}`;
  if (tileCache.has(key)) return tileCache.get(key)!;
  let out: Float32Array | null = null;
  try {
    const r = await fetch(`/api/tiles/terrain/${key}.png`);
    if (r.ok) {
      const bmp = await createImageBitmap(await r.blob());
      const cv = new OffscreenCanvas(256, 256);
      const ctx = cv.getContext("2d")!;
      ctx.drawImage(bmp, 0, 0);
      const px = ctx.getImageData(0, 0, 256, 256).data;
      out = new Float32Array(256 * 256);
      for (let i = 0; i < out.length; i++) out[i] = px[4 * i] * 256 + px[4 * i + 1] + px[4 * i + 2] / 256 - 32768;
    }
  } catch { out = null; }
  if (tileCache.size > 400) tileCache.delete(tileCache.keys().next().value!);
  tileCache.set(key, out);
  return out;
}

const HM = 65;                                              // heightmap samples per tile edge
function terrainProvider(Cesium: C, onMissing: () => void): CesiumType.TerrainProvider {
  return new Cesium.CustomHeightmapTerrainProvider({
    width: HM, height: HM, tilingScheme: new Cesium.WebMercatorTilingScheme(),
    credit: "Terrain: Mapzen / AWS Terrain Tiles",
    callback: async (x: number, y: number, level: number) => {
      const z = Math.min(level, 15), s = 2 ** (level - z);
      const src = await terrariumTile(z, Math.floor(x / s), Math.floor(y / s));
      const out = new Float32Array(HM * HM);
      if (!src) { onMissing(); return out; }
      const span = 256 / s, ox = (x % s) * span, oy = (y % s) * span;
      for (let j = 0; j < HM; j++) {
        for (let i = 0; i < HM; i++) {
          const u = Math.min(255, Math.max(0, ox + (i / (HM - 1)) * span - 0.5));
          const v = Math.min(255, Math.max(0, oy + (j / (HM - 1)) * span - 0.5));
          const i0 = Math.floor(u), j0 = Math.floor(v), i1 = Math.min(255, i0 + 1), j1 = Math.min(255, j0 + 1);
          const fu = u - i0, fv = v - j0;
          out[j * HM + i] = (src[j0 * 256 + i0] * (1 - fu) + src[j0 * 256 + i1] * fu) * (1 - fv)
                          + (src[j1 * 256 + i0] * (1 - fu) + src[j1 * 256 + i1] * fu) * fv;
        }
      }
      return out;
    },
  });
}

/* --------------------------------------------------------------- frame helpers */
function frameAt(f: Frames, t: number) {
  const n = f.t.length, dt = f.t[1] - f.t[0];
  const i = Math.max(0, Math.min(n - 2, Math.floor(t / dt)));
  const a = Math.max(0, Math.min(1, (t - f.t[i]) / dt));
  const L = (arr: number[]) => arr[i] * (1 - a) + arr[i + 1] * a;
  return { i, e: L(f.east), n: L(f.north), u: L(f.up), speed: L(f.speed), mach: L(f.mach), thrust: L(f.thrust),
           ax: [L(f.ax_e), L(f.ax_n), L(f.ax_u)] as [number, number, number], phase: f.phase[i] };
}

/* ------------------------------------------------------------------ component */
export function EarthView(props: EarthProps) {
  const host = useRef<HTMLDivElement>(null);
  const viewerRef = useRef<CesiumType.Viewer | null>(null);
  const cesiumRef = useRef<C | null>(null);
  const live = useRef(props);
  live.current = props;
  const [source, setSource] = useState<Source>(() => stored("aerodyne-earth-source", "satellite") as Source);
  const [googleKey, setGoogleKey] = useState(() => stored("aerodyne-google-key", ""));
  const [cam, setCam] = useState<Cam>("pad");
  const camRef = useRef<Cam>("pad");
  camRef.current = cam;
  const [status, setStatus] = useState<string>("Loading the 3D engine…");
  const [warn, setWarn] = useState<string | null>(null);
  const [scaleNote, setScaleNote] = useState(1);
  const [cacheJob, setCacheJob] = useState<string | null>(null);
  const padH = useRef<number>(0);
  const loc = props.location;
  const locKey = loc ? `${loc.latitude.toFixed(6)},${loc.longitude.toFixed(6)}` : "";

  // viewer lifetime: per location and map source
  useEffect(() => {
    if (!loc || !host.current) return;
    if (source === "google3d" && !googleKey) { setStatus(""); return; }
    let viewer: CesiumType.Viewer | null = null;
    let disposed = false;
    let missingTerrain = false;
    setWarn(null);
    setStatus("Loading the 3D engine…");
    loadCesium().then(async (Cesium) => {
      if (disposed || !host.current) return;
      cesiumRef.current = Cesium;
      let imagery: CesiumType.ImageryProvider | null = null;
      if (source === "satellite") {
        imagery = new Cesium.UrlTemplateImageryProvider({
          url: "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
          maximumLevel: 19, credit: "Imagery: Esri, Maxar, Earthstar Geographics, and the GIS User Community" });
      } else if (source === "map") {
        imagery = new Cesium.UrlTemplateImageryProvider({ url: "https://tile.openstreetmap.org/{z}/{x}/{y}.png", maximumLevel: 19,
          credit: "© OpenStreetMap contributors" });
      }
      const terrain = source === "google3d" ? new Cesium.EllipsoidTerrainProvider()
        : terrainProvider(Cesium, () => { if (!missingTerrain) { missingTerrain = true; setWarn("Some terrain tiles are not cached and can't be downloaded (no internet?). The ground is drawn flat there."); } });
      viewer = new Cesium.Viewer(host.current, {
        baseLayer: imagery ? new Cesium.ImageryLayer(imagery) : false,
        terrainProvider: terrain,
        animation: false, timeline: false, baseLayerPicker: false, geocoder: false, homeButton: false, sceneModePicker: false,
        navigationHelpButton: false, fullscreenButton: false, infoBox: false, selectionIndicator: false,
      });
      viewerRef.current = viewer;
      const scene = viewer.scene;
      scene.globe.baseColor = Cesium.Color.fromCssColorString("#2c3b2a");
      const shadeRelief = () => {                              // sun-shaded relief, mid-morning sun at the site
        const d = new Date();
        const h = 10 - loc.longitude / 15;
        d.setUTCHours(Math.floor(h), Math.round((h % 1) * 60), 0, 0);
        viewer!.clock.currentTime = Cesium.JulianDate.fromDate(d);
        viewer!.clock.shouldAnimate = false;
        scene.globe.enableLighting = true;
        scene.globe.baseColor = Cesium.Color.fromCssColorString("#7c8f5a");
      };
      if (source === "relief") shadeRelief();
      scene.globe.depthTestAgainstTerrain = true;
      scene.fog.enabled = true;
      if (scene.skyAtmosphere) scene.skyAtmosphere.show = true;
      imagery?.errorEvent.addEventListener(() => shadeRelief());
      imagery?.errorEvent.addEventListener(() => setWarn("Map imagery can't be loaded (no internet?). Terrain and the flight are still shown; "
        + "cache the terrain beforehand for the field."));
      if (source === "google3d") {
        try {
          Cesium.GoogleMaps.defaultApiKey = googleKey;
          const tiles = await Cesium.createGooglePhotorealistic3DTileset();
          if (disposed) return;
          scene.primitives.add(tiles);
          scene.globe.show = false;
          tiles.tileFailed.addEventListener(() => setWarn("Google 3D tiles failed to load: check the API key (Map Tiles API enabled) and internet."));
        } catch (e) {
          setWarn(`Google 3D tiles unavailable: ${(e as Error).message}`);
        }
      }
      // pad height on the ground model
      const carto = Cesium.Cartographic.fromDegrees(loc.longitude, loc.latitude);
      padH.current = source === "google3d" ? loc.altitude_msl : 0;
      if (source !== "google3d") {
        Cesium.sampleTerrain(terrain, 14, [carto]).then((r) => { padH.current = r[0].height ?? 0; }).catch(() => undefined);
      } else {
        setTimeout(() => {
          scene.sampleHeightMostDetailed([carto]).then((r) => { if (r[0]?.height !== undefined) padH.current = r[0].height; }).catch(() => undefined);
        }, 4000);
      }
      buildEntities(Cesium, viewer, live, padH, camRef, setScaleNote);
      setStatus("");
    }).catch((e) => setStatus((e as Error).message));
    return () => {
      disposed = true;
      if (viewer && !viewer.isDestroyed()) viewer.destroy();
      viewerRef.current = null;
    };
  }, [locKey, source, googleKey]);

  // camera input: free orbit only in the overview
  useEffect(() => {
    const v = viewerRef.current, Cesium = cesiumRef.current;
    if (!v || !Cesium) return;
    v.scene.screenSpaceCameraController.enableInputs = cam === "overview";
    if (cam === "overview") flyOverview(Cesium, v, live.current, padH.current);
  }, [cam, status]);

  const cacheTerrain = () => {
    if (!loc) return;
    api.post<{ job: string }>("/api/tiles/terrain/prefetch", { lat: loc.latitude, lon: loc.longitude, radius_km: 4, max_zoom: 14 })
      .then(({ job }) => {
        setCacheJob("Caching terrain…");
        const poll = () => api.get<Job & { summary?: { tiles: number; fetched: number; already_cached: number; failed: number } }>(`/api/jobs/${job}`).then((j) => {
          if (j.status === "running") { setCacheJob(`Caching terrain… ${j.done}/${j.total} tiles`); setTimeout(poll, 800); }
          else if (j.status === "error") setCacheJob(`Terrain cache failed: ${j.error}`);
          else setCacheJob(`Terrain cached for offline use: ${j.summary?.tiles ?? 0} tiles (${j.summary?.fetched ?? 0} new, ${j.summary?.failed ?? 0} failed).`);
        });
        poll();
      }).catch((e) => setCacheJob((e as Error).message));
  };

  if (!loc) {
    return <div className="earth empty-earth"><p>Set the launch location (My location, or latitude and longitude) to see the flight over the real terrain.</p></div>;
  }
  const f = props.res && props.stage !== "setup" && props.stage !== "armed" && props.stage !== "countdown" && props.stage !== "hold"
    ? frameAt(props.res.frames, props.t) : null;
  return (
    <div className="earth">
      <div ref={host} className="earth-canvas" />
      {status && <div className="earth-status">{status}</div>}
      {source === "google3d" && !googleKey && (
        <div className="earth-status">
          <GoogleKeyForm onSave={(k) => { setGoogleKey(k); store("aerodyne-google-key", k); }} />
        </div>
      )}
      <div className="earth-controls">
        <div className="seg" role="group" aria-label="Camera">
          {([["pad", "Pad camera"], ["chase", "Chase"], ["overview", "Overview"]] as Array<[Cam, string]>).map(([k, l]) => (
            <button key={k} className={cam === k ? "on" : ""} onClick={() => setCam(k)}>{l}</button>
          ))}
        </div>
        <select value={source} aria-label="Map source" onChange={(e) => { const s = e.target.value as Source; setSource(s); store("aerodyne-earth-source", s); }}>
          {SOURCES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
        </select>
        {source === "google3d" && googleKey && <button onClick={() => { setGoogleKey(""); store("aerodyne-google-key", ""); }}>Change key</button>}
        {source !== "google3d" && <button onClick={cacheTerrain} title="Download the terrain around this site so the Earth view works at the field without internet">Cache terrain</button>}
      </div>
      <div className="earth-hud">
        <span className="big">{f ? `T+${props.t.toFixed(1)} s` : props.stage === "countdown" || props.stage === "hold" ? `T-${props.tMinus}` : "ON PAD"}</span>
        <span><small>ALT</small>{fmt(f?.u ?? 0)} m</span>
        <span><small>SPD</small>{fmt(f?.speed ?? 0)} m/s</span>
        <span><small>MACH</small>{fmt(f?.mach ?? 0, 2)}</span>
        {props.res && f && <span className="phase">{props.res.phases[f.phase]}</span>}
      </div>
      {(warn || cacheJob || scaleNote > 1.5) && (
        <div className="earth-notes">
          {warn && <div className="warn">{warn}</div>}
          {cacheJob && <div>{cacheJob}</div>}
          {scaleNote > 1.5 && <div>Rocket and parachutes drawn ×{fmt(scaleNote)} so they stay visible at this distance · SIMULATED</div>}
        </div>
      )}
    </div>
  );
}

function GoogleKeyForm({ onSave }: { onSave: (k: string) => void }) {
  const [k, setK] = useState("");
  return (
    <form className="key-form" onSubmit={(e) => { e.preventDefault(); if (k.trim()) onSave(k.trim()); }}>
      <strong>Google Photorealistic 3D Tiles</strong>
      <p>Google's 3D Earth needs your own Google Maps Platform API key with the <em>Map Tiles API</em> enabled
        (Google Cloud console; it has a free monthly allowance). The key stays in this browser only.</p>
      <input value={k} onChange={(e) => setK(e.target.value)} placeholder="API key" aria-label="Google Maps API key" />
      <button className="primary" type="submit">Use key</button>
    </form>
  );
}

/* ---------------------------------------------------------------- the scene */
function flyOverview(Cesium: C, viewer: CesiumType.Viewer, p: EarthProps, padHeight: number) {
  if (!p.location) return;
  const M = Cesium.Transforms.eastNorthUpToFixedFrame(Cesium.Cartesian3.fromDegrees(p.location.longitude, p.location.latitude, padHeight));
  const pts: CesiumType.Cartesian3[] = [Cesium.Matrix4.multiplyByPoint(M, new Cesium.Cartesian3(0, 0, 0), new Cesium.Cartesian3())];
  if (p.res) {
    const f = p.res.frames;
    for (let i = 0; i < f.t.length; i += 10) pts.push(Cesium.Matrix4.multiplyByPoint(M, new Cesium.Cartesian3(f.east[i], f.north[i], f.up[i]), new Cesium.Cartesian3()));
  } else {
    pts.push(Cesium.Matrix4.multiplyByPoint(M, new Cesium.Cartesian3(0, 0, 300), new Cesium.Cartesian3()));
  }
  const bs = Cesium.BoundingSphere.fromPoints(pts);
  viewer.camera.flyToBoundingSphere(bs, { offset: new Cesium.HeadingPitchRange(Cesium.Math.toRadians(-30), Cesium.Math.toRadians(-22), Math.max(600, bs.radius * 3.2)), duration: 1.2 });
}

function buildEntities(Cesium: C, viewer: CesiumType.Viewer, live: { current: EarthProps }, padH: { current: number },
                       camRef: { current: Cam }, setScaleNote: (s: number) => void) {
  const { Cartesian3, Matrix4, Matrix3, Quaternion, Color } = Cesium;
  const scratch = new Cartesian3();
  const enu = () => {
    const l = live.current.location!;
    return Cesium.Transforms.eastNorthUpToFixedFrame(Cartesian3.fromDegrees(l.longitude, l.latitude, padH.current));
  };
  const at = (M: CesiumType.Matrix4, e: number, n: number, u: number) => Matrix4.multiplyByPoint(M, new Cartesian3(e, n, u), new Cartesian3());
  const inFlight = () => {
    const p = live.current;
    return !!p.res && !["setup", "armed", "countdown", "hold", "failed"].includes(p.stage);
  };
  const railDir = (): [number, number, number] => {
    const p = live.current;
    const el = ((p.res?.site.elevation_deg ?? p.weather.rail_elevation_deg) * Math.PI) / 180;
    const az = ((p.res?.site.azimuth_deg ?? (p.weather.launch_into_wind ? p.weather.wind_from_deg : p.weather.rail_azimuth_deg)) * Math.PI) / 180;
    return [Math.cos(el) * Math.sin(az), Math.cos(el) * Math.cos(az), Math.sin(el)];
  };
  const deployedNames = () => {
    const p = live.current;
    return inFlight() ? p.res!.events.filter(([te, e]) => e.startsWith("deploy:") && te <= p.t).map(([, e]) => e.slice(7)) : [];
  };
  // current state in ENU: aft end position and body axis
  const state = () => {
    const p = live.current;
    const len = p.res?.length_m ?? p.pad?.length_m ?? 1.25;
    if (!inFlight()) {
      const d = railDir();
      return { aft: [d[0] * 0.05, d[1] * 0.05, 0.25] as [number, number, number], axis: d, len, thrust: 0, t: 0 };
    }
    const f = frameAt(p.res!.frames, p.t);
    let axis = f.ax;
    if (deployedNames().length) axis = [0, 0, 1];
    const nrm = Math.hypot(...axis) || 1;
    axis = [axis[0] / nrm, axis[1] / nrm, axis[2] / nrm];
    // the simulator tracks the CG; place the aft end a little behind it
    const back = len * 0.55;
    return { aft: [f.e - axis[0] * back, f.n - axis[1] * back, f.u - axis[2] * back] as [number, number, number], axis,
             len, thrust: f.thrust / (p.res!.peak_thrust || 1), t: p.t };
  };
  // visual scale so the rocket stays visible from far away
  let vis = 1;
  const orient = (axisENU: [number, number, number]) => {
    const M = enu();
    const a = Matrix3.multiplyByVector(Matrix4.getMatrix3(M, new Matrix3()), new Cartesian3(...axisENU), new Cartesian3());
    Cartesian3.normalize(a, a);
    const z = Cartesian3.UNIT_Z;
    const cross = Cartesian3.cross(z, a, new Cartesian3());
    const ang = Math.acos(Math.max(-1, Math.min(1, Cartesian3.dot(z, a))));
    if (Cartesian3.magnitude(cross) < 1e-9) return Quaternion.IDENTITY;
    return Quaternion.fromAxisAngle(Cartesian3.normalize(cross, cross), ang);
  };
  const along = (s: ReturnType<typeof state>, d: number): CesiumType.Cartesian3 =>
    at(enu(), s.aft[0] + s.axis[0] * d, s.aft[1] + s.axis[1] * d, s.aft[2] + s.axis[2] * d);
  const E = viewer.entities;
  const dia = () => live.current.res?.diameter_m ?? live.current.pad?.diameter_m ?? 0.066;

  // rocket body + nose
  E.add({
    position: new Cesium.CallbackPositionProperty(() => { const s = state(); return along(s, s.len * 0.4 * vis); }, false),
    orientation: new Cesium.CallbackProperty(() => orient(state().axis), false),
    cylinder: { length: new Cesium.CallbackProperty(() => state().len * 0.8 * vis, false),
                topRadius: new Cesium.CallbackProperty(() => (dia() / 2) * vis, false),
                bottomRadius: new Cesium.CallbackProperty(() => (dia() / 2) * vis, false),
                material: Color.fromCssColorString("#f2f5f7"), slices: 24 },
  });
  E.add({
    position: new Cesium.CallbackPositionProperty(() => { const s = state(); return along(s, s.len * 0.9 * vis); }, false),
    orientation: new Cesium.CallbackProperty(() => orient(state().axis), false),
    cylinder: { length: new Cesium.CallbackProperty(() => state().len * 0.2 * vis, false), topRadius: 0,
                bottomRadius: new Cesium.CallbackProperty(() => (dia() / 2) * vis, false),
                material: Color.fromCssColorString("#19c3a6"), slices: 24 },
  });
  // fins: two thin crossed plates at the aft end
  for (const rot of [0, Math.PI / 2]) {
    E.add({
      position: new Cesium.CallbackPositionProperty(() => { const s = state(); return along(s, s.len * 0.08 * vis); }, false),
      orientation: new Cesium.CallbackProperty(() => {
        const q = orient(state().axis);
        return Quaternion.multiply(q, Quaternion.fromAxisAngle(Cartesian3.UNIT_Z, rot), new Quaternion());
      }, false),
      box: { dimensions: new Cesium.CallbackProperty(() => new Cartesian3(dia() * 3.2 * vis, dia() * 0.12 * vis, state().len * 0.16 * vis), false),
             material: Color.fromCssColorString("#1e2a33") },
    });
  }
  // flame
  E.add({
    position: new Cesium.CallbackPositionProperty(() => { const s = state(); return along(s, -s.len * 0.12 * vis); }, false),
    point: { pixelSize: new Cesium.CallbackProperty(() => 8 + 22 * state().thrust, false),
             color: Color.fromCssColorString("#ffd35a").withAlpha(0.95), outlineColor: Color.fromCssColorString("#ff6a1f").withAlpha(0.8),
             outlineWidth: 5, show: new Cesium.CallbackProperty(() => inFlight() && state().thrust > 0.01, false) },
  });
  // always-visible marker and label for the rocket
  E.add({
    position: new Cesium.CallbackPositionProperty(() => { const s = state(); return along(s, s.len * 0.5 * vis); }, false),
    label: { text: new Cesium.CallbackProperty(() => {
               const p = live.current;
               if (!inFlight()) return "ON PAD";
               const f = frameAt(p.res!.frames, p.t);
               const recent = p.res!.events.filter(([te]) => te <= p.t && p.t - te < 2.5).map(([, e]) => e.replace("deploy:", "").replace("_", " ").toUpperCase());
               return `${fmt(f.u)} m${recent.length ? "  ·  " + recent.join(" · ") : ""}`;
             }, false),
             font: "600 14px Inter, system-ui, sans-serif", fillColor: Color.WHITE, outlineColor: Color.fromCssColorString("#061018"), outlineWidth: 4,
             style: Cesium.LabelStyle.FILL_AND_OUTLINE, pixelOffset: new Cesium.Cartesian2(18, -18),
             horizontalOrigin: Cesium.HorizontalOrigin.LEFT, disableDepthTestDistance: Number.POSITIVE_INFINITY },
  });
  // parachutes
  for (let k = 0; k < 2; k++) {
    E.add({
      position: new Cesium.CallbackPositionProperty(() => {
        const s = state();
        const names = deployedNames();
        const r = (live.current.res?.recovery.find((d) => d.name === names[k])?.diameter ?? 0.6) / 2;
        return along(s, s.len * vis + (1.2 + k * 1.6) * r * vis + r * vis * 1.4);
      }, false),
      ellipsoid: { radii: new Cesium.CallbackProperty(() => {
                     const names = deployedNames();
                     const r = (live.current.res?.recovery.find((d) => d.name === names[k])?.diameter ?? 0.6) / 2 * vis;
                     return new Cartesian3(r, r, r * 0.75);
                   }, false),
                   maximumCone: Math.PI / 2, material: Color.fromCssColorString(k === 0 ? "#ff5a4f" : "#ffb020"),
                   show: new Cesium.CallbackProperty(() => deployedNames().length > k, false) },
    });
  }
  // rail and pad
  E.add({
    polyline: { positions: new Cesium.CallbackProperty(() => {
                  const M = enu(), d = railDir(), L = live.current.res?.site.rail_length ?? live.current.pad?.site.rail_length ?? 2;
                  return [at(M, 0, 0, 0), at(M, d[0] * L, d[1] * L, d[2] * L)];
                }, false), width: 3, material: Color.fromCssColorString("#c9d1d6") },
  });
  E.add({
    position: new Cesium.CallbackPositionProperty(() => at(enu(), 0, 0, 0), false),
    point: { pixelSize: 7, color: Color.fromCssColorString("#ff5a4f"), outlineColor: Color.WHITE, outlineWidth: 2,
             disableDepthTestDistance: Number.POSITIVE_INFINITY },
    label: { text: "PAD", font: "700 12px Inter, system-ui, sans-serif", fillColor: Color.WHITE, outlineColor: Color.fromCssColorString("#061018"),
             outlineWidth: 3, style: Cesium.LabelStyle.FILL_AND_OUTLINE, pixelOffset: new Cesium.Cartesian2(0, 16),
             disableDepthTestDistance: Number.POSITIVE_INFINITY },
  });
  // predicted path (dashed) and flown smoke trail
  const path = (upto: (t: number) => boolean) => new Cesium.CallbackProperty(() => {
    const p = live.current;
    if (!p.res) return [];
    const f = p.res.frames, M = enu(), out: CesiumType.Cartesian3[] = [];
    for (let i = 0; i < f.t.length; i += 3) { if (!upto(f.t[i])) break; out.push(at(M, f.east[i], f.north[i], f.up[i])); }
    return out;
  }, false);
  E.add({ polyline: { positions: path(() => true), width: 2,
                      material: new Cesium.PolylineDashMaterialProperty({ color: Color.fromCssColorString("#2ee6c0").withAlpha(0.8), dashLength: 18 }) } });
  E.add({ polyline: { positions: path((tt) => inFlight() && tt <= live.current.t), width: 7,
                      material: new Cesium.PolylineGlowMaterialProperty({ color: Color.WHITE.withAlpha(0.75), glowPower: 0.25 }) } });
  // predicted landing point
  E.add({
    position: new Cesium.CallbackPositionProperty(() => {
      const f = live.current.res?.frames;
      return f ? at(enu(), f.east[f.east.length - 1], f.north[f.north.length - 1], 0) : at(enu(), 0, 0, -1e4);
    }, false),
    point: { pixelSize: 9, color: Color.fromCssColorString("#ffdd57"), outlineColor: Color.fromCssColorString("#061018"), outlineWidth: 2,
             disableDepthTestDistance: Number.POSITIVE_INFINITY, show: new Cesium.CallbackProperty(() => !!live.current.res, false) },
    label: { text: "Predicted landing", font: "600 12px Inter, system-ui, sans-serif", fillColor: Color.fromCssColorString("#ffdd57"),
             outlineColor: Color.fromCssColorString("#061018"), outlineWidth: 3, style: Cesium.LabelStyle.FILL_AND_OUTLINE,
             pixelOffset: new Cesium.Cartesian2(0, -16), disableDepthTestDistance: Number.POSITIVE_INFINITY,
             show: new Cesium.CallbackProperty(() => !!live.current.res, false) },
  });

  // cameras (pad and chase are driven every frame; overview is free)
  let lastNote = 1;
  viewer.scene.preRender.addEventListener(() => {
    const s = state();
    const M = enu();
    const mid = along(s, s.len * 0.5 * vis);
    const camPos = viewer.camera.positionWC;
    const dist = Cartesian3.distance(camPos, mid);
    vis = Math.max(1, dist / (s.len * 45));
    if (Math.abs(vis - lastNote) / lastNote > 0.15) { lastNote = vis; setScaleNote(vis); }
    const mode = camRef.current;
    if (mode === "overview") return;
    const upAt = (pt: CesiumType.Cartesian3) => Cesium.Ellipsoid.WGS84.geodeticSurfaceNormal(pt, new Cartesian3());
    let eye: CesiumType.Cartesian3;
    if (mode === "pad") {
      // an observer ~90 m from the pad, across the wind, eye height above the pad
      const wf = (live.current.weather.wind_from_deg * Math.PI) / 180 + Math.PI / 2;
      eye = at(M, Math.sin(wf) * 90, Math.cos(wf) * 90, 2.5);
    } else {
      const d = Math.max(12, s.len * vis * 7);
      const sideE = -s.axis[1], sideN = s.axis[0];
      const h = Math.hypot(sideE, sideN) || 1;
      const c = Matrix4.multiplyByPointAsVector(M, new Cartesian3(-s.axis[0] * d + (sideE / h) * d * 0.5, -s.axis[1] * d + (sideN / h) * d * 0.5,
                                                                   -s.axis[2] * d * 0.6 + d * 0.35), scratch);
      eye = Cartesian3.add(mid, c, new Cartesian3());
    }
    const dir = Cartesian3.normalize(Cartesian3.subtract(mid, eye, new Cartesian3()), new Cartesian3());
    const upv = upAt(eye);
    const right = Cartesian3.normalize(Cartesian3.cross(dir, upv, new Cartesian3()), new Cartesian3());
    const up = Cartesian3.cross(right, dir, new Cartesian3());
    viewer.camera.setView({ destination: eye, orientation: { direction: dir, up } });
    const fr = viewer.camera.frustum as CesiumType.PerspectiveFrustum;
    const range = Cartesian3.distance(eye, mid);
    const alt = inFlight() ? frameAt(live.current.res!.frames, live.current.t).u : 0;
    const want = mode === "pad" ? Math.max(18, 0.45 * alt + 14) : Math.max(8, s.len * vis * 5);
    fr.fov = Math.min(Cesium.Math.toRadians(70), Math.max(Cesium.Math.toRadians(4), 2 * Math.atan(want / range)));
  });
}
