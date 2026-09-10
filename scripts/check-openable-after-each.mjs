// 명령을 하나씩 실행하고 매번 Excel이 파일을 열 수 있는지 확인한다.
// 우리 코드로는 계속 읽히지만 Excel에서만 안 열리는 손상을 잡기 위한 것.
import { execFileSync } from "node:child_process";
import { copyFileSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";

const base = "http://127.0.0.1:19532";
const headers = { Authorization: "Bearer dev-token", "Content-Type": "application/json" };

const source = process.argv[2];
const workbook = process.argv[3];
const commands = JSON.parse(readFileSync(process.argv[4], "utf-8"));
const sheetName = process.argv[5] || "Sales_Data";

copyFileSync(source, workbook);

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
const outPath = path.join(reportsDir(), "openable_after_each.txt");

function excelCanOpen() {
  try {
    const out = execFileSync(
      "services/sidecar/.venv/Scripts/python.exe",
      ["services/sidecar/scripts/open_with_excel.py", workbook],
      { encoding: "utf-8" },
    );
    return out.trim();
  } catch (err) {
    return `검사 실패: ${String(err).slice(0, 120)}`;
  }
}

const lines = [`시작 상태: ${excelCanOpen()}`];
for (const [i, message] of commands.entries()) {
  let action = "-";
  try {
    const res = await fetch(`${base}/excel-live/command`, {
      method: "POST",
      headers,
      body: JSON.stringify({
        message,
        workbook_id: workbook,
        sheet_name: sheetName,
        session_id: `openable-${i}`,
        approve: true,
      }),
    });
    const body = await res.json();
    action = `${body.action}${body.ok ? "" : " (실패)"}`;
  } catch (err) {
    action = `요청 실패: ${String(err).slice(0, 80)}`;
  }
  lines.push(`${i}. ${message}\n    ${action}\n    Excel: ${excelCanOpen()}`);
  writeFileSync(outPath, lines.join("\n") + "\n", "utf-8");
}
console.log(`done → ${outPath}`);
