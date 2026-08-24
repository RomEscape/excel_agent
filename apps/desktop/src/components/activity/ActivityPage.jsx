/**
 * ActivityPage — 작업 기록 (와이어프레임 `작업 기록` 1280 본문).
 *
 * 구 `대시보드`와 구 `작업 검색`을 하나로 합친 페이지다. 리뷰 지적 두 가지가
 * 그대로 반영돼 있다:
 *   - "차단과 보안 두 영역이 분리된 이유가 궁금하다. 보안 안에도 차단된 명령이
 *     있어 정보가 중복된다" → 보안 카드를 없애고 `자동 마스킹`을 KPI로 올렸다.
 *   - "작업 기록과 작업 검색을 하나로 병합" → 검색창이 표 헤더에 붙었고
 *     사이드바의 `작업 검색` 항목은 사라졌다.
 *
 * 구 대시보드에서 뺀 것:
 *   - `승인 대기` KPI — 모바일은 조회 전용이라 승인 큐가 쌓이지 않는다는 리뷰 결론
 *   - AI 엔진 상태 배너 / 첫 명령 가이드 — 온보딩과 환경 설정으로 자리를 옮겼다
 *
 * 치수는 Figma export에서 그대로 옮겼다. 특히 헷갈리기 쉬운 두 가지:
 *   - **섹션 제목(`작업 요약`·`최근 활동`)은 24px w600**이고, 18px w500은
 *     KPI 카드 라벨(`전체 명령`)이다. 둘을 같은 스타일로 두면 위계가 무너진다.
*   - **명령문과 보조 정보는 한 줄에 나란히**(gap 16) 놓인다. 2줄로 쌓으면 행
 *     높이가 늘어 표가 프레임과 어긋난다. 보조 자리는 프레임에선 파일명이지만
 *     감사 로그에 파일 컬럼이 없어 툴 이름이 온다(`lib/activityLog.js` 참고).
 *
 * 상태는 갖되 도메인 로직은 갖지 않는다 — 표시 모델 변환은 전부
 * `lib/activityLog.js`(순수)가, 수집은 `lib/api.js`가 맡는다.
 */
import React, { useEffect, useMemo, useState } from "react";
import { ChevronLeft, ChevronRight, Search, TextSearch } from "lucide-react";

import { cn } from "@/lib/utils";
import {
  ACTIVITY_PAGE_SIZE,
  ACTIVITY_STATUS,
  buildPageList,
  buildSummary,
  filterRows,
  pageCount,
  sortRows,
  toActivityRows,
} from "@/lib/activityLog";
import {
  getCommandAuditLogs,
  getCommandAuditStats,
  securityStats,
} from "@/lib/api";

/**
 * 검색이 훑는 범위.
 *
 * 서버가 검색 파라미터를 받지 않아서 클라이언트가 거른다. 그렇다고 현재 페이지
 * 20건만 훑으면 "검색 결과가 없습니다"가 거짓말이 된다 — 다음 페이지에 있는데도
 * 없다고 말한다. 그래서 검색을 시작하면 최근 N건을 한 번 더 받아 그 안에서 찾고,
 * 그 범위를 화면이 문장으로 밝힌다. 500은 사이드카 `/security/audit`의 limit 상한이다.
 */
const SEARCH_WINDOW = 500;

/** 표 컬럼 폭 — Figma 기준 104 / flex / 100 / 100. */
const COLUMNS = [
  { id: "device", label: "디바이스", className: "w-[104px] shrink-0" },
  { id: "command", label: "명령", className: "flex-1 min-w-0" },
  { id: "status", label: "상태", className: "w-[100px] shrink-0" },
  { id: "time", label: "시간", className: "w-[100px] shrink-0" },
];

/** 헤더의 정렬 표시 — Figma는 아이콘이 아니라 10×5 납작한 사각형이다. */
function SortMark({ active, desc }) {
  return (
    <span
      aria-hidden="true"
      className={cn(
        "h-[5px] w-[10px] shrink-0 rounded-[1px] transition-transform",
        active ? "bg-foreground" : "bg-ink-subtle",
        active && desc && "rotate-180"
      )}
    />
  );
}

