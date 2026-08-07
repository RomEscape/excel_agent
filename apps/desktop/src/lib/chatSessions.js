/**
 * chatSessions — 대화 세션 목록을 사이드바용 그룹으로 묶는 순수 모듈.
 *
 * 목업(desktop-app 브랜치)의 대화 사이드바는 세션을 "오늘 / 어제 / 지난 7일 /
 * 이전"으로 나눠 보여준다. 그 분류 규칙은 UI가 아니라 데이터 로직이므로
 * 여기서 소유한다 (CLAUDE.md 안티패턴: "UI 컴포넌트 안에 비즈니스 로직").
 *
 * 순수하게 유지하려고 "지금"을 인자로 받는다 — 테스트에서 고정 시각을 주입할 수
 * 있고, 자정 근처에서 렌더마다 결과가 흔들리는 것도 호출부가 통제한다.
 */

/** 그룹 라벨 — 표시 순서와 동일하다. */
export const GROUP_TODAY = "오늘";
export const GROUP_YESTERDAY = "어제";
export const GROUP_WEEK = "지난 7일";
export const GROUP_OLDER = "이전";

const GROUP_ORDER = [GROUP_TODAY, GROUP_YESTERDAY, GROUP_WEEK, GROUP_OLDER];

/** 로컬 타임존 기준 자정으로 내린다 — 날짜 경계는 UTC가 아니라 사용자 하루 기준. */
function startOfDay(date) {
  const d = new Date(date);
  d.setHours(0, 0, 0, 0);
  return d;
}

const DAY_MS = 24 * 60 * 60 * 1000;

/**
 * 세션 하나가 속할 그룹 라벨을 판정한다.
 *
 * @param {string|number|Date|null|undefined} timestamp last_message_at
 * @param {Date} now
 * @returns {string} GROUP_* 중 하나
 */
export function groupLabelFor(timestamp, now) {
  if (!timestamp) return GROUP_OLDER;
  const t = new Date(timestamp);
  if (Number.isNaN(t.getTime())) return GROUP_OLDER;

  const today = startOfDay(now);
  const diffDays = Math.floor((today.getTime() - startOfDay(t).getTime()) / DAY_MS);

  // 미래 시각(기기 시계 어긋남 등)은 오늘로 취급 — "이전"으로 밀리면 사용자가
  // 방금 만든 대화를 목록 맨 아래에서 찾아야 한다.
  if (diffDays <= 0) return GROUP_TODAY;
  if (diffDays === 1) return GROUP_YESTERDAY;
  if (diffDays < 7) return GROUP_WEEK;
  return GROUP_OLDER;
}

/**
 * 세션 배열 → 표시용 그룹 배열.
 *
 * 각 그룹 안은 최신 순으로 정렬한다. 비어 있는 그룹은 결과에서 빠지므로
 * UI는 받은 배열을 그대로 순회하면 된다.
 *
 * @param {Array<{session_id: string, last_message_at?: string, preview?: string, message_count?: number}>} sessions
 * @param {Date} [now]
 * @returns {Array<{label: string, items: Array<object>}>}
 */
export function groupSessions(sessions, now = new Date()) {
  const list = Array.isArray(sessions) ? sessions : [];
  const buckets = new Map(GROUP_ORDER.map((label) => [label, []]));

  for (const s of list) {
    if (!s || !s.session_id) continue;
    buckets.get(groupLabelFor(s.last_message_at, now)).push(s);
  }

  const byRecent = (a, b) => {
    const ta = a.last_message_at ? new Date(a.last_message_at).getTime() : 0;
    const tb = b.last_message_at ? new Date(b.last_message_at).getTime() : 0;
    return tb - ta;
  };

  return GROUP_ORDER.filter((label) => buckets.get(label).length > 0).map((label) => ({
    label,
    items: buckets.get(label).sort(byRecent),
  }));
}

/**
 * 사이드바 한 줄에 쓸 제목. preview가 비면 안내 문구로 대체한다.
 * 목업의 대화 항목은 한 줄 말줄임이므로 길이 제한도 여기서 건다.
 */
export function sessionTitle(session, maxLength = 40) {
  const raw = String(session?.preview || "").trim();
  if (!raw) return "(빈 대화)";
  const flat = raw.replace(/\s+/g, " ");
  return flat.length > maxLength ? `${flat.slice(0, maxLength - 1)}…` : flat;
}
