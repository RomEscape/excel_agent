import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  // Tauri dev server configuration
  server: {
    port: 1420,
    strictPort: true,
    // Do not open the browser — Tauri opens its own webview
    open: false,
    watch: {
      // On Windows, watching files inside node_modules can be expensive
      ignored: ["**/node_modules/**", "**/src-tauri/**"],
    },
  },
  // Produce a flat dist/ bundle for Tauri to serve
  build: {
    outDir: "dist",
    emptyOutDir: true,
    // Inline small assets to reduce file count
    assetsInlineLimit: 4096,
  },
  // Prevent Vite from obscuring Rust/sidecar errors in the console
  clearScreen: false,
});
