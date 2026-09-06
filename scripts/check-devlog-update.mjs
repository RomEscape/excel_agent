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

//: 개발일지 제목 형식 — `## 2026-09-06 (일) 23:40 KST — 제목`.
//: 하루에 항목이 여러 개 쌓이면 날짜만으로는 순서를 알 수 없다(2026-09-06 실측:
//: 하루 17개). 앞 항목의 "남은 것"을 뒤 항목이 닫았는지 보려면 시각이 필요하다.
const DEVLOG_HEADING = /^##\s+\S/;
const DEVLOG_HEADING_WITH_KST =
  /^##\s+\d{4}-\d{2}-\d{2}\s*\([^)]*\)\s+\d{1,2}:\d{2}\s+KST\s+[—-]/;

/** 지금의 한국시간을 `2026-09-06 (일) 23:40` 로. 셸 TZ 함정을 피해 Intl 로 직접 뽑는다. */
function kstStamp() {
  const at = new Date();
  const ymd = new Intl.DateTimeFormat("sv-SE", {
    timeZone: "Asia/Seoul",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(at);
  const hm = new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Seoul",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(at);
  const day = new Intl.DateTimeFormat("ko-KR", {
    timeZone: "Asia/Seoul",
    weekday: "short",
  }).format(at);
  return `${ymd} (${day}) ${hm}`;
}

/** 이번 변경에서 **새로 추가된** 개발일지 제목 줄들. 과거 항목은 보지 않는다. */
function addedDevlogHeadings({ staged, base, head }) {
  const range = staged ? "--cached" : `${base} ${head}`;
  const cmd =
    `git -c core.quotepath=false diff ${range} --unified=0 -- 개발일지.md`;
  let raw = "";
  try {
    raw = execSync(cmd, { encoding: "utf-8", stdio: ["ignore", "pipe", "pipe"] });
  } catch {
    return []; // diff 를 못 읽으면 이 검사는 건너뛴다(훅이 커밋을 막는 이유가 되면 안 된다)
  }
  return raw
    .split(/\r?\n/g)
    .filter((line) => line.startsWith("+") && !line.startsWith("+++"))
    .map((line) => line.slice(1))
    .filter((line) => DEVLOG_HEADING.test(line));
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
    // 제목에 한국시간이 없으면 막는다 — 같은 날 항목의 순서를 잃지 않기 위해서다.
    const headings = addedDevlogHeadings(args);
    const missing = headings.filter((line) => !DEVLOG_HEADING_WITH_KST.test(line));
    if (missing.length) {
      console.error("[FAIL] 개발일지 제목에 한국시간(KST)이 없습니다.");
      console.error(`형식: ## ${kstStamp()} KST — 제목`);
      console.error("고칠 제목:");
      for (const line of missing.slice(0, 10)) {
        console.error(`- ${line.trim()}`);
      }
      process.exit(1);
    }
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
