/**
 * ApprovalDialog — Zero-Trust 보안 확인 다이얼로그.
 *
 * R-S2: decision context 강화.
 *   - meta: 요청자(source) / 스킬(tool_name) / 세션(8자)
 *   - 명령 200자 초과 시 "전체 보기" 토글 (max-height 60vh + scroll)
 *   - 거부 시 사유 입력 (선택, 30자) — agentSubmitApproval(id, false, reason)로 전달
 *   - 키보드: Y/Enter 승인, N/Esc 거부 (단일키는 input focus 시 무시)
 *   - 위험 등급(`danger` prop): file delete 등 → 빨강 + "삭제하려면 1초 누르기" 추가 confirm
 */
import React, { useState, useEffect, useCallback, useRef } from "react";
import {
  ShieldAlert,
  CheckCircle,
  XCircle,
  Clock,
  Eye,
  Bot,
  Hash,
  Wrench,
  AlertOctagon,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const PREVIEW_LIMIT = 200;
const REASON_MAX = 30;
// 위험 등급은 0.5초 hold 후 즉시 거부 가능하지만, 위험 액션에 한해 1초 hold 강제
const HOLD_MS_DANGER = 1000;

// source enum → 사용자 친화적 라벨
const SOURCE_LABELS = {
  telegram: "Telegram 봇",
  slack: "Slack 봇",
  discord: "Discord 봇",
  local_chat: "앱 내 에이전트",
  app: "앱 내 에이전트",
  agent: "앱 내 에이전트",
  api: "API 호출",
};

function formatSource(src) {
  if (!src) return null;
  const k = String(src).toLowerCase();
  return SOURCE_LABELS[k] ?? src;
}

function shortSession(id) {
  if (!id) return null;
  const s = String(id);
  return s.length > 8 ? s.slice(0, 8) : s;
}

// ── 위험 분류 휴리스틱 (sidecar가 명시 grade를 주지 않을 때 fallback) ──────
//
// N-3 정교화 (Sprint 4):
//   - High: 데이터 영구 손실/되돌릴 수 없는 작업 (hold-to-confirm 강제)
//   - Medium: 데이터 비활성화/숨김 (현재 일반 confirm으로 처리, 추후 확장 여지)
//   - Safe: 위 키워드 없음
//
// false positive 방지: word-boundary로 segment(. _ - / 시작/끝) 단위 매칭만 인정.
//   예) "complete"의 "let"은 매칭 안 됨, "system_info.fetch"의 "system"도 단독 단어가 아니면 OK
//   tool_name 관례인 dot/snake/kebab/slash 구분자 모두 word boundary로 취급.
//
// fallback: tool_name이 없으면 명령 텍스트(command)에서도 동일 휴리스틱 적용.
const HIGH_DANGER_KEYWORDS = [
  "delete", "drop", "destroy", "wipe", "purge",
  "format", "truncate", "kill", "terminate", "rm",
];
const MEDIUM_DANGER_KEYWORDS = [
  "clear", "reset", "archive", "cancel",
  "deactivate", "disable", "revoke",
];

// segment 경계 = 영문/숫자가 아닌 문자 또는 문자열 시작/끝.
// dot/snake/kebab/slash 모두 \W로 잡히고, 공백/괄호/콜론도 동일.
function buildBoundaryRegex(words) {
  const alt = words.join("|");
  // (^|\W) prefix + (\W|$) suffix 로 경계 보장 — \b는 underscore를 단어로 보아 부정확
  return new RegExp(`(^|\\W)(${alt})(\\W|$)`, "i");
}

const HIGH_DANGER_RE = buildBoundaryRegex(HIGH_DANGER_KEYWORDS);
const MEDIUM_DANGER_RE = buildBoundaryRegex(MEDIUM_DANGER_KEYWORDS);

// High danger 판정 — toolName 우선, 없으면 commandText fallback.
// 둘 다 null이면 false (안전 측 = 일반 confirm).
function inferDangerFromTool(toolName, commandText) {
  const candidates = [toolName, commandText].filter(Boolean).map(String);
  if (candidates.length === 0) return false;
  return candidates.some((s) => HIGH_DANGER_RE.test(s));
}

// Medium danger 판정 — 향후 UI 확장용 (현재 일반 confirm과 동일 처리).
// 호출자는 시각 강조(badge 등)에만 사용 권장.
// eslint-disable-next-line no-unused-vars
function inferMediumDangerFromTool(toolName, commandText) {
  const candidates = [toolName, commandText].filter(Boolean).map(String);
  if (candidates.length === 0) return false;
  // High에 이미 잡히면 medium은 무의미
  if (candidates.some((s) => HIGH_DANGER_RE.test(s))) return false;
  return candidates.some((s) => MEDIUM_DANGER_RE.test(s));
}

export default function ApprovalDialog({
  open,
  command,
  reason,
  auditId,
  source,
  toolName,
  sessionId,
  // danger: 명시되면 그대로, 아니면 toolName 기반 자동 추론.
  // 호출자가 false를 명시했더라도 toolName이 위험 패턴이면 안전 측 보강.
  danger: dangerProp,
  timeoutSeconds = 60,
  onApprove,
  onReject,
}) {
  // toolName이 null이면 명령 텍스트 자체에서도 high-danger 키워드 추론.
  // dangerProp(sidecar 명시)이 우선, 없을 때만 휴리스틱 사용.
  const danger = dangerProp || inferDangerFromTool(toolName, command);
  const [remaining, setRemaining] = useState(timeoutSeconds);
  const [decided, setDecided] = useState(false);
  const [showFull, setShowFull] = useState(false);
  const [rejectMode, setRejectMode] = useState(false);
  const [rejectReason, setRejectReason] = useState("");
  // 위험 액션 hold 진행률 (0..1)
  const [holdProgress, setHoldProgress] = useState(0);
  const holdTimerRef = useRef(null);
  const holdRafRef = useRef(null);
  const reasonInputRef = useRef(null);

  // 모달 open 시 상태 초기화
  useEffect(() => {
    if (open) {
      setRemaining(timeoutSeconds);
      setDecided(false);
      setShowFull(false);
      setRejectMode(false);
      setRejectReason("");
      setHoldProgress(0);
    }
  }, [open, timeoutSeconds]);

  // 카운트다운
  useEffect(() => {
    if (!open || decided) return;
    if (remaining <= 0) {
      setDecided(true);
      onReject(auditId);
      return;
    }
    const timer = setTimeout(() => setRemaining((r) => r - 1), 1000);
    return () => clearTimeout(timer);
  }, [open, decided, remaining, auditId, onReject]);

  const cleanupHold = useCallback(() => {
    if (holdTimerRef.current) {
      clearTimeout(holdTimerRef.current);
      holdTimerRef.current = null;
    }
    if (holdRafRef.current) {
      cancelAnimationFrame(holdRafRef.current);
      holdRafRef.current = null;
    }
    setHoldProgress(0);
  }, []);

  useEffect(() => () => cleanupHold(), [cleanupHold]);

  const submitApprove = useCallback(() => {
    if (decided) return;
    setDecided(true);
    onApprove(auditId);
  }, [decided, auditId, onApprove]);

  const submitReject = useCallback(
    (reasonText) => {
      if (decided) return;
      setDecided(true);
      onReject(auditId, reasonText && reasonText.trim() ? reasonText.trim() : undefined);
    },
    [decided, auditId, onReject]
  );

  // 위험 등급일 때 승인은 1초 hold 강제. 일반 등급은 즉시.
  const handleApproveClick = useCallback(() => {
    if (!danger) {
      submitApprove();
      return;
    }
    // hold 시작
    if (holdTimerRef.current) return;
    const start = performance.now();
    const tick = () => {
      const elapsed = performance.now() - start;
      const ratio = Math.min(1, elapsed / HOLD_MS_DANGER);
      setHoldProgress(ratio);
      if (ratio < 1) {
        holdRafRef.current = requestAnimationFrame(tick);
      }
    };
    holdRafRef.current = requestAnimationFrame(tick);
    holdTimerRef.current = setTimeout(() => {
      cleanupHold();
      submitApprove();
    }, HOLD_MS_DANGER);
  }, [danger, submitApprove, cleanupHold]);

  const handleApproveCancel = useCallback(() => {
    if (!danger) return;
    cleanupHold();
  }, [danger, cleanupHold]);

  const handleRejectClick = useCallback(() => {
    if (rejectMode) {
      submitReject(rejectReason);
    } else {
      // 사유 입력 단계 진입 (선택사항이라 즉시 X 없이 패널 노출)
      setRejectMode(true);
      setTimeout(() => reasonInputRef.current?.focus(), 0);
    }
  }, [rejectMode, rejectReason, submitReject]);

  // ── 단축키: Y/Enter 승인, N/Esc 거부 ──
  // 단일키(Y/N)는 input focus 시 무시 — Layout의 isTypingTarget 패턴과 동일하게 처리.
  // IME 조합 중(한글 입력 등)에는 단일키 + Enter 모두 무시 — 변환 중에 우발적 승인 방지.
  useEffect(() => {
    if (!open || decided) return;
    const handler = (e) => {
      // 입력 필드 focus 시 단일키 무시 (Enter는 form context로 흘려보냄)
      const t = e.target;
      const tag = t?.tagName;
      const typing =
        tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || t?.isContentEditable;

      // IME 조합 중 — 단축키/Enter/Esc 모두 무시 (조합 확정 키는 IME가 처리)
      const composing =
        e.isComposing ||
        (e.nativeEvent && e.nativeEvent.isComposing) ||
        e.keyCode === 229;
      if (composing) return;

      if (e.key === "Escape") {
        e.preventDefault();
        // 거부 모드면 모드 해제, 아니면 즉시 거부
        if (rejectMode) {
          setRejectMode(false);
          setRejectReason("");
        } else {
          submitReject();
        }
        return;
      }
      if (e.key === "Enter") {
        // 거부 모드에선 사유 제출, 아니면 승인
        e.preventDefault();
        if (rejectMode) {
          submitReject(rejectReason);
        } else if (!danger) {
          submitApprove();
        }
        return;
      }
      if (typing) return;
      if (e.key === "y" || e.key === "Y") {
        e.preventDefault();
        if (!danger) submitApprove();
        // 위험 등급은 마우스 hold만 허용 (실수 방지)
        return;
      }
      if (e.key === "n" || e.key === "N") {
        e.preventDefault();
        // 사유 입력 단계로 진입
        if (!rejectMode) {
          setRejectMode(true);
          setTimeout(() => reasonInputRef.current?.focus(), 0);
        } else {
          submitReject(rejectReason);
        }
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [open, decided, rejectMode, rejectReason, submitApprove, submitReject, danger]);

  if (!open || decided) return null;

  // ── 표시용 데이터 ─────────────────────────────────────────────────────────
  const cmdRaw = (command || "").trim();
  const cmdLong = cmdRaw.length > PREVIEW_LIMIT;
  const cmdView = cmdLong && !showFull ? cmdRaw.slice(0, PREVIEW_LIMIT) : cmdRaw;

  const pct = Math.max(0, (remaining / timeoutSeconds) * 100);
  const urgentColor =
    remaining <= 15 ? "bg-red-500" : remaining <= 30 ? "bg-orange-400" : "bg-primary";

  // 색상 — 위험 등급 우선, 그다음 일반 CONFIRM(주황)
  const headerColor = danger ? "text-red-600" : "text-orange-600";
  const headerIcon = danger ? AlertOctagon : ShieldAlert;
  const HeaderIcon = headerIcon;
  const reasonBoxColor = danger
    ? "bg-red-50 border-red-200 dark:bg-red-950/30 dark:border-red-900"
    : "bg-orange-50 border-orange-200 dark:bg-orange-950/30 dark:border-orange-900";
  const reasonTextColor = danger
    ? "text-red-700 dark:text-red-200"
    : "text-orange-700 dark:text-orange-200";
  const reasonBodyColor = danger
    ? "text-red-900 dark:text-red-100"
    : "text-orange-900 dark:text-orange-100";

  const sourceLabel = formatSource(source);
  const sessionShort = shortSession(sessionId);
  const hasMeta = sourceLabel || toolName || sessionShort;

  return (
    <div className="fixed inset-0 z-50 overflow-y-auto bg-black/50 backdrop-blur-sm">
      <div
        className="flex min-h-full items-center justify-center p-4"
        onClick={(e) => {
          if (e.target === e.currentTarget) submitReject();
        }}
      >
      <div
        className="relative z-10 w-full max-w-lg space-y-4 rounded-lg border bg-background p-6 shadow-lg"
        role="dialog"
        aria-modal="true"
        aria-labelledby="approval-dialog-title"
      >
        {/* 헤더 */}
        <div>
          <h2
            id="approval-dialog-title"
            className={cn("flex items-center gap-2 text-base font-semibold", headerColor)}
          >
            <HeaderIcon className="h-5 w-5" />
            {danger ? "위험한 작업 — 추가 확인 필요" : "보안 확인 요청"}
          </h2>
          <p className="mt-1 text-sm text-muted-foreground">
            AI가 사용자 확인이 필요한 작업을 요청했어요.
            {danger && " 이 작업은 되돌릴 수 없을 수 있어요."}
          </p>
        </div>

        {/* 메타: 요청자만 사용자 친화적으로 표시.
            도구명/세션 ID 같은 기술 정보는 숨김 — 일반 사용자에게는 의미 없음. */}
        {sourceLabel && (
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 rounded-md border border-border bg-muted/40 px-3 py-2 text-[11px] text-muted-foreground">
            <span className="inline-flex items-center gap-1">
              <Bot className="h-3 w-3" />
              요청자: <span className="font-medium text-foreground">{sourceLabel}</span>
            </span>
          </div>
        )}

        {/* 사유 */}
        <div className={cn("rounded-md border p-3", reasonBoxColor)}>
          <p className={cn("mb-1 text-xs font-semibold", reasonTextColor)}>사유</p>
          <p className={cn("text-sm", reasonBodyColor)}>{reason}</p>
        </div>

        {/* 명령 미리보기 */}
        {cmdRaw && (
          <div>
            <div className="mb-1 flex items-center justify-between">
              <p className="text-xs font-semibold text-muted-foreground">명령 미리보기</p>
              {cmdLong && (
                <button
                  type="button"
                  onClick={() => setShowFull((v) => !v)}
                  className="inline-flex items-center gap-1 text-[11px] font-medium text-primary hover:underline"
                >
                  <Eye className="h-3 w-3" />
                  {showFull ? "접기" : `전체 보기 (${cmdRaw.length}자)`}
                </button>
              )}
            </div>
            <pre
              className={cn(
                "overflow-auto whitespace-pre-wrap break-all rounded-md bg-muted p-3 font-mono text-xs",
                showFull ? "max-h-[60vh]" : "max-h-36"
              )}
            >
              {cmdView}
              {cmdLong && !showFull && "\n..."}
            </pre>
          </div>
        )}

        {/* 타임아웃 카운트다운 */}
        <div className="space-y-1.5">
          <div className="flex items-center justify-between">
            <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <Clock className="h-3.5 w-3.5" />
              응답 제한 시간
            </span>
            <span
              className={cn(
                "font-mono text-sm font-semibold",
                remaining <= 15 ? "text-red-600" : "text-foreground"
              )}
            >
              {remaining}초
            </span>
          </div>
          <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
            <div
              className={cn("h-full rounded-full transition-all duration-1000", urgentColor)}
              style={{ width: `${pct}%` }}
            />
          </div>
          {remaining <= 15 && (
            <p className="text-xs text-red-600">시간이 초과되면 자동으로 거부됩니다.</p>
          )}
        </div>

        {/* 거부 사유 입력 (rejectMode) */}
        {rejectMode && (
          <div className="rounded-md border border-border bg-muted/30 p-3">
            <label
              htmlFor="reject-reason"
              className="mb-1 block text-xs font-semibold text-muted-foreground"
            >
              거부 사유 (선택, {REASON_MAX}자 이내)
            </label>
            <input
              id="reject-reason"
              ref={reasonInputRef}
              type="text"
              value={rejectReason}
              maxLength={REASON_MAX}
              onChange={(e) => setRejectReason(e.target.value)}
              placeholder="예: 권한 밖 작업"
              className="w-full rounded-md border border-input bg-background px-2.5 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-ring"
            />
            <p className="mt-1 text-right text-[10px] text-muted-foreground">
              {rejectReason.length}/{REASON_MAX}
            </p>
          </div>
        )}

        {/* 버튼 */}
        <div className="flex items-center justify-between gap-2">
          {/* 단축키 안내 */}
          <span className="text-[11px] text-muted-foreground">
            {danger
              ? "위험 등급 — 승인은 마우스로 1초 누르세요"
              : "Y / Enter 승인 · N / Esc 거부"}
          </span>

          <div className="flex gap-2">
            <Button
              variant="outline"
              onClick={handleRejectClick}
              className="flex items-center gap-1.5 border-destructive/40 text-destructive hover:bg-destructive/5"
            >
              <XCircle className="h-4 w-4" />
              {rejectMode ? "거부 확정" : "거부"}
            </Button>

            {/* 승인 버튼 — 위험 등급은 onMouseDown/Up + onTouchStart/End hold */}
            <button
              type="button"
              onMouseDown={danger ? handleApproveClick : undefined}
              onMouseUp={danger ? handleApproveCancel : undefined}
              onMouseLeave={danger ? handleApproveCancel : undefined}
              onTouchStart={danger ? handleApproveClick : undefined}
              onTouchEnd={danger ? handleApproveCancel : undefined}
              onClick={!danger ? handleApproveClick : undefined}
              className={cn(
                "relative inline-flex items-center gap-1.5 overflow-hidden rounded-md px-4 py-2 text-sm font-medium text-white transition-colors",
                danger
                  ? "bg-red-600 hover:bg-red-700 select-none"
                  : "bg-green-600 hover:bg-green-700"
              )}
              aria-label={danger ? "위험한 작업 승인 (1초 누르기)" : "승인"}
            >
              {/* hold 진행 표시 (위험 등급) */}
              {danger && holdProgress > 0 && (
                <span
                  aria-hidden
                  className="absolute inset-y-0 left-0 bg-red-900/40 transition-[width]"
                  style={{ width: `${holdProgress * 100}%` }}
                />
              )}
              <CheckCircle className="relative h-4 w-4" />
              <span className="relative">{danger ? "1초 눌러 승인" : "승인"}</span>
            </button>
          </div>
        </div>
      </div>
      </div>
    </div>
  );
}
