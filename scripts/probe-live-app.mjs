// 실행 중인 앱의 sidecar에 실제 명령을 넣어 동작을 확인한다.
// 사용: node scripts/probe-live-app.mjs <워크북경로> [시트명]
import { writeFileSync } from "node:fs";

const base = "http://127.0.0.1:19532";
const headers = {
  Authorization: "Bearer dev-token",
  "Content-Type": "application/json",
};

const workbookId = process.argv[2];
const sheetName = process.argv[3] || "Sales_Data";
if (!workbookId) {
  console.error("워크북 경로가 필요합니다.");
  process.exit(1);
}

const COMMANDS = process.env.PROBE_COMMANDS
  ? JSON.parse(process.env.PROBE_COMMANDS)
  : [
      "매출 데이터를 주문일자 오름차순으로 정렬해줘",
      "매출이 10만 원 미만인 건은 빨간색으로 표시해줘",
      "지역별 매출 합계를 집계해서 Regional_Report 시트에 만들어줘",
      "주문번호가 중복된 건이 있는지 찾아서 알려줘",
      "영업담당자별 매출 합계를 구해서 Sales_Rank 시트에 정리해줘",
      "이익률 열에 매출이익 나누기 매출 수식을 넣어줘",
    ];

async function post(path, body) {
  const res = await fetch(`${base}${path}`, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  });
  const text = await res.text();
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${text}`);
  return JSON.parse(text);
}

async function run(message, sessionId) {
  let out = await post("/excel-live/command", {
    message,
    workbook_id: workbookId,
    sheet_name: sheetName,
    session_id: sessionId,
    approve: true,
  });
  if (out.approval_required && out.pending_approval?.approval_id) {
    out = await post("/excel-live/approval", {
      approval_id: out.pending_approval.approval_id,
      approved: true,
    });
  }
  return out;
}

const report = [];
for (const [i, message] of COMMANDS.entries()) {
  const started = Date.now();
  try {
    const out = await run(message, `probe-${i}`);
    report.push({ message, elapsed_ms: Date.now() - started, response: out });
  } catch (err) {
    report.push({
      message,
      ok: false,
      error: String(err).slice(0, 300),
      elapsed_ms: Date.now() - started,
    });
  }
}

writeFileSync("logs/probe_live_app.json", JSON.stringify(report, null, 2), "utf-8");
console.log(`wrote logs/probe_live_app.json (${report.length} commands)`);