/** KPI 카드 — Figma: padding 20/12, radius 12, 1px #E1E6DF. */
function SummaryCard({ label, hint, value, loading }) {
  return (
    <div className="flex min-h-[110px] flex-1 flex-col justify-between rounded-xl border border-border bg-card px-5 py-3">
      <div className="flex flex-col gap-1">
        {/* 18px w500 #3D443C / 14px w400 #B2B9B0 */}
        <p className="truncate text-lg font-medium leading-6 text-ink-body">{label}</p>
        <p className="line-clamp-2 text-sm leading-[18px] text-ink-faint">{hint}</p>
      </div>
      {loading ? (
        <div className="mt-3 h-8 w-24 animate-pulse rounded bg-muted" />
      ) : (
        /* 숫자와 단위가 같은 스타일이다 — 24px w600 #0C1909 */
        <p className="mt-3 flex items-center gap-1 text-2xl font-semibold leading-8 text-foreground">
          <span className="tabular-nums">{value.toLocaleString()}</span>
          <span>개</span>
        </p>
      )}
    </div>
  );
}

/** 상태 배지 — Figma: minWidth 80, padding 20/8, radius 16(알약), 0.5px 테두리. */
function StatusBadge({ status }) {
  const token = ACTIVITY_STATUS[status] ?? ACTIVITY_STATUS.pending;
  return (
    <span
      className={cn(
        "inline-flex min-w-[80px] items-center justify-center rounded-2xl border-[0.5px] px-5 py-2 text-xs leading-4",
        token.className
      )}
    >
      {token.label}
    </span>
  );
}

/** 표 헤더 — 칸마다 세로 구분선이 있고 아래에 연한 초록 그림자가 깔린다. */
function TableHeader({ sort, onSort }) {
  return (
    <div
      className="flex items-center border-b border-border bg-card"
      style={{ boxShadow: "0px 4px 4px rgba(167, 224, 148, 0.10)" }}
    >
      {COLUMNS.map((col, i) => (
        <button
          key={col.id}
          type="button"
          onClick={() => onSort(col.id)}
          aria-label={`${col.label} 기준 정렬`}
          className={cn(
            "flex items-center justify-between gap-3 px-5 py-3 text-left text-sm font-normal leading-[18px] text-foreground transition-colors hover:text-primary",
            i < COLUMNS.length - 1 && "border-r border-border",
            col.className
          )}
        >
          {col.label}
          <SortMark active={sort.key === col.id} desc={sort.desc} />
        </button>
      ))}
    </div>
  );
}

/**
 * 표 한 행 — Figma: 위아래 padding 8, 행 사이 구분선 없음.
 * 디바이스·시간은 가운데 정렬, 명령 칸만 왼쪽에서 시작한다.
 */
function ActivityRow({ row }) {
  return (
    <div className="flex items-start py-2 transition-colors hover:bg-accent/40">
      <div className="flex w-[104px] shrink-0 items-center justify-center px-2.5 py-2">
        <span className="truncate text-base leading-[22px] text-foreground">{row.device}</span>
      </div>

      {/* 명령문과 보조 정보(툴 이름)는 한 줄에 나란히 (gap 16) */}
      <div className="flex min-w-0 flex-1 items-center gap-4 px-5 py-2">
        <span className="truncate text-base leading-[22px] text-foreground">{row.command}</span>
        {row.file && (
          <span className="shrink-0 text-sm leading-[18px] text-ink-subtle">{row.file}</span>
        )}
      </div>

      <div className="flex h-9 w-[100px] shrink-0 items-center justify-center px-3">
        <StatusBadge status={row.status} />
      </div>

      <div className="flex w-[100px] shrink-0 items-center justify-center py-2 text-sm leading-[18px] text-ink-subtle">
        {row.time}
      </div>
    </div>
  );
}

