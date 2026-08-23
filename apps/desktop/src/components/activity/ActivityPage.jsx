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
 * 상태는 갖되 도메인 로직은 갖지 않는다 — 표시 모델 변환은 전부
 * `lib/activityLog.js`(순수)가, 수집은 `lib/api.js`가 맡는다.
 */
import React, { useCallback, useEffect, useMemo, useState } from "react";
import { ChevronDown, ChevronLeft, ChevronRight, Search, TextSearch } from "lucide-react";

import { Card, CardContent } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import {
  ACTIVITY_PAGE_SIZE,
  ACTIVITY_STATUS,
  buildPageList,
  buildSummary,
  filterRows,
  pageCount,
  toActivityRows,
} from "@/lib/activityLog";
import {
  getCommandAuditLogs,
  getCommandAuditStats,
  securityStats,
} from "@/lib/api";

/** 표 컬럼 폭 — 와이어프레임 1280 기준 104 / 976 / 100 / 100. */
const COLUMNS = [
  { id: "device", label: "디바이스", className: "w-[104px] shrink-0" },
  { id: "command", label: "명령", className: "flex-1 min-w-0" },
  { id: "status", label: "상태", className: "w-[100px] shrink-0" },
  { id: "time", label: "시간", className: "w-[100px] shrink-0" },
];

/** KPI 카드 한 장 — 와이어프레임 306×132. */
function SummaryCard({ label, hint, value, loading }) {
  return (
    <Card>
      <CardContent className="flex h-[132px] flex-col justify-between p-5">
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold text-foreground">{label}</p>
          <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">{hint}</p>
        </div>
        {loading ? (
          <div className="h-8 w-20 animate-pulse rounded bg-muted" />
        ) : (
          <p className="text-2xl font-bold tabular-nums">
            {value.toLocaleString()}
            <span className="ml-1 text-base font-medium text-muted-foreground">개</span>
          </p>
        )}
      </CardContent>
    </Card>
  );
}

/** 상태 배지 — 완료(연초록) / 차단(연분홍) / 대기(연노랑). */
function StatusBadge({ status }) {
  const token = ACTIVITY_STATUS[status] ?? ACTIVITY_STATUS.pending;
  return (
    <span
      className={cn(
        "inline-flex h-8 w-20 items-center justify-center rounded-md text-xs font-semibold",
        token.className
      )}
    >
      {token.label}
    </span>
  );
}

/** 표 헤더 — 각 칸에 정렬 방향 표시가 붙는다. */
function TableHeader({ sort, onSort }) {
  return (
    <div className="flex items-center gap-4 border-b border-border px-4 py-3">
      {COLUMNS.map((col) => (
        <button
          key={col.id}
          type="button"
          onClick={() => onSort(col.id)}
          className={cn(
            "flex items-center gap-1 text-left text-xs font-semibold text-muted-foreground transition-colors hover:text-foreground",
            col.className
          )}
          aria-label={`${col.label} 기준 정렬`}
        >
          {col.label}
          <ChevronDown
            className={cn(
              "h-3.5 w-3.5 shrink-0 transition-transform",
              sort.key === col.id ? "text-foreground" : "opacity-50",
              sort.key === col.id && sort.desc && "rotate-180"
            )}
          />
        </button>
      ))}
    </div>
  );
}

