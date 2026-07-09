/**
 * localStorageMigration.js — localStorage 키 이름 변경 시 1회 데이터 이전.
 *
 * 브랜드 변경(`private-claw:*` → `officeclaw:*`)처럼 키 prefix가 바뀔 때,
 * 기존 사용자의 저장값을 새 키로 옮긴다. UI 컴포넌트는 새 키만 읽으면 된다.
 *
 * CLAUDE.md 모듈 규칙: 같은 이전 로직을 컴포넌트마다 인라인하지 않고 여기로 모은다.
 */

/**
 * 레거시 키의 값을 새 키로 1회 복사하고 레거시 키를 제거한다 (멱등).
 *
 * - 새 키에 이미 값이 있으면(이전 완료) 아무것도 하지 않는다.
 * - 레거시 키가 없으면(신규 사용자) 아무것도 하지 않는다.
 *
 * @param {string} oldKey 레거시 localStorage 키
 * @param {string} newKey 새 localStorage 키
 */
export function migrateLsKey(oldKey, newKey) {
  try {
    if (localStorage.getItem(newKey) !== null) return; // 이미 이전됨
    const legacy = localStorage.getItem(oldKey);
    if (legacy === null) return; // 옮길 값 없음
    localStorage.setItem(newKey, legacy);
    localStorage.removeItem(oldKey);
  } catch {
    // localStorage 접근 불가 환경 — 조용히 무시
  }
}
