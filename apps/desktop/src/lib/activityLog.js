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
 * 상태 토큰 — 와이어프레임의 Button 인스턴스.
 *
 * 색을 여기 모아두는 이유: 배지는 `완료`(연초록) / `차단`(연분홍)처럼 의미가
 * 색에 묶여 있어서 컴포넌트마다 클래스를 다시 쓰면 금방 어긋난다.
 *
 * 실제 색값은 `index.css`의 `--status-*` 토큰이 갖는다(라이트/다크 양쪽 정의).
 * 프레임 값은 완료 `#2DB400` on `#ECF8E8`, 차단 `#D23819` on `#F8D1C9`.
 * `대기`는 프레임에 없는 상태라 중립 토큰을 쓴다.
 */
export const ACTIVITY_STATUS = Object.freeze({
  done: {
    label: "완료",
    className: "bg-status-done-bg text-status-done border-status-done",
  },
  blocked: {
    label: "차단",
    className: "bg-status-blocked-bg text-status-blocked border-status-blocked",
  },
  pending: { label: "대기", className: "bg-muted text-muted-foreground border-border" },
});

/**
 * 감사 로그의 source 값 → 와이어프레임의 디바이스 라벨.
 *
 * 키는 사이드카 `command_audit.normalize_source()`가 보장하는 5개 enum이다
 * (`telegram|slack|discord|agent|webui`). 여기 없는 값을 쓰면 표에 `agent` 같은
 * 내부 문자열이 그대로 노출된다 — 실제로 그랬다.
 *
 * `desktop`·`mobile`은 enum에 없지만 남겨둔다. 릴레이가 자기 이름으로 기록하기
 * 시작하면 그때도 표가 깨지지 않는다.
 */
const DEVICE_LABELS = Object.freeze({
  agent: "데스크탑",
  webui: "데스크탑",
  desktop: "데스크탑",
  telegram: "모바일",
  slack: "모바일",
  discord: "모바일",
  mobile: "모바일",
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

  // approved를 먼저 소진한다. 등급과 OR로 묶으면 `approved:false` + `grade:"SAFE"`
  // 조합에서 거부된 명령이 완료로 표시된다.
  //
  // ⚠️ SQLite는 boolean 타입이 없어 `approved`가 **1 / 0 / null**로 온다.
  // `=== true`로만 비교하면 승인·거부가 전부 빠져나가 모든 행이 `대기`가 된다.
  if (log.approved === true || log.approved === 1) return "done";
  if (log.approved === false || log.approved === 0) return "blocked";

  // 등급 컬럼 이름은 `grade`다(SAFE|CONFIRM|DENIED). `classification`은 예전
  // 이름이라 혹시 남아 있는 응답을 위해 뒤에 둔다.
  const grade = String(log.grade ?? log.classification ?? "").toLowerCase();
  if (grade === "safe") return "done";
  if (grade === "denied") return "blocked";
  return "pending";
}

/**
 * 타임스탬프 → epoch ms. 못 읽으면 0.
 *
 * 표시(`activityTime`)와 정렬(`sortRows`)이 같은 파싱을 써야 한다 — 정렬이 표시
 * 문자열(`6일 전`)을 비교하면 순서가 뜻 없이 뒤섞인다.
 */

/**
 * 시간 표시 — 오늘이면 `오후 08:00`, 아니면 `6일 전`.
 *
 * 와이어프레임에 두 형태가 섞여 있다. 오늘 것까지 "0일 전"으로 쓰면 방금 한
 * 작업인지 아침에 한 작업인지 구분이 안 되고, 반대로 전부 시각으로 쓰면
 * 며칠 전 기록의 날짜를 알 수 없다.
 */
export function toEpochMs(timestamp) {
  const ms = typeof timestamp === "number" ? timestamp * 1000 : Date.parse(timestamp);
  return Number.isFinite(ms) && ms > 0 ? ms : 0;
}

export function activityTime(timestamp, now = new Date()) {
  const ms = toEpochMs(timestamp);
  if (!ms) return "";
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
 * 상태 정렬 순서 — `완료 → 차단 → 대기`.
 * 키 문자열(`done`/`blocked`/`pending`)을 그대로 비교하면 알파벳순이라
 * `blocked`가 `done`보다 앞서서 사용자가 기대하는 순서와 어긋난다.
 */
const STATUS_RANK = Object.freeze({ done: 0, blocked: 1, pending: 2 });

/**
 * 감사 로그 → 표 한 행.
 *
 * `명령` 칸은 명령문 + 보조 정보 두 조각이다. 보조 자리에는 **툴 이름**
 * (`command_log.tool_name`)이 온다 — 와이어프레임은 대상 파일명을 그렸지만
 * 감사 로그에 파일 컬럼이 없다. 없는 값을 지어내는 대신 실제로 아는 값을 넣는다.
 * (파일 컬럼이 생기면 `log.file_name`이 먼저 잡히도록 순서를 그대로 뒀다.)
 *
 * `ts`는 화면에 안 나오지만 정렬이 쓴다 — 표시 문자열로 정렬하면 안 되기 때문.
 */
export function toActivityRow(log, now = new Date()) {
  if (!log) return null;
  const status = statusOf(log);
  return {
    id: log.id ?? `${log.timestamp}-${log.command ?? log.action ?? ""}`,
    device: deviceLabel(log.source),
    command: log.command ?? log.action ?? "-",
    file: log.file_name ?? log.tool_name ?? "",
    status,
    statusLabel: ACTIVITY_STATUS[status].label,
    ts: toEpochMs(log.timestamp),
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
 * 정렬 — 받아온 페이지 안에서만 한다(서버가 정렬 파라미터를 받지 않는다).
 *
 * 칸마다 비교 대상이 다르다:
 *   - `time`   → 원본 epoch(`ts`). 표시 문자열(`6일 전`·`오후 08:00`)로 비교하면
 *               "6"과 "오"를 견주는 꼴이라 순서가 무의미해진다.
 *   - `status` → 의미 순서(`완료 → 차단 → 대기`).
 *   - 나머지  → 한국어 로케일 문자열 비교.
 *
 * 정렬이 안정적이어야 같은 시각의 행이 렌더마다 자리를 바꾸지 않으므로,
 * 값이 같으면 서버가 준 순서(id 역순 = 최신순)를 유지한다.
 */
export function sortRows(rows, sort) {
  const list = Array.isArray(rows) ? rows : [];
  const key = sort?.key ?? "time";
  const dir = sort?.desc ? -1 : 1;

  return list
    .map((row, i) => ({ row, i }))
    .sort((a, b) => {
      let cmp;
      if (key === "time") {
        cmp = (a.row.ts ?? 0) - (b.row.ts ?? 0);
      } else if (key === "status") {
        cmp = (STATUS_RANK[a.row.status] ?? 9) - (STATUS_RANK[b.row.status] ?? 9);
      } else {
        cmp = String(a.row[key] ?? "").localeCompare(String(b.row[key] ?? ""), "ko");
      }
      // 동점이면 원래 순서 유지 — 방향을 곱하지 않는다(곱하면 내림차순에서 뒤집힌다).
      return cmp === 0 ? a.i - b.i : cmp * dir;
    })
    .map((x) => x.row);
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
