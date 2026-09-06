/**
 * WorkbookPreview — 대상 통합문서를 앱 안에서 표로 보여 준다(값만, 읽기 전용).
 *
 * 2026-09-06 사용자: "그냥 화면 안에서 엑셀 파일 확인이 가능하게, 엑셀 UI 옆에 대화창".
 * Excel 창을 웹뷰 안에 넣을 수는 없어서 사이드카가 읽은 사용 범위를 격자로 그린다.
 * 명령이 끝날 때마다 다시 읽으므로 "시켰더니 뭐가 바뀌었나"를 앱 안에서 확인할 수 있다.
 * 서식(색·굵기·테두리)은 아직 안 그린다 — 값과 구조만.
 *
 * 상태는 store/workbookPreviewStore, 액션은 lib/workbookPreviewManager. 여기는 그리기만.
 */
import React, { useMemo } from "react";
import { ExternalLink, RefreshCw, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import {
  refreshWorkbookPreview,
  selectPreviewSheet,
  setWorkbookPreviewOpen,
} from "@/lib/workbookPreviewManager";

/** 27 → "AA" */
function colLetter(index) {
  let n = index + 1;
  let s = "";
  while (n > 0) {
    const r = (n - 1) % 26;
    s = String.fromCharCode(65 + r) + s;
    n = Math.floor((n - 1) / 26);
  }
  return s;
}

/** "C3:H9" → {row: 3, col: 2} (0-based col). 못 읽으면 A1. */
function rangeOrigin(ref) {
  const m = String(ref || "").toUpperCase().match(/^([A-Z]+)(\d+)/);
  if (!m) return { row: 1, col: 0 };
  let col = 0;
  for (const ch of m[1]) col = col * 26 + (ch.charCodeAt(0) - 64);
  return { row: Number(m[2]), col: col - 1 };
}

function formatCell(value) {
  if (value === null || value === undefined) return "";
  if (typeof value === "number") {
    return Number.isInteger(value)
      ? value.toLocaleString("ko-KR")
      : value.toLocaleString("ko-KR", { maximumFractionDigits: 4 });
  }
  if (typeof value === "boolean") return value ? "TRUE" : "FALSE";
  return String(value);
}

function agoLabel(updatedAt) {
  if (!updatedAt) return "";
  const sec = Math.max(0, Math.round((Date.now() - updatedAt) / 1000));
  if (sec < 5) return "방금 갱신";
  if (sec < 60) return `${sec}초 전 갱신`;
  return `${Math.round(sec / 60)}분 전 갱신`;
}

/**
 * @param {{
 *   data: object|null, sheet: string, loading: boolean, error: string, updatedAt: number,
 *   onOpenInExcel?: () => void, className?: string
 * }} props
 */
export function WorkbookPreview({ data, sheet, loading, error, updatedAt, onOpenInExcel, className }) {
  const values = Array.isArray(data?.values) ? data.values : [];
  const origin = useMemo(() => rangeOrigin(data?.range), [data?.range]);
  const colCount = values.reduce((m, row) => Math.max(m, Array.isArray(row) ? row.length : 0), 0);
  const headerRow = values[0] || [];
  // 첫 줄이 전부 글자면 머리글로 보고 굵게 — 실무 표의 90%가 그렇다. 틀려도 굵기뿐이다.
  const firstRowIsHeader =
    headerRow.length > 0 && headerRow.every((v) => v === null || v === undefined || typeof v === "string");

  return (
    <div className={cn("flex min-h-0 flex-col rounded-md border border-border bg-card", className)}>
      {/* 헤더: 파일명 · 시트 탭 · 갱신/열기/닫기 */}
      <div className="flex items-center gap-2 border-b border-border px-3 py-2">
        <span className="truncate text-sm font-medium" title={data?.name || ""}>
          {data?.name || "통합문서"}
        </span>
        {data?.range && (
          <span className="shrink-0 font-mono text-[11px] text-muted-foreground">{data.range}</span>
        )}
        {data?.truncated && (
          <span className="shrink-0 rounded bg-amber-500/15 px-1.5 py-0.5 text-[10px] font-medium text-amber-700 dark:text-amber-400">
            앞부분만 표시
          </span>
        )}
        <span className="ml-auto shrink-0 text-[11px] text-muted-foreground">{agoLabel(updatedAt)}</span>
        <Button
          variant="ghost"
          size="icon"
          className="h-7 w-7 shrink-0"
          title="다시 읽기"
          onClick={() => refreshWorkbookPreview()}
          disabled={loading}
        >
          <RefreshCw className={cn("h-3.5 w-3.5", loading && "animate-spin")} />
        </Button>
        {onOpenInExcel && (
          <Button variant="ghost" size="icon" className="h-7 w-7 shrink-0" title="Excel로 열기" onClick={onOpenInExcel}>
            <ExternalLink className="h-3.5 w-3.5" />
          </Button>
        )}
        <Button
          variant="ghost"
          size="icon"
          className="h-7 w-7 shrink-0"
          title="미리보기 닫기"
          onClick={() => setWorkbookPreviewOpen(false)}
        >
          <X className="h-3.5 w-3.5" />
        </Button>
      </div>

      {/* 시트 탭 — Excel 하단 탭과 같은 순서 */}
      {Array.isArray(data?.sheets) && data.sheets.length > 0 && (
        <div className="flex gap-1 overflow-x-auto border-b border-border px-2 py-1">
          {data.sheets.map((name) => {
            const active = name === (sheet || data.sheet);
            return (
              <button
                key={name}
                type="button"
                onClick={() => selectPreviewSheet(name)}
                className={cn(
                  "shrink-0 rounded px-2 py-1 text-xs transition-colors",
                  active ? "bg-primary/15 font-medium text-primary" : "text-muted-foreground hover:bg-muted",
                )}
              >
                {name}
              </button>
            );
          })}
        </div>
      )}

      {/* 격자 */}
      <div className="min-h-0 flex-1 overflow-auto select-text">
        {error && <p className="p-3 text-xs text-destructive">{error}</p>}
        {!error && !data && (
          <p className="p-3 text-xs text-muted-foreground">
            {loading ? "읽는 중..." : "왼쪽 목록에서 엑셀 파일을 클릭하면 여기에 내용이 보입니다."}
          </p>
        )}
        {!error && data && values.length === 0 && (
          <p className="p-3 text-xs text-muted-foreground">이 시트에는 아직 값이 없습니다.</p>
        )}
        {!error && values.length > 0 && (
          <table className="border-collapse text-xs" style={{ fontVariantNumeric: "tabular-nums" }}>
            <thead className="sticky top-0 z-10 bg-muted">
              <tr>
                <th className="sticky left-0 z-20 w-10 border border-border bg-muted px-1 py-0.5 text-center font-normal text-muted-foreground" />
                {Array.from({ length: colCount }, (_, c) => (
                  <th
                    key={c}
                    className="min-w-[4.5rem] border border-border px-2 py-0.5 text-center font-normal text-muted-foreground"
                  >
                    {colLetter(origin.col + c)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {values.map((row, r) => (
                <tr key={r} className={cn(r === 0 && firstRowIsHeader && "font-semibold")}>
                  <td className="sticky left-0 z-10 border border-border bg-muted px-1 py-0.5 text-center text-muted-foreground">
                    {origin.row + r}
                  </td>
                  {Array.from({ length: colCount }, (_, c) => {
                    const v = Array.isArray(row) ? row[c] : undefined;
                    const isNum = typeof v === "number";
                    return (
                      <td
                        key={c}
                        className={cn(
                          "max-w-[18rem] truncate border border-border px-2 py-0.5 align-top",
                          isNum ? "text-right" : "text-left",
                        )}
                        title={v === null || v === undefined ? "" : String(v)}
                      >
                        {formatCell(v)}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

export default WorkbookPreview;
