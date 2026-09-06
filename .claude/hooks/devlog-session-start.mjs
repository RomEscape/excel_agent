/**
 * Claude Code SessionStart 훅.
 *  1) 작업트리 스냅샷을 기준선으로 저장한다(Stop 훅이 "이 세션에서 바뀐 것"만 보게).
 *  2) 개발일지 최근 섹션을 additionalContext 로 넣는다(Cursor sessionStart 훅과 같은 역할).
 * stdin: {session_id, cwd, hook_event_name:"SessionStart", source}
 * stdout: {"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"..."}}
 */
import fs from "node:fs";
import path from "node:path";
import {
  DEVLOG_NAME,
  parsePayload,
  projectRoot,
  readStdin,
  recentSections,
  worktreeSnapshot,
  writeBaseline,
} from "./devlog-lib.mjs";

const payload = parsePayload(readStdin());
const root = projectRoot(payload);

try {
  writeBaseline(payload?.session_id, worktreeSnapshot(root));
} catch {
  /* 기준선이 없으면 Stop 훅이 보수적으로 판정한다 */
}

let context = "";
const devlogPath = path.join(root, DEVLOG_NAME);
if (fs.existsSync(devlogPath)) {
  const recent = recentSections(fs.readFileSync(devlogPath, "utf8"), 6000);
  context = [
    `작업 시작 전 ${DEVLOG_NAME} 최근 항목이다. 여기 적힌 실측·결정이 현재 상태다 — 기억으로 덮어쓰지 말 것.`,
    "코드를 고치거나 실험을 돌린 턴은 끝나기 전에 같은 파일에 한 항목(증상·원인·조치·측정·남은 것)을 남긴다(CLAUDE.md §1).",
    "",
    recent,
  ].join("\n");
} else {
  context = `${DEVLOG_NAME} 를 프로젝트 루트(${root})에서 찾지 못했다. OneDrive 동기화 여부를 먼저 확인할 것.`;
}

process.stdout.write(
  JSON.stringify({ hookSpecificOutput: { hookEventName: "SessionStart", additionalContext: context } }),
);
