/**
 * Modal — 내용을 자유롭게 담는 범용 모달.
 *
 * `dialog.jsx`의 `AlertDialog`와 역할이 다르다. 저쪽은 "확인/취소" 두 버튼이
 * 붙박이인 확인 다이얼로그이고, 이쪽은 도움말·QR 페어링처럼 **본문이 주인공인**
 * 모달을 위한 껍데기다. 버튼이 필요하면 `footer`로 넘긴다.
 *
 * 오버레이·Esc·바깥 클릭·포커스 처리는 두 컴포넌트가 같은 규칙을 쓴다.
 *
 * 사용:
 *   <Modal open={open} title="김대리 사용법" onClose={() => setOpen(false)}>
 *     …본문…
 *   </Modal>
 */
import React, { useEffect, useRef } from "react";
import { X } from "lucide-react";

import { cn } from "@/lib/utils";

/** 폭 프리셋 — 와이어프레임의 모달 두 종(도움말 720 · QR 600)에 맞춘다. */
const WIDTHS = {
  sm: "max-w-sm",
  md: "max-w-xl",
  lg: "max-w-3xl",
};

/**
 * @param {{
 *   open: boolean;
 *   title: string;
 *   description?: string;
 *   size?: 'sm'|'md'|'lg';
 *   layer?: 'default'|'top';
 *   icon?: React.ComponentType<{className?: string}>;
 *   footer?: React.ReactNode;
 *   children?: React.ReactNode;
 *   onClose: () => void;
 * }} props
 */
export default function Modal({
  open,
  title,
  description,
  size = "md",
  layer = "default",
  icon: Icon,
  footer,
  children,
  onClose,
}) {
  const closeRef = useRef(null);

  // 열릴 때 닫기 버튼에 포커스 — 키보드 사용자가 Tab을 눌러 모달 밖으로
  // 나가버리지 않도록 시작점을 모달 안에 둔다.
  useEffect(() => {
    if (open) closeRef.current?.focus();
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e) => {
      // IME 조합 중 Esc는 변환 취소용이라 가로채지 않는다 (Layout과 같은 규칙).
      if (e.key !== "Escape") return;
      if (e.isComposing || e.keyCode === 229) return;
      onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      className={cn(
        "fixed inset-0 overflow-y-auto bg-black/50 backdrop-blur-sm",
        // 명령 팔레트가 z-[1000]이다. 팔레트를 열어둔 채로 `Cmd/Ctrl+/`를 누를 수
        // 있으므로, 그 위에 떠야 하는 모달만 `top`을 쓴다.
        layer === "top" ? "z-[1100]" : "z-50"
      )}
      role="dialog"
      aria-modal="true"
      aria-labelledby="modal-title"
    >
      <div
        className="flex min-h-full items-center justify-center p-4"
        onClick={(e) => {
          if (e.target === e.currentTarget) onClose();
        }}
      >
        <div
          className={cn(
            "relative z-10 flex w-full flex-col overflow-hidden rounded-2xl border border-border bg-background shadow-xl",
            "animate-in fade-in-0 zoom-in-95",
            WIDTHS[size] ?? WIDTHS.md
          )}
        >
          <div className="flex items-start justify-between gap-4 px-7 pb-4 pt-6">
            <div className="flex min-w-0 items-center gap-2.5">
              {Icon && (
                <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-accent">
                  <Icon className="h-[18px] w-[18px] text-primary" />
                </span>
              )}
              <div className="min-w-0">
                <h2 id="modal-title" className="truncate text-xl font-semibold text-foreground">
                  {title}
                </h2>
                {description && (
                  <p className="mt-0.5 text-sm text-ink-faint">{description}</p>
                )}
              </div>
            </div>
            <button
              ref={closeRef}
              type="button"
              onClick={onClose}
              aria-label="닫기"
              className="shrink-0 rounded-md p-1 text-ink-subtle transition-colors hover:bg-accent hover:text-foreground"
            >
              <X className="h-5 w-5" />
            </button>
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto px-7 pb-6">{children}</div>

          {footer && (
            <div className="flex items-center justify-between gap-3 border-t border-border bg-muted/40 px-7 py-4">
              {footer}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
