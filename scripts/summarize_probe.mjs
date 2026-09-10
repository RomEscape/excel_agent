// probe-live-app.mjs 결과를 한 줄 요약으로 뽑는다.
// 사용: node scripts/summarize_probe.mjs [입력 json] [출력 txt]
//      (기본: <reports>/probe_live_app.json → <reports>/probe_summary.txt)
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
const outPath = process.argv[3] || path.join(reportsDir(), "probe_summary.txt");
const report = JSON.parse(readFileSync(inPath, "utf-8"));
const lines = [];
for (const row of report) {
  const res = row.response || {};
  const steps = res.executed_steps || res.steps || [];
  const actions = steps.map((s) => (s.action || "").replace("excel_live.", "")).join(" > ");
  const detail = steps
    .map((s) => {
      const r = s.result || {};
      const bits = [];
      if (r.matched_cells !== undefined) bits.push(`${r.matched_cells}칸`);
      if (r.rows_written !== undefined) bits.push(`${r.rows_written}행`);
      if (r.sheet_name) bits.push(r.sheet_name);
      if (r.pdf_path) bits.push("pdf");
      if (r.duplicate_groups !== undefined) bits.push(`중복${r.duplicate_groups}`);
      return bits.join("/");
    })
    .filter(Boolean)
    .join(" | ");
  const status = res.ok === true ? "OK" : res.action === "excel_live.clarify" ? "되묻기" : "실패";
  lines.push(
    `[${status}] ${row.message}\n    ${actions || res.action || "-"}  ${detail}  ${row.elapsed_ms}ms` +
      (res.ok ? "" : `\n    reply: ${String(res.reply || res.error || "").slice(0, 160)}`),
  );
}
writeFileSync(outPath, lines.join("\n"), "utf-8");
console.log(`wrote summary → ${outPath}`);
