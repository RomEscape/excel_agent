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
  onConfirm,
  onCancel,
}) {
  const cancelRef = useRef(null);

  // Focus cancel button when dialog opens (safe default action)
  useEffect(() => {
    if (open) {
      cancelRef.current?.focus();
    }
  }, [open]);

  // Close on Escape key
  useEffect(() => {
    if (!open) return;
    const handleKey = (e) => {
      if (e.key === "Escape") onCancel();
    };
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [open, onCancel]);

  if (!open) return null;

  return (
    /* Backdrop */
    <div
      className="fixed inset-0 z-50 flex items-center justify-center"
      role="dialog"
      aria-modal="true"
      aria-labelledby="alert-dialog-title"
      aria-describedby={description ? "alert-dialog-desc" : undefined}
    >
      {/* Overlay */}
      <div
        className="absolute inset-0 bg-black/50 backdrop-blur-sm"
        onClick={onCancel}
        aria-hidden="true"
      />

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
            className="mt-2 text-sm text-muted-foreground"
          >
            {description}
          </p>
        )}

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
  );
}

export default AlertDialog;
