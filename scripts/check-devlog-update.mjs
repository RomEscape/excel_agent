import { execSync } from "node:child_process";

function parseArgs(argv) {
  const args = { staged: false, base: "", head: "" };
  for (let i = 2; i < argv.length; i += 1) {
    const token = String(argv[i] || "").trim();
    if (token === "--staged") {
      args.staged = true;
      continue;
    }
    if (token === "--base") {
      args.base = String(argv[i + 1] || "").trim();
      i += 1;
      continue;
    }
    if (token === "--head") {
      args.head = String(argv[i + 1] || "").trim();
      i += 1;
      continue;
    }
  }
  return args;
}

function normalizePath(path) {
  return String(path || "").trim().replaceAll("\\", "/");
}

// 2026-09-06: D(삭제)도 코드 변경이다 — 코드 파일을 지우기만 하는 커밋이 '[PASS] 변경 파일 없음'으로
// 통과하던 구멍(같은 날 감사에서 실측). 필터를 ACMRD 로 넓혔다.
function runGitDiff({ staged, base, head }) {
  let cmd = "";
  if (staged) {
    cmd = "git -c core.quotepath=false diff --cached --name-only --diff-filter=ACMRD";
  } else {
    if (!base || !head) {
      throw new Error("CI 모드에서는 --base/--head가 필요합니다.");
    }
    cmd = `git -c core.quotepath=false diff --name-only --diff-filter=ACMRD ${base} ${head}`;
  }
  const raw = execSync(cmd, { encoding: "utf-8", stdio: ["ignore", "pipe", "pipe"] });
  return raw
    .split(/\r?\n/g)
    .map((line) => normalizePath(line))
    .filter(Boolean);
}

function requiresDevlog(path) {
  const p = normalizePath(path);
  if (!p) return false;
  if (p === "개발일지.md") return false;
  if (p.startsWith("docs/")) return false;
  if (p.startsWith("logs/")) return false;
  if (p.endsWith(".md")) return false;
  return true;
}

function main() {
  const args = parseArgs(process.argv);
  const changed = runGitDiff(args);
  if (!changed.length) {
    console.log("[PASS] 변경 파일 없음");
    return;
  }

  const codeLike = changed.filter((path) => requiresDevlog(path));
  if (!codeLike.length) {
    console.log("[PASS] 개발일지 갱신 대상 코드 변경 없음");
    return;
  }

  const hasDevlog = changed.some((path) => normalizePath(path) === "개발일지.md");
  if (hasDevlog) {
    console.log("[PASS] 코드 변경 + 개발일지 업데이트 확인");
    return;
  }

  console.error("[FAIL] 코드 변경이 있는데 개발일지.md 업데이트가 없습니다.");
  console.error("개발일지에 이번 변경 목적/핵심 수정/검증 결과를 기록해 주세요.");
  console.error("코드 변경 파일:");
  for (const path of codeLike.slice(0, 50)) {
    console.error(`- ${path}`);
  }
  process.exit(1);
}

main();
