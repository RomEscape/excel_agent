/**
 * documents — 워크스페이스 파일 목록 → 홈 문서 카드 모델 (순수).
 *
 * 최종 와이어프레임 B-1(Frame 166)의 홈은 상태 카드 3장이 아니라 문서 카드
 * 그리드다. 카드 하나는 썸네일 + 메타 바(파일 종류 아이콘 · 이름 · `3일 전`)로,
 * 그리드는 6장까지 깔고 남은 개수를 `15개 문서 더보기...` 타일로 보여준다.
 *
 * 이 모듈은 DOM도 fetch도 모른다. `workspaceListFiles()`가 준 배열과 기준 시각을
 * 받아 표시 모델만 만든다 (CLAUDE.md 가이드라인 2 — 표시와 데이터 분리).
 */

/** 홈 그리드에 까는 카드 수. 나머지는 `더보기` 타일의 숫자가 된다. */
export const DOCUMENT_GRID_LIMIT = 6;

/**
 * 문서로 취급할 확장자 → 종류.
 *
 * 워크스페이스에는 로그·스크립트도 섞이는데 그것까지 카드로 깔면 홈이
 * 파일 탐색기가 된다. "업무 문서"만 남긴다.
 */
const KIND_BY_EXT = Object.freeze({
  xlsx: "excel",
  xlsm: "excel",
  xls: "excel",
  csv: "excel",
  docx: "word",
  doc: "word",
  pptx: "powerpoint",
  ppt: "powerpoint",
  pdf: "pdf",
});

/** 파일명에서 소문자 확장자. 확장자가 없으면 빈 문자열. */
export function extensionOf(name) {
  const base = String(name || "");
  const dot = base.lastIndexOf(".");
  if (dot <= 0 || dot === base.length - 1) return "";
  return base.slice(dot + 1).toLowerCase();
}

/** 문서 종류 — 카드 아이콘 선택에 쓴다. 문서가 아니면 null. */
export function documentKind(name) {
  return KIND_BY_EXT[extensionOf(name)] ?? null;
}

/**
 * 수정 시각 → 와이어프레임의 `1일 전` 표기.
 *
 * @param {number} mtimeSeconds unix epoch seconds (workspace_list_files가 주는 형식)
 * @param {Date} now 기준 시각 — 테스트가 고정할 수 있도록 주입받는다
 */
export function relativeDay(mtimeSeconds, now = new Date()) {
  const ms = Number(mtimeSeconds) * 1000;
  if (!Number.isFinite(ms) || ms <= 0) return "";

  // "며칠 전"은 경과 시간이 아니라 날짜 경계로 센다. 어제 23:00 파일이
  // 지금 01:00에 "0일 전"으로 보이면 안 된다.
  const startOfDay = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const days = Math.round((startOfDay(now) - startOfDay(new Date(ms))) / 86400000);

  if (days <= 0) return "오늘";
  if (days === 1) return "어제";
  return `${days}일 전`;
}

/**
 * 파일 목록 → 홈 그리드 모델.
 *
 * @param {Array<{name: string, path: string, size?: number, modified?: number, is_dir?: boolean}>} files
 * @param {Date} now 기준 시각
 * @param {number} limit 그리드에 까는 카드 수
 * @returns {{cards: Array<{path: string, name: string, kind: string, age: string, modified: number}>, remaining: number, total: number}}
 */
export function buildDocumentGrid(files, now = new Date(), limit = DOCUMENT_GRID_LIMIT) {
  const list = Array.isArray(files) ? files : [];

  const docs = list
    .filter((f) => f && !f.is_dir && documentKind(f.name))
    // 최근 수정 순 — 홈은 "방금 하던 일"로 돌아가는 자리다.
    .sort((a, b) => Number(b.modified || 0) - Number(a.modified || 0));

  const safeLimit = Number.isInteger(limit) && limit > 0 ? limit : DOCUMENT_GRID_LIMIT;

  return {
    cards: docs.slice(0, safeLimit).map((f) => ({
      path: f.path,
      name: f.name,
      kind: documentKind(f.name),
      age: relativeDay(f.modified, now),
      modified: Number(f.modified || 0),
    })),
    remaining: Math.max(0, docs.length - safeLimit),
    total: docs.length,
  };
}
