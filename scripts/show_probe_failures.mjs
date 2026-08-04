// 실패한 명령의 원인만 뽑아 본다.
import { readFileSync, writeFileSync } from "node:fs";

const report = JSON.parse(readFileSync(process.argv[2] || "logs/probe_live_app.json", "utf-8"));
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
writeFileSync(process.argv[3] || "logs/probe_failures.txt", lines.join("\n\n") + "\n", "utf-8");
console.log(`${lines.length} failures`);
