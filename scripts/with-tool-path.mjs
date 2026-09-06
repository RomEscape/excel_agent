/**
 * 셋업이 설치한 도구(rustup 의 cargo, uv, Ollama)가 **이 셸의 PATH 에는 아직 없을 때**를 메운다.
 *
 *   node scripts/with-tool-path.mjs <명령> [인자...]
 *
 * 왜: setup.ps1 이 Rust 를 깔고 사용자 PATH(레지스트리)에 ~\.cargo\bin 을 넣어도, 그 전에 열린
 * 셸 — 특히 VS Code·Cursor 통합 터미널은 **에디터 프로세스의 환경을 물려받아** 새 탭을 열어도
 * 그대로다 — 에서는 `npm run tauri:dev` 가 계속 `cargo metadata … program not found` 로 죽는다
 * (2026-09-06 실측: 셋업 완주 뒤에도 사용자 터미널에서 두 번 재현, VSCODE_PID 존재).
 * 에디터를 재시작하면 풀리지만, 그걸 모르는 사람이 "왜 아직도 안 돼" 로 막히지 않게
 * 알려진 설치 위치를 PATH 앞에 붙이고 나서 명령을 실행한다. 이미 있으면 아무것도 안 한다.
 */
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const [cmd, ...args] = process.argv.slice(2);
if (!cmd) {
  console.error("사용법: node scripts/with-tool-path.mjs <명령> [인자...]");
  process.exit(2);
}

const home = os.homedir();
const localAppData = process.env.LOCALAPPDATA || path.join(home, "AppData", "Local");
const candidates = [
  path.join(home, ".cargo", "bin"), // rustup (Windows·macOS·Linux 공통)
  path.join(home, ".local", "bin"), // uv (astral 설치기 기본 위치)
  path.join(localAppData, "Programs", "Ollama"), // Ollama Windows 설치기
  "/opt/homebrew/bin", // macOS Apple Silicon brew (ollama·uv)
];

// Windows 는 환경변수 이름이 `Path` 로 들어올 수 있다 — 같은 키를 덮어써야 두 벌이 안 생긴다.
const pathKey = Object.keys(process.env).find((k) => k.toUpperCase() === "PATH") || "PATH";
const parts = String(process.env[pathKey] || "").split(path.delimiter).filter(Boolean);
const lowered = new Set(parts.map((p) => p.toLowerCase()));
const added = [];
for (const dir of candidates) {
  if (fs.existsSync(dir) && !lowered.has(dir.toLowerCase())) {
    parts.unshift(dir);
    added.push(dir);
  }
}
if (added.length) {
  console.error(`[with-tool-path] PATH 앞에 추가: ${added.join(", ")} (새 터미널/에디터 재시작이면 필요 없음)`);
}

const env = { ...process.env, [pathKey]: parts.join(path.delimiter) };
const result = spawnSync(cmd, args, { stdio: "inherit", shell: true, env });
if (result.error) {
  console.error(`[with-tool-path] 실행 실패: ${result.error.message}`);
  process.exit(1);
}
process.exit(result.status ?? 1);
