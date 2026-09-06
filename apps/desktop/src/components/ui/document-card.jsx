/**
 * 문서 카드 UI primitive — 와이어프레임 B-1(Frame 166)의 242×268 타일.
 *
 * 구성: 썸네일 180×239 자리 + 하단 메타 바 44px(종류 아이콘 · 이름 · `3일 전`).
 *
 * 썸네일은 실제 파일 미리보기가 아니다. 엑셀 시트를 렌더하려면 파일을 통째로
 * 읽어 파싱해야 하는데, 홈에 6장을 깔면 매번 6번을 읽게 된다. 와이어프레임의
 * 시트 이미지는 자리표시자이므로 종류별 색만 다른 지면으로 대신한다.
 *
 * 상태를 갖지 않는다 — 데이터는 lib/documents.js, 액션은 lib/documentManager.js.
 */
import * as React from "react";
import { FileSpreadsheet, FileText, FileType, Presentation, Plus } from "lucide-react";
import { cn } from "@/lib/utils";

/** 종류 → 아이콘 + 썸네일 톤. 색은 실제 오피스 앱 색 계열을 따라간다. */
const KIND_STYLE = {
  excel: { icon: FileSpreadsheet, tint: "text-[#21A366]" },
  word: { icon: FileText, tint: "text-[#2B579A]" },
  powerpoint: { icon: Presentation, tint: "text-[#C43E1C]" },
  pdf: { icon: FileType, tint: "text-destructive" },
};

/**
 * @param {{path: string, name: string, kind: string, age: string}} doc
 * @param {boolean} selected 삭제 모드에서 고른 상태
 * @param {boolean} [active] 에이전트가 지금 대상으로 잡은 문서
 * @param {(path: string) => void} onOpen 더블클릭 — OS 기본 앱(Excel)으로 연다
 * @param {(doc: object) => void} [onSelect] 한 번 클릭 — 에이전트 대상으로 잡는다. 없으면 클릭이 곧 열기
 * @param {(path: string) => void} [onToggleSelect] 있으면 삭제 선택 모드로 동작한다
 *
 * 2026-09-06: 클릭 한 번이 곧 Excel 열기였다. 대상을 고를 방법이 없었고, 사용자는 "더블클릭하면
 * 열려야 하는 것 아니냐"고 했다. 워크스페이스 목록과 같은 규칙으로 맞춘다.
 */
export function DocumentCard({ doc, selected, active = false, onOpen, onSelect, onToggleSelect }) {
  const style = KIND_STYLE[doc.kind] ?? KIND_STYLE.excel;
  const Icon = style.icon;
  const selectable = typeof onToggleSelect === "function";
  const targetable = !selectable && typeof onSelect === "function";

  const handleClick = () => {
    if (selectable) onToggleSelect(doc.path);
    else if (targetable) onSelect(doc);
    else onOpen?.(doc.path);
  };

  return (
    <button
      type="button"
      onClick={handleClick}
      onDoubleClick={() => {
        if (!selectable) onOpen?.(doc.path);
      }}
      title={
        selectable
          ? `${doc.name} 선택`
          : targetable
            ? `${doc.name} — 클릭: 대상으로 선택 · 더블클릭: 열기`
            : `${doc.name} 열기`
      }
      className={cn(
        "group flex w-full flex-col overflow-hidden rounded-xl border text-left transition-colors",
        selected
          ? "border-destructive ring-2 ring-destructive/30"
          : active
            ? "border-primary ring-2 ring-primary/30"
            : "border-border hover:border-primary/50"
      )}
    >
      {/* 썸네일 자리 */}
      <span
        className={cn(
          "flex h-40 items-center justify-center bg-card transition-colors",
          !selected && "group-hover:bg-accent"
        )}
      >
        <Icon className={cn("h-10 w-10", style.tint)} />
      </span>

      {/* 메타 바 — 아이콘 · 이름 · 경과일 */}
      <span className="flex items-center gap-2 border-t border-border bg-background px-3 py-2.5">
        <Icon className={cn("h-4 w-4 shrink-0", style.tint)} />
        <span className="min-w-0 flex-1 truncate text-xs font-medium text-foreground">
          {doc.name}
          {active && (
            <span className="ml-2 rounded bg-primary/15 px-1.5 py-0.5 text-[10px] font-medium text-primary">
              대상
            </span>
          )}
        </span>
        <span className="shrink-0 text-[11px] text-muted-foreground">{doc.age}</span>
      </span>
    </button>
  );
}

/** `15개 문서 더보기...` 타일 — 그리드 마지막 칸. */
export function MoreDocumentsTile({ count, onClick }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="flex h-full min-h-[13.5rem] flex-col items-center justify-center gap-3 rounded-xl border border-border bg-card text-muted-foreground transition-colors hover:border-primary/50 hover:text-foreground"
    >
      <Plus className="h-8 w-8" />
      <span className="text-xs font-medium">{count}개 문서 더보기...</span>
    </button>
  );
}

export default DocumentCard;
