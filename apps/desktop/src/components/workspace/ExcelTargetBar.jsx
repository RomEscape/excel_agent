// 에이전트가 지금 무엇을 편집하는지 한 줄로 보여 준다.
//
// 2026-08-16: 이게 없어서 드래그가 안 먹던 버그를 사용자가 "인지가 안 되는 것
// 같다"로만 표현할 수 있었다. 대상 파일·시트·선택이 보이면 어긋난 순간 바로 드러난다.

import { FileSpreadsheet, Lock, MousePointerSquareDashed } from "lucide-react";

import { cn } from "@/lib/utils";

export function ExcelTargetBar({ target, className }) {
  const { available, workbookName, sheetName, selection, readOnly, engine } = target || {};

  if (!available || !workbookName) {
    return (
      <div
        className={cn(
          "flex items-center gap-2 rounded-md border border-dashed px-3 py-2 text-xs",
          "text-muted-foreground",
          className,
        )}
      >
        <FileSpreadsheet className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
        <span>대상 엑셀 파일이 없습니다 — 왼쪽 목록에서 .xlsx 파일을 클릭하면 여기에 표시됩니다.</span>
      </div>
    );
  }

  return (
    <div
      className={cn(
        "flex flex-wrap items-center gap-x-3 gap-y-1 rounded-md border px-3 py-2 text-xs",
        readOnly ? "border-amber-500/40 bg-amber-500/5" : "bg-muted/40",
        className,
      )}
    >
      <span className="flex min-w-0 items-center gap-1.5 font-medium">
        <FileSpreadsheet className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
        <span className="truncate" title={workbookName}>
          {workbookName}
        </span>
      </span>

      {sheetName ? (
        <span className="text-muted-foreground">
          시트 <span className="font-mono">{sheetName}</span>
        </span>
      ) : null}

      {selection ? (
        <span className="flex items-center gap-1 text-muted-foreground">
          <MousePointerSquareDashed className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          <span className="font-mono">{selection}</span>
        </span>
      ) : null}

      {readOnly ? (
        <span className="flex items-center gap-1 font-medium text-amber-600 dark:text-amber-400">
          <Lock className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          읽기 전용 — 편집하려면 Excel을 닫아 주세요
        </span>
      ) : null}

      {engine ? (
        <span className="ml-auto text-[10px] uppercase tracking-wider text-muted-foreground">
          {engine}
        </span>
      ) : null}
    </div>
  );
}
