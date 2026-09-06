/**
 * Claude Code 훅 공통 — 개발일지 하네스.
 *
 * .cursor/hooks/devlog-lib.mjs 의 판정 규칙(requiresDevlog·isDevlogPath·recentSections)을 그대로
 * 가져다 쓰고, 여기서는 Claude Code 에만 필요한 것만 둔다:
 *   - 세션 시작 시점의 작업트리 스냅샷(경로 → 내용 sha1). Stop 훅은 이 스냅샷과의 **차이**만 본다.
 *     git diff HEAD 만 보면 세션 전부터 있던 미커밋 변경(예: 배터리 로그 JSON) 때문에 대화만 한
 *     턴에도 잔소리가 나온다(2026-09-06 Cursor stop 훅 실측이 정확히 그 경우였다).
 *   - untracked 새 파일도 본다(`git diff HEAD` 는 못 본다 — 같은 날 실측).
 */
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { execFileSync } from "node:child_process";

export {
  DEVLOG_NAME,
  isDevlogPath,
  parsePayload,
  readStdin,
  recentSections,
  requiresDevlog,
  toPosix,
} from "../../.cursor/hooks/devlog-lib.mjs";

import { isDevlogPath, requiresDevlog, toPosix } from "../../.cursor/hooks/devlog-lib.mjs";

export function projectRoot(payload) {
  const fromEnv = process.env.CLAUDE_PROJECT_DIR;
  if (fromEnv && fs.existsSync(fromEnv)) return fromEnv;
  if (payload?.cwd && fs.existsSync(payload.cwd)) return payload.cwd;
  return process.cwd();
}

function git(root, args) {
  return execFileSync("git", ["-c", "core.quotepath=false", ...args], {
    cwd: root,
    encoding: "utf8",
    stdio: ["ignore", "pipe", "ignore"],
  });
}

/** 작업트리에서 HEAD 와 다른 파일 + untracked 파일 → { relPath: sha1 | "deleted" } */
export function worktreeSnapshot(root) {
  const out = {};
  let changed = [];
  let untracked = [];
  try {
    changed = git(root, ["diff", "--name-only", "--diff-filter=ACMRD", "HEAD"]).split(/\r?\n/g);
    untracked = git(root, ["ls-files", "--others", "--exclude-standard"]).split(/\r?\n/g);
  } catch {
    return out; // git 이 없거나 저장소가 아니면 빈 스냅샷 — 훅은 침묵한다
  }
  for (const raw of [...changed, ...untracked]) {
    const rel = toPosix(raw);
    if (!rel) continue;
    const abs = path.join(root, rel);
    if (!fs.existsSync(abs)) {
      out[rel] = "deleted";
      continue;
    }
    try {
      out[rel] = crypto.createHash("sha1").update(fs.readFileSync(abs)).digest("hex");
    } catch {
      out[rel] = "unreadable";
    }
  }
  return out;
}

function baselinePath(sessionId) {
  const id = String(sessionId || "nosession").replace(/[^a-zA-Z0-9_-]/g, "_").slice(0, 64);
  return path.join(os.tmpdir(), `officeclaw-claude-devlog-baseline-${id}.json`);
}

export function writeBaseline(sessionId, snapshot) {
  fs.writeFileSync(baselinePath(sessionId), JSON.stringify({ at: new Date().toISOString(), files: snapshot }));
}

export function readBaseline(sessionId) {
  try {
    const parsed = JSON.parse(fs.readFileSync(baselinePath(sessionId), "utf8"));
    return parsed && typeof parsed.files === "object" ? parsed.files : null;
  } catch {
    return null;
  }
}

/**
 * 세션 동안 바뀐 파일 = 지금 스냅샷에서 기준선과 sha 가 다르거나 새로 생긴 것.
 * 기준선이 없으면(훅이 세션 시작 뒤에 켜짐) 지금 스냅샷 전체를 쓴다 — Cursor 훅과 같은 보수적 판정.
 */
export function sessionChanges(now, baseline) {
  const changed = [];
  for (const [rel, sha] of Object.entries(now)) {
    if (!baseline || baseline[rel] !== sha) changed.push(rel);
  }
  return changed;
}

export function classify(files) {
  return {
    codeLike: files.filter((f) => requiresDevlog(f)),
    devlogTouched: files.some((f) => isDevlogPath(f)),
  };
}
