/**
 * 에이전트 응답 안에 인라인으로 붙는 결과 카드들.
 *
 * 목업(desktop-app)의 4·5번 화면 — 스프레드시트 미리보기 카드와 막대 대시보드
 * 카드 — 를 실제 Excel Live 결과로 렌더한다. 데이터 → 표시 모델 변환은
 * lib/excelResult.js가 이미 끝냈으므로 여기서는 그리기만 한다.
 *
 * 색은 전부 테마 토큰이다. 목업의 lime 계열과 green-50 은 브랜드 초록의 Figma
 * 근사치라 그대로 옮기면 CLAUDE.md의 "코드에서 새 브랜드 색을 짓지 않는다"
 * 규칙을 어기고 다크 지면에서 대비도 깨진다 → primary/muted/border로 매핑했다.
 */
import * as React from "react";
import { FileSpreadsheet, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";

/**
 * 카드 공통 껍데기 — 목업의 흰 배경 + 1px 테두리 + 라운드 10px.
 * 헤더는 파일명/상태를 좌우로 나눠 놓는 회색 바.
 */
function ResultShell({ icon: Icon = FileSpreadsheet, title, status, children, className }) {
  return (
    <div
      className={cn(
        "w-full overflow-hidden rounded-[10px] border border-border bg-card",
        className
      )}
    >
      {(title || status) && (
        <div className="flex items-center justify-between gap-3 border-b border-border bg-muted/60 px-4 py-2.5">
          <span className="flex min-w-0 items-center gap-1.5 text-xs font-semibold text-foreground">
            <Icon className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
            <span className="truncate">{title}</span>
          </span>
          {status && (
            <span className="shrink-0 text-xs font-semibold text-muted-foreground">
              {status}
            </span>
          )}
        </div>
      )}
      {children}
    </div>
  );
}

/**
 * 스프레드시트 미리보기 (목업 4번).
 *
 * 첫 행을 헤더로 강조하는 건 엑셀 관행이지만 항상 참은 아니다. 그래서 시각적
 * 강조(진한 글씨 + 회색 배경)만 주고 데이터 자체는 그대로 둔다.
 *
 * @param {object} props
 * @param {{address, rowCount, colCount, columns, rows, truncated}} props.view
 * @param {boolean} [props.busy] 진행 중이면 헤더에 "AI 분석 중" 표시
 */
export function SheetPreviewCard({ view, busy = false, title }) {
  if (!view || view.kind !== "sheet") return null;
  const { address, rowCount, colCount, columns, rows, truncated } = view;

  return (
    <ResultShell
      title={title || address || "범위 미리보기"}
      status={
        busy ? (
          <span className="inline-flex items-center gap-1.5">
            <Loader2 className="h-3 w-3 animate-spin" />
            AI 분석 중
          </span>
        ) : (
          `${rowCount}행 × ${colCount}열`
        )
      }
    >
      {rows.length === 0 ? (
        <p className="px-4 py-6 text-center text-xs text-muted-foreground">
          범위에 표시할 값이 없습니다.
        </p>
      ) : (
        // 넓은 범위는 카드 안에서만 가로 스크롤 — 채팅 폭을 밀지 않는다.
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-xs">
            <thead>
              <tr>
                {/* 행 번호 거터 */}
                <th className="sticky left-0 z-10 w-12 border-b border-r border-border bg-muted/50 px-2 py-1.5 text-center text-[11px] font-semibold text-muted-foreground" />
                {columns.map((letter) => (
                  <th
                    key={letter}
                    className="min-w-[88px] border-b border-r border-border bg-muted/50 px-2.5 py-1.5 text-center text-[11px] font-semibold text-muted-foreground"
                  >
                    {letter}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, rowIdx) => (
                <tr key={row.number}>
                  <td className="sticky left-0 z-10 border-b border-r border-border bg-muted/50 px-2 py-1.5 text-center text-[11px] text-muted-foreground">
                    {row.number}
                  </td>
                  {columns.map((letter, colIdx) => (
                    <td
                      key={letter}
                      className={cn(
                        "max-w-[220px] truncate border-b border-r border-border px-2.5 py-1.5",
                        rowIdx === 0
                          ? "bg-muted/30 font-semibold text-foreground"
                          : "text-foreground"
                      )}
                      title={row.cells[colIdx] || undefined}
                    >
                      {row.cells[colIdx] ?? ""}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {truncated && (
        <p className="border-t border-border px-4 py-2 text-[11px] text-muted-foreground">
          미리보기는 앞부분만 표시합니다 — 실제 범위는 {rowCount}행 × {colCount}열입니다.
        </p>
      )}
    </ResultShell>
  );
}

/**
 * 막대 대시보드 (목업 5번).
 *
 * 1위만 primary로 채우고 나머지는 muted 계열로 눕힌다 — 목업이 초록/회색/연회색
 * 3단으로 그린 의도(무엇이 1등인지 한눈에)를 토큰으로 옮긴 것이다.
 */
export function BarChartCard({ view }) {
  if (!view || view.kind !== "bars") return null;
  const { title, items, max, truncated } = view;
  if (items.length === 0) return null;

  const numberFormat = new Intl.NumberFormat("ko-KR");

  return (
    <div className="w-full rounded-[10px] border border-border bg-card p-4">
      <p className="mb-3 text-xs font-bold text-primary">{title}</p>
      <ul className="flex flex-col gap-2">
        {items.map((item, idx) => {
          const pct = Math.max(2, Math.round((item.value / max) * 100));
          return (
            <li key={`${item.label}-${idx}`} className="flex items-center gap-2.5">
              <span
                className="w-28 shrink-0 truncate text-xs font-semibold text-foreground"
                title={item.label}
              >
                {item.label}
              </span>
              <span className="flex h-3.5 flex-1 items-center overflow-hidden rounded-md bg-muted">
                <span
                  className={cn(
                    "h-full rounded-md transition-[width] duration-500 ease-out",
                    idx === 0 ? "bg-primary" : "bg-muted-foreground/40"
                  )}
                  style={{ width: `${pct}%` }}
                />
              </span>
              <span className="w-20 shrink-0 text-right text-xs tabular-nums text-muted-foreground">
                {numberFormat.format(item.value)}
              </span>
            </li>
          );
        })}
      </ul>
      {truncated && (
        <p className="mt-2.5 text-[11px] text-muted-foreground">
          상위 {items.length}개만 표시했습니다.
        </p>
      )}
    </div>
  );
}

/** 단일 통계값 카드 — 합계/평균처럼 값 하나만 나오는 결과. */
export function StatCard({ view }) {
  if (!view || view.kind !== "stat") return null;
  return (
    <div className="inline-flex flex-col gap-0.5 rounded-[10px] border border-border bg-card px-4 py-3">
      <span className="text-[11px] font-semibold text-muted-foreground">{view.label}</span>
      <span className="text-lg font-bold tabular-nums text-primary">{view.value}</span>
    </div>
  );
}

/**
 * 표시 모델의 kind를 보고 알맞은 카드를 고른다.
 * kind가 "text"면 카드 없이 null — 문장은 버블이 이미 보여준다.
 */
export default function ResultCard({ view, busy = false }) {
  if (!view) return null;
  switch (view.kind) {
    case "sheet":
      return <SheetPreviewCard view={view} busy={busy} />;
    case "bars":
      return <BarChartCard view={view} />;
    case "stat":
      return <StatCard view={view} />;
    default:
      return null;
  }
}