/** 표 한 행 — 와이어프레임 1280×54. `명령` 칸만 2줄이다. */
function ActivityRow({ row }) {
  return (
    <div className="flex items-center gap-4 border-b border-border/60 px-4 py-2.5 transition-colors last:border-b-0 hover:bg-accent/40">
      <div className="w-[104px] shrink-0 truncate text-sm text-foreground">{row.device}</div>
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm text-foreground">{row.command}</p>
        {row.file && (
          <p className="truncate text-xs text-muted-foreground">{row.file}</p>
        )}
      </div>
      <div className="w-[100px] shrink-0">
        <StatusBadge status={row.status} />
      </div>
      <div className="w-[100px] shrink-0 text-sm text-muted-foreground">{row.time}</div>
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
        className="flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-foreground disabled:pointer-events-none disabled:opacity-40"
        aria-label="이전 페이지"
      >
        <ChevronLeft className="h-4 w-4" />
      </button>

      {items.map((item, i) =>
        item === "gap" ? (
          <span
            key={`gap-${i}`}
            className="flex h-8 w-8 items-center justify-center text-muted-foreground"
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
                : "text-muted-foreground hover:bg-accent/60 hover:text-foreground"
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
        className="flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-foreground disabled:pointer-events-none disabled:opacity-40"
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

  const loadData = useCallback(async (targetPage) => {
    setLoading(true);
    const offset = (targetPage - 1) * ACTIVITY_PAGE_SIZE;
    const [statsResult, logsResult, secResult] = await Promise.allSettled([
      getCommandAuditStats(),
      getCommandAuditLogs(ACTIVITY_PAGE_SIZE, offset),
      securityStats(),
    ]);

    setStats(statsResult.status === "fulfilled" ? statsResult.value : null);
    setLogs(logsResult.status === "fulfilled" ? logsResult.value?.logs ?? [] : []);
    setSecStats(secResult.status === "fulfilled" ? secResult.value : null);
    setLoading(false);
  }, []);

  useEffect(() => {
    loadData(page);
  }, [loadData, page]);

  const summary = useMemo(() => buildSummary(stats, secStats), [stats, secStats]);

  // 정렬은 받아온 페이지 안에서만 한다 — 서버가 정렬 파라미터를 받지 않으므로
  // 전체 정렬인 척하면 페이지를 넘길 때 순서가 어긋난다.
  const rows = useMemo(() => {
    const base = filterRows(toActivityRows(logs), query);
    const dir = sort.desc ? -1 : 1;
    return [...base].sort((a, b) => {
      const av = String(a[sort.key] ?? "");
      const bv = String(b[sort.key] ?? "");
      return av.localeCompare(bv, "ko") * dir;
    });
  }, [logs, query, sort]);

  const totalPages = pageCount(stats?.total ?? 0);

  const handleSort = (key) =>
    setSort((prev) => ({ key, desc: prev.key === key ? !prev.desc : true }));

  return (
    <div className="mx-auto max-w-[1280px] space-y-6">
      {/* 헤더 — 와이어프레임의 tabler:input-search + 타이틀 */}
      <div className="flex items-center gap-2">
        <TextSearch className="h-6 w-6 text-foreground" />
        <h1 className="text-xl font-bold">작업 기록</h1>
      </div>

      {/* 작업 요약 — KPI 4장 */}
      <section>
        <h2 className="mb-3 text-sm font-semibold text-muted-foreground">작업 요약</h2>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {summary.map((card) => (
            <SummaryCard key={card.id} {...card} loading={loading} />
          ))}
        </div>
      </section>

      {/* 최근 활동 — 검색 + 표 + 페이지네이션 */}
      <section>
        <div className="mb-3 flex items-center justify-between gap-4">
          <h2 className="text-sm font-semibold text-muted-foreground">최근 활동</h2>
          <div className="relative w-[358px] max-w-full">
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="검색어를 입력해주세요."
              aria-label="작업 기록 검색"
              className="h-9 w-full rounded-md border border-border bg-card pl-3 pr-9 text-sm text-foreground placeholder:text-muted-foreground focus:border-primary focus:outline-none"
            />
            <Search className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          </div>
        </div>

        <Card className="overflow-hidden">
          <TableHeader sort={sort} onSort={handleSort} />

          {loading ? (
            <div className="divide-y divide-border/60">
              {[0, 1, 2, 3, 4].map((n) => (
                <div key={n} className="flex items-center gap-4 px-4 py-4">
                  <div className="h-4 w-16 animate-pulse rounded bg-muted" />
                  <div className="h-4 flex-1 animate-pulse rounded bg-muted" />
                  <div className="h-8 w-20 animate-pulse rounded bg-muted" />
                  <div className="h-4 w-14 animate-pulse rounded bg-muted" />
                </div>
              ))}
            </div>
          ) : rows.length === 0 ? (
            <div className="flex flex-col items-center justify-center gap-1.5 py-24 text-center">
              <p className="text-sm font-semibold text-foreground">
                {query.trim() ? "검색 결과가 없습니다" : "아직 활동 기록이 없습니다"}
              </p>
              <p className="text-xs text-muted-foreground">
                {query.trim()
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
        </Card>

        <Pagination page={page} total={totalPages} onChange={setPage} />
      </section>
    </div>
  );
}
