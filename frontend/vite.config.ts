/// <reference types="vitest/config" />
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const api = `http://127.0.0.1:${env.CONCORD_PORT || process.env.CONCORD_PORT || 8620}`;
  return {
    plugins: [react(), tailwindcss()],
    // Concord Label: Vite on 5174 (Concord Bench uses 5173), API on 8620 (Bench: 8600).
    server: { port: 5174, proxy: { "/api": { target: api, changeOrigin: false } } },
    preview: { proxy: { "/api": { target: api } } },
    build: { outDir: "dist", sourcemap: true, chunkSizeWarningLimit: 900 },
    test: { environment: "jsdom", include: ["src/**/*.test.{ts,tsx}"], globals: false },
  };
});
