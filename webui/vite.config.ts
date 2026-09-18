import path from "path"
import tailwindcss from "@tailwindcss/vite"
import react from "@vitejs/plugin-react"
import { defineConfig } from "vite"

// Relative asset URLs so the bundle works when nginx serves it from any path.
export default defineConfig({
  base: "./",
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(import.meta.dirname, "./src"),
    },
  },
  build: {
    // Emit into the Flux kustomize base. Flat deterministic names keep the
    // ConfigMap keys stable across rebuilds (same contract as chacdn).
    outDir: path.resolve(import.meta.dirname, "../base/www"),
    emptyOutDir: true,
    rollupOptions: {
      output: {
        entryFileNames: "index.js",
        assetFileNames: "index.css",
        // Keep the SPA small; it is shipped as a ConfigMap.
        manualChunks: undefined,
      },
    },
  },
})
