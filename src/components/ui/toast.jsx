/**
 * Toast notification component.
 *
 * Displays a transient notification at the bottom of the screen.
 * Supports an optional undo action (used for reversible operations like deletion).
 *
 * Usage:
 *   const [toast, setToast] = useState(null);
 *
 *   // Show a simple message
 *   setToast({ message: "저장 완료!" });
 *
 *   // Show with undo action
 *   setToast({
 *     message: "'key' 삭제됨",
 *     action: { label: "되돌리기", onClick: handleUndo },
 *     duration: 5000,
 *   });
 *
 *   <Toast toast={toast} onDismiss={() => setToast(null)} />
 */
import React, { useEffect, useRef } from "react";
import { X } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";

/**
 * @typedef {Object} ToastData
 * @property {string} message
 * @property {'default'|'success'|'error'} [variant]
 * @property {{ label: string; onClick: () => void }} [action]
 * @property {number} [duration] - Auto-dismiss delay in ms (default 4000)
 */

/**
 * @param {{ toast: ToastData|null; onDismiss: () => void }} props
 */
export function Toast({ toast, onDismiss }) {
  const timerRef = useRef(null);

  useEffect(() => {
    if (!toast) return;

    const duration = toast.duration ?? 4000;
    timerRef.current = setTimeout(() => {
      onDismiss();
    }, duration);

    return () => {
      clearTimeout(timerRef.current);
    };
  }, [toast, onDismiss]);

  if (!toast) return null;

  const variantClass =
    toast.variant === "error"
      ? "border-destructive/40 bg-destructive/10 text-destructive"
      : toast.variant === "success"
      ? "border-green-500/40 bg-green-500/10 text-green-700 dark:text-green-400"
      : "border-border bg-background text-foreground";

  return (
    <div className="fixed bottom-4 left-1/2 z-50 -translate-x-1/2">
      <div
        className={cn(
          "flex min-w-[280px] max-w-sm items-center justify-between gap-3 rounded-lg border px-4 py-3 shadow-lg",
          "animate-in slide-in-from-bottom-2 fade-in-0",
          variantClass
        )}
        role="status"
        aria-live="polite"
      >
        <p className="text-sm font-medium">{toast.message}</p>

        <div className="flex items-center gap-1 shrink-0">
          {toast.action && (
            <Button
              variant="ghost"
              size="sm"
              className="h-auto px-2 py-1 text-xs font-semibold underline-offset-2 hover:underline"
              onClick={() => {
                clearTimeout(timerRef.current);
                toast.action.onClick();
                onDismiss();
              }}
            >
              {toast.action.label}
            </Button>
          )}
          <Button
            variant="ghost"
            size="icon"
            className="h-6 w-6 shrink-0 opacity-60 hover:opacity-100"
            onClick={onDismiss}
            aria-label="닫기"
          >
            <X className="h-3 w-3" />
          </Button>
        </div>
      </div>
    </div>
  );
}

export default Toast;
