// 명령을 하나씩 실행하고 매번 Excel이 파일을 열 수 있는지 확인한다.
// 우리 코드로는 계속 읽히지만 Excel에서만 안 열리는 손상을 잡기 위한 것.
import { execFileSync } from "node:child_process";
import { copyFileSync, readFileSync, writeFileSync } from "node:fs";

const base = "http://127.0.0.1:19532";
const headers = { Authorization: "Bearer dev-token", "Content-Type": "application/json" };

const source = process.argv[2];
const workbook = process.argv[3];
const commands = JSON.parse(readFileSync(process.argv[4], "utf-8"));
const sheetName = process.argv[5] || "Sales_Data";

copyFileSync(source, workbook);

function excelCanOpen() {
  try {
    const out = execFileSync(
      "python-sidecar/.venv/Scripts/python.exe",
      ["python-sidecar/scripts/open_with_excel.py", workbook],
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
  writeFileSync("logs/openable_after_each.txt", lines.join("\n") + "\n", "utf-8");
}
console.log("done");
