import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { fileURLToPath } from "node:url";

// The FastAPI backend (main.py) serves /api and /ws/dashboard on :8000. In dev we proxy both so the
// browser talks to one origin — no CORS, and the WebSocket URL is simply `/ws/dashboard`.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const target = env.VITE_BACKEND_URL || "http://127.0.0.1:8000";
  return {
    plugins: [react(), tailwindcss()],
    resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
    server: {
      port: 5173,
      proxy: {
        "/api": { target, changeOrigin: true },
        "/ping": { target, changeOrigin: true },
        "/ws": { target: target.replace(/^http/, "ws"), ws: true, changeOrigin: true },
      },
    },
    worker: { format: "es" },
    build: { chunkSizeWarningLimit: 1200 },
  };
});
