import { cpSync, existsSync, readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import react from "@vitejs/plugin-react";
import { defineConfig, type Plugin } from "vite";

const here = fileURLToPath(new URL(".", import.meta.url));

/** Ship CesiumJS (3D globe for the launch simulator's Earth view) as static files under /cesium/,
 *  so the app works without a CDN; only the map tiles need internet. */
function cesiumAssets(): Plugin {
  return {
    name: "cesium-assets",
    buildStart() {
      const src = `${here}node_modules/cesium/Build/Cesium`;
      const dst = `${here}public/cesium`;
      const version = JSON.parse(readFileSync(`${here}node_modules/cesium/package.json`, "utf8")).version as string;
      const stamp = `${dst}/VERSION`;
      if (existsSync(stamp) && readFileSync(stamp, "utf8") === version) return;
      cpSync(src, dst, { recursive: true });
      writeFileSync(stamp, version);
    },
  };
}

// In development the Python server (aerodyne app) provides /api on :8765.
export default defineConfig({
  plugins: [react(), cesiumAssets()],
  server: { proxy: { "/api": "http://127.0.0.1:8765" } },
  test: { environment: "node" },
});
