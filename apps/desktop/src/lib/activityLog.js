/**
 * activityLog — 작업 기록 화면의 순수 계약.
 *
 * 여기에는 fetch도 store도 없다. "감사 로그 한 줄 → 표 한 행", "통계 → KPI 4장",
 * "현재 페이지 → 페이지 번호 목록" 세 가지 변환만 있다. 데이터 수집은
 * `lib/api.js`가, 조합은 `components/activity/ActivityPage.jsx`가 맡는다.
 *
 * 대시보드가 없어지고 작업 기록으로 병합되면서 생긴 모듈이다 — 구 Dashboard는
 * 카드 리스트라 표 모델이 필요 없었지만, 최종안은 정렬·검색·페이지네이션이 붙은
 * 표라서 "행"이라는 표시 모델이 있어야 한다.
 */

/** 표 한 페이지에 담는 행 수. 와이어프레임 기준 20행 × 30페이지. */
export const ACTIVITY_PAGE_SIZE = 20;

/**
 * 상태 토큰 — 와이어프레임의 Button 인스턴스 3종.
 *
 * 색을 여기에 두는 이유: 배지는 `완료`(연초록) / `차단`(연분홍)처럼 의미가 색에
 * 묶여 있어서 컴포넌트마다 클래스를 다시 쓰면 금방 어긋난다. 다크 대응은
 * `index.css`의 토큰이 아니라 여기 클래스의 `dark:` 변형이 맡는다 —
 * 이 배지 색은 TOKENS.md의 지면 5색에 없던 신규 값이다.
 */
export const ACTIVITY_STATUS = Object.freeze({
  done: {
    label: "완료",
    className: "bg-[#ECF8E8] text-[#1B6C00] dark:bg-[#1B3314] dark:text-[#8FE07A]",
  },
  blocked: {
    label: "차단",
    className: "bg-[#F8D1C9] text-[#8C2A17] dark:bg-[#3A1810] dark:text-[#F0A08E]",
  },
  pending: {
    label: "대기",
    className: "bg-[#FBF0D3] text-[#7A5A00] dark:bg-[#33290D] dark:text-[#E8C766]",
  },
});

/** 감사 로그의 source 값 → 와이어프레임의 디바이스 라벨. */
const DEVICE_LABELS = Object.freeze({
  desktop: "데스크탑",
  mobile: "모바일",
  telegram: "모바일",
  slack: "모바일",
  discord: "모바일",
});

/**
 * source 문자열을 디바이스 라벨로 좁힌다.
 * 모르는 값은 버리지 않고 그대로 보여준다 — 새 채널이 붙었을 때 표에서
 * 조용히 사라지는 것보다 낯선 이름이라도 보이는 편이 낫다.
 */
export function deviceLabel(source) {
  const key = String(source || "").toLowerCase();
  if (!key) return "데스크탑";
  return DEVICE_LABELS[key] ?? source;
}

/**
 * 로그 한 줄의 상태를 3값으로 접는다.
 *
 * `approved`가 명시돼 있으면 그것이 우선이고, 없으면 classification으로 떨어진다.
 * 둘 다 없으면 대기 — 실행 중이거나 승인 대기인 명령이다.
 */
export function statusOf(log) {
  if (!log) return "pending";
  // approved를 먼저 소진한다. classification과 OR로 묶으면 `approved:false` +
  // `classification:"safe"` 조합에서 거부된 명령이 완료로 표시된다.
  if (log.approved === true) return "done";
  if (log.approved === false) return "blocked";
  const cls = String(log.classification || "").toLowerCase();
  if (cls === "safe") return "done";
  if (cls === "denied") return "blocked";
  return "pending";
}

/**
 * 시간 표시 — 오늘이면 `오후 08:00`, 아니면 `6일 전`.
 *
 * 와이어프레임에 두 형태가 섞여 있다. 오늘 것까지 "0일 전"으로 쓰면 방금 한
 * 작업인지 아침에 한 작업인지 구분이 안 되고, 반대로 전부 시각으로 쓰면
 * 며칠 전 기록의 날짜를 알 수 없다.
 */
export function activityTime(timestamp, now = new Date()) {
  const ms = typeof timestamp === "number" ? timestamp * 1000 : Date.parse(timestamp);
  if (!Number.isFinite(ms) || ms <= 0) return "";
  const then = new Date(ms);

  const startOfDay = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const days = Math.round((startOfDay(now) - startOfDay(then)) / 86400000);

  if (days <= 0) {
    return then.toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit" });
  }
  if (days < 7) return `${days}일 전`;
  if (days < 30) return `${Math.floor(days / 7)}주일 전`;
  if (days < 365) return `${Math.floor(days / 30)}달 전`;
  return `${Math.floor(days / 365)}년 전`;
}

