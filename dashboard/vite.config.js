import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const publicHost = process.env.RAILWAY_PUBLIC_DOMAIN;

export default defineConfig({
  plugins: [react()],
  server: {
    host: "0.0.0.0",
    port: 8080,
    strictPort: true,
    allowedHosts: ["localhost", "127.0.0.1", ...(publicHost ? [publicHost] : [])],
    hmr: publicHost
      ? { protocol: "wss", host: publicHost, clientPort: 443 }
      : undefined,
    proxy: {
      "/v1": {
        target: process.env.O2A_API_TARGET || "http://127.0.0.1:8081",
        changeOrigin: true,
      },
    },
  },
});
