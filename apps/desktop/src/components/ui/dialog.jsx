/**
 * Custom AlertDialog component — replaces browser-native confirm().
 *
 * Designed for Tauri WebView compatibility and Korean UI consistency.
 * Renders a modal overlay with confirm/cancel buttons.
 *
 * Usage:
 *   const [dialog, setDialog] = useState(null);
 *
 *   <AlertDialog
 *     open={!!dialog}
 *     title="자격증명 삭제"
 *     description={`'${dialog?.key}'를 삭제하시겠습니까?`}
 *     confirmLabel="삭제"
 *     confirmVariant="destructive"
 *     onConfirm={() => handleDelete(dialog.key)}
 *     onCancel={() => setDialog(null)}
 *   />
 */
import React, { useEffect, useRef } from "react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";

/**
 * @param {{
 *   open: boolean;
 *   title: string;
 *   description?: string;
 *   confirmLabel?: string;
 *   cancelLabel?: string;
 *   confirmVariant?: 'default'|'destructive'|'outline'|'secondary'|'ghost';
 *   requireExplicitChoice?: boolean;
 *   children?: React.ReactNode;
 *   onConfirm: () => void;
 *   onCancel: () => void;
 * }} props
 */
export function AlertDialog({
  open,
  title,
  description,
  confirmLabel = "확인",
  cancelLabel = "취소",
  confirmVariant = "default",
  // 취소에 실제 비용이 있는 다이얼로그(엑셀 승인 등)용. 켜면 배경 클릭·Escape로는
  // 닫히지 않고 버튼으로만 답할 수 있다 — 팝업 뒤 파일 목록을 무심코 눌렀다가
  // 승인 대기 중인 계획이 통째로 버려지는 사고를 막는다(2026-08-18 실측).
  requireExplicitChoice = false,
  children,
  onConfirm,
  onCancel,
}) {
  const cancelRef = useRef(null);

  // 확인만 받는 다이얼로그는 취소에 포커스를 준다 (안전한 기본 동작).
  // 입력을 받는 경우(children)는 건드리지 않는다 — 여기서 포커스를 가져가면
  // children의 autoFocus 입력이 곧바로 포커스를 뺏겨 타이핑이 안 된다.
  useEffect(() => {
    if (open && !children) {
      cancelRef.current?.focus();
    }
  }, [open, children]);

  // Close on Escape key
  useEffect(() => {
    if (!open || requireExplicitChoice) return;
    const handleKey = (e) => {
      if (e.key === "Escape") onCancel();
    };
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [open, onCancel, requireExplicitChoice]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 overflow-y-auto bg-black/50 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-labelledby="alert-dialog-title"
      aria-describedby={description ? "alert-dialog-desc" : undefined}
    >
      <div
        className="flex min-h-full items-center justify-center p-4"
        onClick={(e) => {
          if (e.target === e.currentTarget && !requireExplicitChoice) onCancel();
        }}
      >
        {/* Dialog panel */}
        <div
          className={cn(
            "relative z-10 w-full max-w-sm rounded-lg border bg-background p-6 shadow-lg",
            "animate-in fade-in-0 zoom-in-95"
          )}
        >
        <h2
          id="alert-dialog-title"
          className="text-base font-semibold text-foreground"
        >
          {title}
        </h2>

        {description && (
          <p
            id="alert-dialog-desc"
            // 승인 다이얼로그는 실행할 단계를 줄 단위로 나열한다.
            className="mt-2 whitespace-pre-line text-sm text-muted-foreground"
          >
            {description}
          </p>
        )}

        {children}

        <div className="mt-5 flex justify-end gap-2">
          <Button
            ref={cancelRef}
            variant="outline"
            size="sm"
            onClick={onCancel}
          >
            {cancelLabel}
          </Button>
          <Button variant={confirmVariant} size="sm" onClick={onConfirm}>
            {confirmLabel}
          </Button>
        </div>
        </div>
      </div>
    </div>
  );
}

export default AlertDialog;
