import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In development the Python server (aerodyne ground) provides /api on :8765.
export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://127.0.0.1:8765" } },
  test: { environment: "node" },
});
