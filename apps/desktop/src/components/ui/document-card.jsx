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
 * @param {(path: string) => void} onOpen
 * @param {(path: string) => void} [onToggleSelect] 있으면 선택 모드로 동작한다
 */
export function DocumentCard({ doc, selected, onOpen, onToggleSelect }) {
  const style = KIND_STYLE[doc.kind] ?? KIND_STYLE.excel;
  const Icon = style.icon;
  const selectable = typeof onToggleSelect === "function";

  return (
    <button
      type="button"
      onClick={() => (selectable ? onToggleSelect(doc.path) : onOpen?.(doc.path))}
      title={selectable ? `${doc.name} 선택` : `${doc.name} 열기`}
      className={cn(
        "group flex w-full flex-col overflow-hidden rounded-xl border text-left transition-colors",
        selected
          ? "border-destructive ring-2 ring-destructive/30"
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
