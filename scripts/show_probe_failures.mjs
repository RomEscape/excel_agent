// 실패한 명령의 원인만 뽑아 본다.
// 사용: node scripts/show_probe_failures.mjs [입력 json] [출력 txt]
//      (기본: <reports>/probe_live_app.json → <reports>/probe_failures.txt)
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";

// 산출물 폴더 — 파이썬(office_claw_sidecar.config.get_reports_dir)과 같은 규칙:
// 환경변수 OFFICE_CLAW_REPORTS_DIR, 없으면 <앱 데이터 폴더>/office_claw/reports.
// 저장소 logs/ 에는 chat_log.jsonl 하나만 남긴다(2026-09-10).
function reportsDir() {
  const home = os.homedir();
  const base =
    process.platform === "win32"
      ? process.env.LOCALAPPDATA || path.join(home, "AppData", "Local")
      : process.platform === "darwin"
        ? path.join(home, "Library", "Application Support")
        : path.join(home, ".local", "share");
  const dir = process.env.OFFICE_CLAW_REPORTS_DIR || path.join(base, "office_claw", "reports");
  mkdirSync(dir, { recursive: true });
  return dir;
}

const inPath = process.argv[2] || path.join(reportsDir(), "probe_live_app.json");
const outPath = process.argv[3] || path.join(reportsDir(), "probe_failures.txt");
const report = JSON.parse(readFileSync(inPath, "utf-8"));
const lines = [];
for (const row of report) {
  const res = row.response || {};
  if (res.ok !== false) continue;
  const r = res.result || {};
  lines.push(
    [
      `명령: ${row.message}`,
      `  액션: ${res.action}`,
      `  실패단계: ${r.failed_action} (#${r.failed_step_index})`,
      `  원인: ${String(r.failure_detail || res.reason || "").slice(0, 400)}`,
      `  계획: ${JSON.stringify(r.planned_steps || [])}`,
    ].join("\n"),
  );
}
writeFileSync(outPath, lines.join("\n\n") + "\n", "utf-8");
console.log(`${lines.length} failures → ${outPath}`);
