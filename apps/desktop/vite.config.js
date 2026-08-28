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
    // 소스맵은 배포본에 절대 싣지 않는다 — 원본 JSX가 통째로 복원된다.
    // (기본값도 false지만, 디버깅하다 켜놓고 릴리스하는 사고를 막으려 명시한다.)
    sourcemap: false,
    rollupOptions: {
      output: {
        // 기본 청크 이름은 `ActivityPage-<hash>.js`처럼 컴포넌트 이름을 그대로
        // 드러내서, 번들을 열어보지 않고 파일 목록만으로 화면 구성이 읽힌다.
        // 해시만 남긴다 — 진입점(index)과 CSS/이미지 이름은 그대로 둔다.
        chunkFileNames: "assets/[hash].js",
      },
    },
  },
  // Prevent Vite from obscuring Rust/sidecar errors in the console
  clearScreen: false,
});
