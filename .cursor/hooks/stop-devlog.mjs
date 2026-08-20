/**
 * 에이전트가 멈추기 직전: 코드는 바꿨는데 개발일지를 안 고쳤으면 한 번 더 쓰라고 한다.
 * git diff가 되면 그걸 쓰고, 안 되면 afterFileEdit dirty 상태를 쓴다.
 */
import { execSync } from "node:child_process";
import {
  isDevlogPath,
  parsePayload,
  readDirty,
  readStdin,
  requiresDevlog,
  toPosix,
  workspaceRoot,
} from "./devlog-lib.mjs";

function gitChanged(root) {
  try {
    const raw = execSync(
      "git -c core.quotepath=false diff --name-only --diff-filter=ACMR HEAD",
      { cwd: root, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] },
    );
    return raw
      .split(/\r?\n/g)
      .map((line) => toPosix(line))
      .filter(Boolean);
  } catch {
    return [];
  }
}

const payload = parsePayload(readStdin());
if (payload?.status === "aborted" || payload?.loop_count >= 1) {
  process.stdout.write("{}");
  process.exit(0);
}

const root = workspaceRoot(payload);
const changed = gitChanged(root);
const dirty = readDirty(root);
const files = [...new Set([...changed, ...dirty])];
const codeLike = files.filter(requiresDevlog);
const hasDevlog = files.some(isDevlogPath);

if (codeLike.length && !hasDevlog) {
  process.stdout.write(
    JSON.stringify({
      followup_message: [
        "코드는 바뀌었는데 개발일지.md는 이번 세션에서 수정되지 않았습니다.",
        "커밋 훅(devlog-guard)이 막을 내용입니다. 지금 개발일지에 목적·핵심 수정·검증 결과를 사실만 적고 끝내세요.",
        "지어내지 마세요. 돌린 명령과 숫자만 남기세요.",
        "코드 변경 파일:",
        ...codeLike.slice(0, 20).map((p) => `- ${p}`),
      ].join("\n"),
    }),
  );
} else {
  process.stdout.write("{}");
}
