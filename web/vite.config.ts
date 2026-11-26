import { fileURLToPath, URL } from "node:url"
import tailwindcss from "@tailwindcss/vite"
import react from "@vitejs/plugin-react"
import { defineConfig } from "vite"

export default defineConfig(({ command }) => ({
  base: command === "build" ? "/app-assets/" : "/",
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  build: {
    outDir: fileURLToPath(new URL("../src/switchboard/static/app", import.meta.url)),
    emptyOutDir: true,
    assetsDir: "assets",
  },
  server: {
    proxy: {
      "^/(?:api|classic|claim|routines|pairing|visibility|connect|avatars|assets|healthz|slack/(?:requests|personas)|inbox/(?:poll|events))(?:/|\\?|$)":
        "http://127.0.0.1:8000",
    },
  },
}))
