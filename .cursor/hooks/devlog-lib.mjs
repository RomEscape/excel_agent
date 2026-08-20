/**
 * 개발일지 하네스 공통.
 * 커밋 가드(scripts/check-devlog-update.mjs)와 같은 기준:
 * 코드가 바뀌면 개발일지.md도 같이 바뀌어야 한다.
 */
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import crypto from "node:crypto";

export const DEVLOG_NAME = "개발일지.md";
export const MAX_CHARS = 9000;

export function readStdin() {
  try {
    return fs.readFileSync(0, "utf8");
  } catch {
    return "";
  }
}

export function parsePayload(raw) {
  try {
    return raw ? JSON.parse(raw) : {};
  } catch {
    return {};
  }
}

export function workspaceRoot(payload) {
  const roots = payload?.workspace_roots || payload?.workspaceRoots;
  if (Array.isArray(roots) && roots[0]) return String(roots[0]);
  return process.cwd();
}

export function findDevlog(root) {
  const exact = path.join(root, DEVLOG_NAME);
  if (fs.existsSync(exact)) return exact;
  try {
    for (const name of fs.readdirSync(root)) {
      if (name === DEVLOG_NAME || name.endsWith("일지.md")) {
        return path.join(root, name);
      }
    }
  } catch {
    /* ignore */
  }
  return null;
}

export function recentSections(text, maxChars = MAX_CHARS) {
  const lines = String(text || "").split(/\r?\n/);
  const headingAt = [];
  for (let i = 0; i < lines.length; i += 1) {
    if (/^##\s+/.test(lines[i])) headingAt.push(i);
  }
  if (!headingAt.length) {
    return text.slice(Math.max(0, text.length - maxChars));
  }
  const start = headingAt[Math.max(0, headingAt.length - 4)];
  let chunk = lines.slice(start).join("\n");
  if (chunk.length > maxChars) {
    chunk = chunk.slice(chunk.length - maxChars);
  }
  return chunk.trim();
}

export function toPosix(filePath) {
  return String(filePath || "").trim().replaceAll("\\", "/");
}

export function relativeToRoot(root, filePath) {
  const rel = path.relative(root, filePath);
  if (!rel || rel.startsWith("..") || path.isAbsolute(rel)) return toPosix(filePath);
  return toPosix(rel);
}

export function isDevlogPath(relPath) {
  const p = toPosix(relPath);
  const base = p.split("/").pop();
  return p === DEVLOG_NAME || base === DEVLOG_NAME || p.endsWith("일지.md");
}

/** 커밋 가드와 동일: docs/logs/.md는 코드 변경으로 보지 않는다. 세션 훅 자체는 제외. */
export function requiresDevlog(relPath) {
  const p = toPosix(relPath);
  if (!p || isDevlogPath(p)) return false;
  if (p.startsWith("docs/") || p.startsWith("logs/")) return false;
  if (p.startsWith(".cursor/")) return false;
  if (p.endsWith(".md")) return false;
  return true;
}

function dirtyStatePath(root) {
  const id = crypto.createHash("sha1").update(String(root)).digest("hex").slice(0, 12);
  return path.join(os.tmpdir(), `officeclaw-devlog-dirty-${id}.json`);
}

export function readDirty(root) {
  try {
    const raw = fs.readFileSync(dirtyStatePath(root), "utf8");
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed?.files) ? parsed.files.map(toPosix).filter(Boolean) : [];
  } catch {
    return [];
  }
}

export function writeDirty(root, files) {
  const uniq = [...new Set(files.map(toPosix).filter(Boolean))];
  const statePath = dirtyStatePath(root);
  if (!uniq.length) {
    try {
      fs.unlinkSync(statePath);
    } catch {
      /* ignore */
    }
    return;
  }
  fs.writeFileSync(statePath, JSON.stringify({ files: uniq, updated_at: new Date().toISOString() }));
}

export function clearDirty(root) {
  writeDirty(root, []);
}

export function noteFileEdit(root, filePath) {
  const rel = relativeToRoot(root, filePath);
  if (isDevlogPath(rel)) {
    clearDirty(root);
    return { kind: "devlog", rel };
  }
  if (!requiresDevlog(rel)) {
    return { kind: "ignored", rel };
  }
  const files = readDirty(root);
  if (!files.includes(rel)) files.push(rel);
  writeDirty(root, files);
  return { kind: "code", rel };
}

export function buildSessionContext(root) {
  const devlogPath = findDevlog(root);
  if (!devlogPath) {
    return [
      `${DEVLOG_NAME}를 이 워크스페이스 루트에서 찾지 못했습니다.`,
      "작업에 들어가기 전에 파일이 있는지(OneDrive 동기화 포함) 확인하세요.",
      "일지가 없으면 이전 결정·실측을 모른 채 중복 작업을 하게 됩니다.",
    ].join("\n");
  }
  const body = fs.readFileSync(devlogPath, "utf8");
  const recent = recentSections(body, MAX_CHARS);
  return [
    "작업 시작 전 개발일지 최근 항목입니다. 지어낸 상태가 아니라 여기 적힌 실측·결정을 현재 상황으로 보고 이어가세요.",
    "세션 훅이 일부를 넣더라도 관련 날짜 섹션은 직접 Read로 여세요.",
    "코드를 바꾸면 끝난 뒤에 같은 파일에 목적/수정/검증 결과를 사실만 이어서 적으세요.",
    "",
    recent,
  ].join("\n");
}