/** 페이지네이션 — `‹ 1 2 3 4 5 … 30 ›`. */
function Pagination({ page, total, onChange }) {
  if (total <= 1) return null;
  const items = buildPageList(page, total);

  return (
    <nav className="flex items-center justify-center gap-1 py-6" aria-label="페이지">
      <button
        type="button"
        onClick={() => onChange(page - 1)}
        disabled={page <= 1}
        className="flex h-8 w-8 items-center justify-center rounded-md text-ink-disabled transition-colors hover:bg-accent hover:text-foreground disabled:pointer-events-none disabled:opacity-40"
        aria-label="이전 페이지"
      >
        <ChevronLeft className="h-4 w-4" />
      </button>

      {items.map((item, i) =>
        item === "gap" ? (
          <span
            key={`gap-${i}`}
            className="flex h-8 w-8 items-center justify-center text-ink-disabled"
            aria-hidden="true"
          >
            &hellip;
          </span>
        ) : (
          <button
            key={item}
            type="button"
            onClick={() => onChange(item)}
            aria-current={item === page ? "page" : undefined}
            className={cn(
              "flex h-8 w-8 items-center justify-center rounded-md text-sm transition-colors",
              item === page
                ? "bg-accent font-semibold text-accent-foreground"
                : "text-ink-disabled hover:bg-accent/60 hover:text-foreground"
            )}
          >
            {item}
          </button>
        )
      )}

      <button
        type="button"
        onClick={() => onChange(page + 1)}
        disabled={page >= total}
        className="flex h-8 w-8 items-center justify-center rounded-md text-ink-disabled transition-colors hover:bg-accent hover:text-foreground disabled:pointer-events-none disabled:opacity-40"
        aria-label="다음 페이지"
      >
        <ChevronRight className="h-4 w-4" />
      </button>
    </nav>
  );
}

