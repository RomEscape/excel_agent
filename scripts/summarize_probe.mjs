// probe-live-app.mjs 결과를 한 줄 요약으로 뽑는다.
import { readFileSync, writeFileSync } from "node:fs";

const report = JSON.parse(readFileSync(process.argv[2] || "logs/probe_live_app.json", "utf-8"));
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
writeFileSync(process.argv[3] || "logs/probe_summary.txt", lines.join("\n"), "utf-8");
console.log("wrote summary");
