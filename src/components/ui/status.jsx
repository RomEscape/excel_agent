/**
 * 통일된 상태 표시 컴포넌트 모음.
 *
 * 모든 톤은 `lib/statusTokens.js`의 STATUS_TONE에서 가져온다.
 * 도메인별 변환은 같은 파일의 getOllamaStatus / getLLMStatus / getMessengerStatus / getSecurityStatus.
 *
 * 컴포넌트:
 *   - StatusDot: 점 + 라벨 (StatusBar segment, 작은 행 안내)
 *   - StatusBadge: 알약 배지 (인라인 표시 — MessengerSettings, ConversationsPage 등)
 *   - StatusRow: 아이콘 + 제목 + 설명 (LocalAISetupWizard 진단 항목, Dashboard 카드)
 *   - StatusBanner: 큰 배너 (Dashboard 메인 AI 엔진 영역)
 */
import React from "react";
import { cn } from "@/lib/utils";
import { STATUS_TONE } from "@/lib/statusTokens";

/**
 * 점 + 라벨 — StatusBar의 segment에 사용.
 *
 * @param {{
 *   tone: 'ok'|'warning'|'pending',
 *   label: string,
 *   icon?: React.ComponentType<{ className?: string }>,  // 라벨 앞 아이콘 (옵션)
 *   className?: string,
 * }} props
 */
export function StatusDot({ tone, label, icon: Icon, className }) {
  const t = STATUS_TONE[tone] ?? STATUS_TONE.pending;
  return (
    <span className={cn("inline-flex items-center gap-1.5", className)}>
      {Icon && <Icon className={cn("h-3.5 w-3.5", t.text)} />}
      <span
        className={cn("h-1.5 w-1.5 rounded-full shrink-0", t.dot)}
        aria-hidden="true"
      />
      <span className="text-foreground/80">{label}</span>
    </span>
  );
}

/**
 * 알약 배지 — 인라인 상태 표시 (예: MessengerSettings의 "실행 중").
 *
 * @param {{
 *   tone: 'ok'|'warning'|'pending',
 *   children: React.ReactNode,
 *   className?: string,
 * }} props
 */
export function StatusBadge({ tone, children, className }) {
  const t = STATUS_TONE[tone] ?? STATUS_TONE.pending;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium",
        t.badgeBg,
        t.badgeText,
        className
      )}
    >
      <span className={cn("h-1.5 w-1.5 rounded-full", t.dot)} aria-hidden="true" />
      {children}
    </span>
  );
}

/**
 * 진단 항목 행 — 체크리스트 형태 (LocalAISetupWizard 진단 화면 등).
 *
 * @param {{
 *   tone: 'ok'|'warning'|'pending',
 *   title: string,
 *   hint?: string,
 *   right?: React.ReactNode,  // 우측 메타 (예: 버전 코드)
 * }} props
 */
export function StatusRow({ tone, title, hint, right }) {
  const t = STATUS_TONE[tone] ?? STATUS_TONE.pending;
  const Icon = t.icon;
  return (
    <li className="flex items-center gap-2 text-xs">
      <span
        className={cn(
          "inline-flex h-4 w-4 shrink-0 items-center justify-center rounded-full",
          t.iconBg
        )}
        aria-hidden="true"
      >
        <Icon
          className={cn(
            "h-2.5 w-2.5",
            t.iconText,
            t.iconSpin && "animate-spin"
          )}
        />
      </span>
      <span className={tone === "ok" ? "text-foreground" : "text-muted-foreground"}>
        {title}
      </span>
      {hint && (
        <span className="ml-1 text-[10px] text-muted-foreground">{hint}</span>
      )}
      {right && <span className="ml-auto">{right}</span>}
    </li>
  );
}

/**
 * 큰 배너 — Dashboard 상단처럼 페이지 전체 폭에 강조 표시.
 *
 * @param {{
 *   tone: 'ok'|'warning'|'pending',
 *   icon: React.ComponentType<{ className?: string }>,
 *   title: string,
 *   description?: React.ReactNode,
 *   actions?: React.ReactNode,
 * }} props
 */
export function StatusBanner({ tone, icon: Icon, title, description, actions }) {
  const t = STATUS_TONE[tone] ?? STATUS_TONE.pending;
  return (
    <div
      className={cn(
        "flex items-start gap-4 rounded-lg border p-5",
        t.border,
        t.bg
      )}
    >
      <div
        className={cn(
          "flex h-12 w-12 shrink-0 items-center justify-center rounded-full",
          t.iconBg
        )}
      >
        <Icon className={cn("h-6 w-6", t.iconText)} />
      </div>
      <div className="flex-1 min-w-0">
        <p className={cn("font-semibold", t.text)}>{title}</p>
        {description && (
          <div className={cn("mt-0.5 text-sm", t.text, "opacity-90")}>
            {description}
          </div>
        )}
        {actions && <div className="mt-3">{actions}</div>}
      </div>
    </div>
  );
}
