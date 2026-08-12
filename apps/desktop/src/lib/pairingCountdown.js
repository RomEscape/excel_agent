/**
 * pairingCountdown — QR 페어링 코드의 남은 시간 계산 (순수).
 *
 * 최종 와이어프레임 B-4(Frame 167)의 `입력 가능 시간 3:29` + 재발급 버튼이
 * 이 값을 쓴다.
 *
 * 페어링 code는 relay에서 TTL(기본 120초)로 만료된다. 그 값을 데스크톱이
 * 추측하면 안 되므로 `/relay/pair`가 `expires_in`으로 알려준 값을 그대로 쓴다.
 * TTL은 rate-limit·엔트로피와 함께 곱해져야 성립하는 방어라 임의로 늘리면 안 된다
 * (CLAUDE.md 2026-08 페어링 code 방어 노트).
 *
 * DOM도 타이머도 없다 — 1초마다 다시 부르는 건 UI가 한다.
 */

/**
 * 만료 시각을 계산한다.
 *
 * @param {number} expiresInSeconds `/relay/pair`가 준 값
 * @param {number} nowMs 기준 시각 (Date.now())
 * @returns {number|null} 만료 epoch ms. 값이 없거나 0이면 null(=만료 개념 없음)
 */
export function toExpiryTime(expiresInSeconds, nowMs) {
  const secs = Number(expiresInSeconds);
  if (!Number.isFinite(secs) || secs <= 0) return null;
  return nowMs + secs * 1000;
}

/**
 * 남은 초. 만료됐으면 0, 만료 개념이 없으면 null.
 *
 * @param {number|null} expiresAtMs
 * @param {number} nowMs
 * @returns {number|null}
 */
export function remainingSeconds(expiresAtMs, nowMs) {
  // null/undefined를 먼저 걸러야 한다 — Number(null)은 NaN이 아니라 0이라
  // finite 검사만으로는 "만료 개념 없음"이 "이미 만료됨"으로 넘어간다.
  if (expiresAtMs == null) return null;
  if (!Number.isFinite(Number(expiresAtMs))) return null;
  // 올림한다 — 남은 0.4초를 "0:00"으로 찍으면 아직 되는 코드가 죽은 것처럼 보인다.
  return Math.max(0, Math.ceil((expiresAtMs - nowMs) / 1000));
}

/**
 * 남은 초 → `3:29` 표기.
 *
 * @param {number|null} seconds
 * @returns {string} 값이 없으면 빈 문자열
 */
export function formatCountdown(seconds) {
  if (seconds == null || !Number.isFinite(Number(seconds))) return "";
  const total = Math.max(0, Math.floor(seconds));
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

/**
 * 만료됐는지. 만료 개념이 없으면(구버전 relay) 항상 false —
 * 알 수 없는 걸 만료로 단정하면 멀쩡한 QR에 재발급 버튼만 남는다.
 */
export function isExpired(expiresAtMs, nowMs) {
  const left = remainingSeconds(expiresAtMs, nowMs);
  return left !== null && left <= 0;
}
