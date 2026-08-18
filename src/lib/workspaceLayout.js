// 워크스페이스 화면의 폭 배분 규칙 — 순수 함수만 둔다.
//
// 2026-08-19까지 채팅 패널은 localStorage에 저장한 px 폭을 창 크기와 무관하게 지켰다.
// 창을 ~900px로 줄이면 패널이 720px을 그대로 차지해 파일 목록이 0에 가깝게 눌리고,
// 한글 파일 이름이 한 글자씩 세로로 찍혔다. 여기서 "컨테이너 폭에 상대적인 상한"과
// "좁으면 세로로 쌓기"를 결정한다. 화면(WorkspacePage)은 이 값을 읽어 배치만 한다.

/** 채팅 패널 최소 폭(px). 이보다 좁으면 입력창·버튼이 겹친다. */
export const CHAT_WIDTH_MIN = 280;
/** 채팅 패널 최대 폭(px). 사용자가 드래그로 넓힐 수 있는 한계. */
export const CHAT_WIDTH_MAX = 720;
/** 저장값이 없거나 깨졌을 때 쓰는 폭(px). */
export const CHAT_WIDTH_DEFAULT = 360;
/** 파일 목록 쪽에 최소한 남겨 둘 폭(px). 툴바 버튼 5개와 파일 행이 겹치지 않는 폭. */
export const MAIN_MIN_WIDTH = 420;
/** 이보다 좁으면 좌우 나란히를 포기하고 위(파일)·아래(채팅)로 쌓는다(px). */
export const STACK_BREAKPOINT = 760;
/** 세로 쌓기일 때 채팅 패널이 차지하는 높이 비율과 최소 높이(px). */
export const STACKED_CHAT_HEIGHT_RATIO = 0.45;
export const STACKED_CHAT_MIN_HEIGHT = 260;

/** 0·NaN·음수·undefined는 "아직 못 쟀다"로 본다. */
function toMeasuredWidth(value) {
  const n = Number(value);
  return Number.isFinite(n) && n > 0 ? n : null;
}

/**
 * 컨테이너 폭에서 채팅 패널이 가질 수 있는 최대 폭.
 * 파일 목록에 MAIN_MIN_WIDTH를 남기고, 절대 상한 CHAT_WIDTH_MAX를 넘지 않으며,
 * 아무리 좁아도 CHAT_WIDTH_MIN 아래로는 내려가지 않는다(그때는 세로 쌓기가 맡는다).
 * 폭을 아직 모르면 절대 상한을 돌려준다.
 */
export function chatWidthUpperBound(containerWidth) {
  const width = toMeasuredWidth(containerWidth);
  if (width == null) return CHAT_WIDTH_MAX;
  return Math.max(CHAT_WIDTH_MIN, Math.min(CHAT_WIDTH_MAX, width - MAIN_MIN_WIDTH));
}

/**
 * 실제로 적용할 채팅 패널 폭 = clamp(요청 폭, CHAT_WIDTH_MIN, chatWidthUpperBound(W)).
 * 저장된 선호 폭과 드래그 중인 폭 양쪽에 같은 규칙을 쓴다.
 */
export function clampChatWidth(requested, containerWidth) {
  const upper = chatWidthUpperBound(containerWidth);
  const n = Number(requested);
  const base = Number.isFinite(n) ? n : CHAT_WIDTH_DEFAULT;
  return Math.round(Math.max(CHAT_WIDTH_MIN, Math.min(upper, base)));
}

/**
 * localStorage 문자열을 폭으로 읽는다. 범위 밖·깨진 값은 기본값.
 * 저장은 "선호 폭"이라 컨테이너 상한을 적용하지 않는다 — 창을 다시 넓히면 돌아와야 한다.
 */
export function readStoredChatWidth(raw) {
  const n = parseInt(raw ?? "", 10);
  return Number.isFinite(n) && n >= CHAT_WIDTH_MIN && n <= CHAT_WIDTH_MAX
    ? n
    : CHAT_WIDTH_DEFAULT;
}

/**
 * 컨테이너 폭으로 배치를 고른다.
 *   "side"    — 파일 목록 | 채팅 (좌우)
 *   "stacked" — 파일 목록 위, 채팅 아래
 * 폭을 아직 모르면 넓은 화면 배치("side")로 둔다.
 */
export function resolveWorkspaceLayout(containerWidth) {
  const width = toMeasuredWidth(containerWidth);
  if (width == null) return "side";
  return width < STACK_BREAKPOINT ? "stacked" : "side";
}
