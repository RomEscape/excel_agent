/**
 * conversationGroups — 대화 목록 화면의 순수 계약.
 *
 * 사이드바의 `chatSessions.js`가 "오늘 / 어제 / 지난 7일 / 이전" 네 덩어리로
 * 묶는 것과 달리, 대화 목록 화면(와이어프레임 229:3237 · 229:3678)은
 * **날짜 하나하나**를 그룹 머리로 쓴다(`8월 18일(화)`). 규칙이 다르므로
 * 같은 모듈에 욱여넣지 않고 여기서 따로 소유한다.
 *
 * `파일별` 보기는 계약만 있고 데이터가 없다 — 사이드카 `list_sessions`가
 * 돌려주는 항목은 `{session_id, last_message_at, message_count, preview}`뿐이라
 * 대화가 어떤 파일을 다뤘는지 알 수 없다. 세션에 파일 필드가 생기면
 * `fileOf()` 하나만 고치면 화면은 그대로 동작한다.
 */

/** 화면 상단 토글 2종. */
export const CONVERSATION_VIEWS = Object.freeze(["day", "file"]);

export const CONVERSATION_VIEW_LABELS = Object.freeze({
  day: "요일별",
  file: "파일별",
});

const WEEKDAYS = ["일", "월", "화", "수", "목", "금", "토"];

/** 저장값·외부 입력을 안전한 보기 값으로 좁힌다. */
export function normalizeView(view) {
  return CONVERSATION_VIEWS.includes(view) ? view : "day";
}

// `new Date(null)`은 Invalid Date가 아니라 에포크(1970-01-01)다 — 빈 값을
// 먼저 거르지 않으면 타임스탬프 없는 세션이 `1월 1일(목)` 그룹으로 들어간다.
const isBlank = (v) => v === null || v === undefined || v === "";

/** `8월 18일(화)` — 와이어프레임의 그룹 머리 형식. */
export function dayLabel(timestamp) {
  if (isBlank(timestamp)) return "날짜 미상";
  const t = new Date(timestamp);
  if (Number.isNaN(t.getTime())) return "날짜 미상";
  return `${t.getMonth() + 1}월 ${t.getDate()}일(${WEEKDAYS[t.getDay()]})`;
}

/** 정렬·중복 판정에 쓰는 로컬 날짜 키 (`2026-08-18`). */
export function dayKey(timestamp) {
  if (isBlank(timestamp)) return "";
  const t = new Date(timestamp);
  if (Number.isNaN(t.getTime())) return "";
  const mm = String(t.getMonth() + 1).padStart(2, "0");
  const dd = String(t.getDate()).padStart(2, "0");
  return `${t.getFullYear()}-${mm}-${dd}`;
}

/**
 * 세션이 다룬 파일명. 지금은 항상 빈 문자열이다 — 위 모듈 주석 참고.
 * 백엔드가 필드를 주기 시작하면 여기만 고친다.
 */
export function fileOf(session) {
  return String(session?.file_name ?? session?.file ?? "").trim();
}

/** 파일별 보기를 실제로 그릴 수 있는 데이터가 하나라도 있는가. */
export function hasFileInfo(sessions) {
  const list = Array.isArray(sessions) ? sessions : [];
  return list.some((s) => fileOf(s).length > 0);
}

const byRecent = (a, b) => {
  const ta = a.last_message_at ? new Date(a.last_message_at).getTime() : 0;
  const tb = b.last_message_at ? new Date(b.last_message_at).getTime() : 0;
  return tb - ta;
};

/**
 * 날짜별 그룹. 최신 날짜가 먼저, 그룹 안에서도 최신이 먼저다.
 * 타임스탬프가 없거나 깨진 세션은 버리지 않고 맨 뒤 `날짜 미상`으로 모은다 —
 * 조용히 사라지면 사용자는 대화를 잃어버린 것으로 본다.
 *
 * @returns {Array<{key: string, label: string, items: Array<object>}>}
 */
export function groupSessionsByDay(sessions) {
  const list = Array.isArray(sessions) ? sessions : [];
  const buckets = new Map();

  for (const s of list) {
    if (!s || !s.session_id) continue;
    const key = dayKey(s.last_message_at);
    if (!buckets.has(key)) {
      buckets.set(key, { key, label: key ? dayLabel(s.last_message_at) : "날짜 미상", items: [] });
    }
    buckets.get(key).items.push(s);
  }

  return [...buckets.values()]
    .sort((a, b) => {
      if (!a.key) return 1; // 날짜 미상은 항상 맨 뒤
      if (!b.key) return -1;
      return b.key.localeCompare(a.key);
    })
    .map((g) => ({ ...g, items: g.items.sort(byRecent) }));
}

/**
 * 파일별 그룹. 파일을 모르는 세션은 맨 뒤 `파일 없음`으로 모은다.
 * 지금은 `fileOf()`가 늘 빈 값이라 사실상 한 그룹이 되는데, 그 사실은
 * 화면이 `hasFileInfo()`로 먼저 판정해 안내로 대체한다.
 */
export function groupSessionsByFile(sessions) {
  const list = Array.isArray(sessions) ? sessions : [];
  const buckets = new Map();

  for (const s of list) {
    if (!s || !s.session_id) continue;
    const name = fileOf(s);
    const key = name || "￿"; // 정렬 시 맨 뒤로 밀리는 키
    if (!buckets.has(key)) {
      buckets.set(key, { key, label: name || "파일 없음", items: [] });
    }
    buckets.get(key).items.push(s);
  }

  return [...buckets.values()]
    .sort((a, b) => a.key.localeCompare(b.key, "ko"))
    .map((g) => ({ ...g, items: g.items.sort(byRecent) }));
}

/** 보기 값에 맞는 그룹 배열. */
export function groupSessions(sessions, view) {
  return normalizeView(view) === "file"
    ? groupSessionsByFile(sessions)
    : groupSessionsByDay(sessions);
}