/**
 * 감사 로그 → 표 한 행.
 *
 * `명령` 칸은 2줄이다 — 명령문과 대상 파일. 파일이 없는 명령(조회·설정 등)도
 * 있으므로 file은 빈 문자열일 수 있고, 그때는 컴포넌트가 둘째 줄을 안 그린다.
 */
export function toActivityRow(log, now = new Date()) {
  if (!log) return null;
  const status = statusOf(log);
  return {
    id: log.id ?? `${log.timestamp}-${log.command ?? log.action ?? ""}`,
    device: deviceLabel(log.source),
    command: log.command ?? log.action ?? "-",
    file: log.file_name ?? log.target ?? "",
    status,
    statusLabel: ACTIVITY_STATUS[status].label,
    time: activityTime(log.timestamp, now),
  };
}

/** 로그 배열 → 행 배열. null 로그는 걸러낸다. */
export function toActivityRows(logs, now = new Date()) {
  if (!Array.isArray(logs)) return [];
  return logs.map((l) => toActivityRow(l, now)).filter(Boolean);
}

/**
 * 검색 — 명령문과 파일명 대상. 서버 왕복 없이 이미 받아온 페이지에서 거른다.
 * 디바이스·상태는 헤더 필터가 따로 맡으므로 여기 포함하지 않는다.
 */
export function filterRows(rows, query) {
  const q = String(query || "").trim().toLowerCase();
  if (!q) return rows;
  return rows.filter(
    (r) =>
      r.command.toLowerCase().includes(q) || r.file.toLowerCase().includes(q)
  );
}

/**
 * 통계 → KPI 4장.
 *
 * 구 대시보드는 `전체 / 승인 대기 / 완료 / 차단`이었는데 최종안은
 * `전체 / 완료 / 차단 / 자동 마스킹`이다. 승인 대기 카드가 빠진 자리에
 * 보안 카드에 있던 자동 마스킹이 올라왔다 — "차단과 보안 두 영역이 중복"이라는
 * 리뷰 지적에 따라 보안 카드를 통째로 없애고 이 한 장으로 합친 결과다.
 */
export function buildSummary(stats, secStats) {
  const total = stats?.total ?? 0;
  const completed = (stats?.safe ?? 0) + (stats?.confirm_approved ?? 0);
  const blocked = (stats?.denied ?? 0) + (stats?.confirm_rejected ?? 0);
  const masked = secStats?.masking?.total ?? 0;

  return [
    { id: "total", label: "전체 명령", hint: "누적 처리 건수", value: total },
    { id: "done", label: "완료된 명령", hint: "자동 실행 + 승인", value: completed },
    { id: "blocked", label: "차단된 명령", hint: "보안 정책 위반", value: blocked },
    {
      id: "masked",
      label: "자동 마스킹",
      hint: "민감정보는 AI에 전달되기 전에 자동 마스킹됩니다.",
      value: masked,
    },
  ];
}

/** 총 건수 → 총 페이지 수 (최소 1). */
export function pageCount(totalItems, pageSize = ACTIVITY_PAGE_SIZE) {
  const n = Number(totalItems);
  if (!Number.isFinite(n) || n <= 0) return 1;
  return Math.max(1, Math.ceil(n / pageSize));
}

/**
 * 페이지 번호 목록 — 와이어프레임의 `1 2 3 4 5 … 30`.
 *
 * `"gap"`은 컴포넌트가 점 3개로 그린다. 끝쪽 페이지에 있을 때는 앞에 gap이
 * 붙는다(`1 … 26 27 28 29 30`) — 항상 뒤에만 넣으면 30페이지에서 현재 위치
 * 주변을 볼 수 없다.
 */
export function buildPageList(current, total, window = 5) {
  const last = Math.max(1, total);
  const cur = Math.min(Math.max(1, current), last);
  if (last <= window + 2) {
    return Array.from({ length: last }, (_, i) => i + 1);
  }

  // 현재 위치를 window 안에 담되 1과 last는 항상 보이게 한다.
  let start = Math.min(Math.max(1, cur - Math.floor(window / 2)), last - window);
  start = Math.max(1, start);
  const middle = Array.from({ length: window }, (_, i) => start + i);

  const out = [];
  if (middle[0] > 1) out.push(1);
  if (middle[0] > 2) out.push("gap");
  out.push(...middle);
  if (middle[middle.length - 1] < last - 1) out.push("gap");
  if (middle[middle.length - 1] < last) out.push(last);
  return out;
}
