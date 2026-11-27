import { readFileSync } from "node:fs"
import { fileURLToPath, URL } from "node:url"
import tailwindcss from "@tailwindcss/vite"
import react from "@vitejs/plugin-react"
import { defineConfig } from "vite"
import { PRODUCT_NAME, TAGLINE } from "./src/app/brand.ts"

const theme = readFileSync(new URL("../src/switchboard/static/theme.js", import.meta.url), "utf8")

export default defineConfig(({ command }) => ({
  base: command === "build" ? "/app-assets/" : "/",
  plugins: [react(), tailwindcss(), {
    name: "swb-brand",
    transformIndexHtml() {
      return [
        { tag: "meta", attrs: { name: "description", content: TAGLINE.en } },
        { tag: "meta", attrs: { property: "og:title", content: PRODUCT_NAME } },
        { tag: "meta", attrs: { property: "og:description", content: TAGLINE.en } },
      ]
    },
  }, {
    name: "swb-theme",
    configureServer(server) {
      server.middlewares.use("/assets/theme.js", (_request, response) => {
        response.setHeader("Content-Type", "text/javascript; charset=utf-8")
        response.end(theme)
      })
    },
    generateBundle() {
      this.emitFile({ type: "asset", fileName: "assets/theme.js", source: theme })
    },
  }],
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  build: {
    outDir: fileURLToPath(new URL("../src/switchboard/static/app", import.meta.url)),
    emptyOutDir: true,
    assetsDir: "assets",
  },
  server: {
    proxy: {
      "^/(?:api|auth|classic|claim|routines/claim|pairing/code|connect|avatars|assets(?!/theme\\.js(?:\\?|$))|healthz|slack/(?:requests|personas)|inbox/(?:poll|events))(?:/|\\?|$)":
        "http://127.0.0.1:8000",
    },
  },
}))
