import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const target = process.env.API_URL ?? "http://localhost:8095";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": target,
      "/ws": { target: target.replace("http", "ws"), ws: true },
    },
  },
  test: { environment: "node" },
});