export default function ActivityPage() {
  const [loading, setLoading] = useState(true);
  const [stats, setStats] = useState(null);
  const [secStats, setSecStats] = useState(null);
  const [logs, setLogs] = useState([]);
  const [page, setPage] = useState(1);
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState({ key: "time", desc: true });

  // 검색 중에는 현재 페이지 대신 넓은 창을 훑는다 (SEARCH_WINDOW 주석 참고).
  const searching = query.trim().length > 0;
  const [searchLogs, setSearchLogs] = useState(null);

  // KPI는 페이지와 무관하다 — 페이지를 넘길 때마다 다시 받을 이유가 없어
  // 표 로딩과 분리해 마운트 시 한 번만 받는다.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const [statsResult, secResult] = await Promise.allSettled([
        getCommandAuditStats(),
        securityStats(),
      ]);
      if (cancelled) return;
      setStats(statsResult.status === "fulfilled" ? statsResult.value : null);
      setSecStats(secResult.status === "fulfilled" ? secResult.value : null);
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    (async () => {
      const offset = (page - 1) * ACTIVITY_PAGE_SIZE;
      try {
        const res = await getCommandAuditLogs(ACTIVITY_PAGE_SIZE, offset);
        if (!cancelled) setLogs(res?.logs ?? []);
      } catch {
        if (!cancelled) setLogs([]);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [page]);

  // 검색을 시작할 때 한 번만 넓은 창을 받는다. 타자 한 글자마다 받지 않도록
  // 의존성은 `searching`(불리언)이지 `query`가 아니다.
  useEffect(() => {
    if (!searching) {
      setSearchLogs(null);
      return undefined;
    }
    let cancelled = false;
    (async () => {
      try {
        const res = await getCommandAuditLogs(SEARCH_WINDOW, 0);
        if (!cancelled) setSearchLogs(res?.logs ?? []);
      } catch {
        if (!cancelled) setSearchLogs([]);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [searching]);

  const summary = useMemo(() => buildSummary(stats, secStats), [stats, secStats]);

  const rows = useMemo(() => {
    const source = searching ? searchLogs ?? [] : logs;
    return sortRows(filterRows(toActivityRows(source), query), sort);
  }, [logs, searchLogs, searching, query, sort]);

  const totalPages = pageCount(stats?.total ?? 0);
  const tableLoading = searching ? searchLogs === null : loading;

  const handleSort = (key) =>
    setSort((prev) => ({ key, desc: prev.key === key ? !prev.desc : true }));

  return (
    /* Figma: 본문 폭 1280, 섹션 간 gap 40 (좌우 여백은 Layout이 준다) */
    <div className="mx-auto flex max-w-[1280px] flex-col gap-10 pb-6 pt-3">
      {/* 작업 요약 — 제목 24px w600, 카드 4장 gap 19 */}
      <section className="flex flex-col gap-3">
        <div className="flex items-center gap-2">
          <TextSearch className="h-6 w-6 text-foreground" />
          <h2 className="text-2xl font-semibold leading-8 text-foreground">작업 요약</h2>
        </div>
        <div className="flex flex-col gap-[19px] sm:flex-row">
          {summary.map((card) => (
            <SummaryCard key={card.id} {...card} loading={stats === null} />
          ))}
        </div>
      </section>

      {/* 최근 활동 — 제목 24px w600 + 검색 358×38 */}
      <section className="flex flex-col gap-3">
        <div className="flex items-center justify-between gap-4">
          <h2 className="text-2xl font-semibold leading-8 text-foreground">최근 활동</h2>
          <div className="flex w-[358px] max-w-full flex-col gap-1">
            <div className="relative">
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="검색어를 입력해주세요."
                aria-label="작업 기록 검색"
                className="h-[38px] w-full rounded border border-brand-soft bg-card pl-3 pr-9 text-base leading-[22px] text-foreground placeholder:text-ink-subtle focus:border-primary focus:outline-none"
              />
              <Search className="pointer-events-none absolute right-3 top-1/2 h-5 w-5 -translate-y-1/2 text-ink-subtle" />
            </div>
            {/* 어디까지 훑었는지 밝힌다 — 안 밝히면 "없다"가 거짓말이 된다. */}
            {searching && (
              <p className="text-xs leading-4 text-ink-faint">
                최근 {SEARCH_WINDOW.toLocaleString()}건에서 찾습니다.
              </p>
            )}
          </div>
        </div>

        <div className="overflow-hidden rounded-xl border border-border bg-card">
          <TableHeader sort={sort} onSort={handleSort} />

          {tableLoading ? (
            <div>
              {[0, 1, 2, 3, 4].map((n) => (
                <div key={n} className="flex items-center gap-4 px-5 py-4">
                  <div className="h-4 w-16 animate-pulse rounded bg-muted" />
                  <div className="h-4 flex-1 animate-pulse rounded bg-muted" />
                  <div className="h-8 w-20 animate-pulse rounded-2xl bg-muted" />
                  <div className="h-4 w-14 animate-pulse rounded bg-muted" />
                </div>
              ))}
            </div>
          ) : rows.length === 0 ? (
            <div className="flex flex-col items-center justify-center gap-1.5 py-24 text-center">
              <p className="text-base text-foreground">
                {searching ? "검색 결과가 없습니다" : "아직 활동 기록이 없습니다"}
              </p>
              <p className="text-xs text-ink-faint">
                {searching
                  ? "다른 검색어로 찾아보세요."
                  : "김대리에게 첫 명령을 내리면 여기에 모든 작업이 기록됩니다."}
              </p>
            </div>
          ) : (
            <div>
              {rows.map((row) => (
                <ActivityRow key={row.id} row={row} />
              ))}
            </div>
          )}
        </div>

        {/* 검색 중에는 페이지네이션을 숨긴다 — 검색은 페이지가 아니라 창(window)
            단위라서, 페이지 번호를 같이 두면 어느 범위를 보는 건지 어긋난다. */}
        {!searching && <Pagination page={page} total={totalPages} onChange={setPage} />}
      </section>
    </div>
  );
}
