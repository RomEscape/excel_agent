/**
 * 앱 전체 상태 표시 통일을 위한 단일 진실 공급원.
 *
 * 톤(tone) 3단계 — 사용자가 "되는지/안 되는지/확인 중인지"만 알면 충분하다.
 * 세부 사유는 tooltip/배너 본문 텍스트에서 풀어 쓴다.
 *
 *   ok      → 녹색, "준비됨"        (정상 동작 가능)
 *   warning → 호박색, "문제 있음"   (설치 안 됨/중지/연결 실패 — 사용자 조치 필요)
 *   pending → 회색 spinner, "확인 중" (초기 헬스 체크 진행 중)
 *
 * 도메인별 매퍼는 sidecar/store 응답 형태를 받아 톤 + 라벨로 변환한다.
 * UI 컴포넌트는 `components/ui/status.jsx`의 StatusDot/StatusBadge/StatusRow를 사용한다.
 */

import {
  Check,
  AlertTriangle,
  Loader2,
} from "lucide-react";

/**
 * 톤 정의 — 색상 클래스/라벨/아이콘.
 *
 * dot/text/bg는 각각 dot 표시, 텍스트 강조, 배경 강조용. 카드/배지에서 골라 쓴다.
 */
export const STATUS_TONE = Object.freeze({
  ok: {
    label: "준비됨",
    dot: "bg-green-500",
    text: "text-green-600 dark:text-green-400",
    border: "border-green-200 dark:border-green-900/40",
    bg: "bg-green-50 dark:bg-green-950/20",
    badgeBg: "bg-green-100 dark:bg-green-900/30",
    badgeText: "text-green-700 dark:text-green-300",
    icon: Check,
    iconBg: "bg-green-100 dark:bg-green-900/40",
    iconText: "text-green-600 dark:text-green-400",
  },
  warning: {
    label: "문제 있음",
    dot: "bg-amber-500",
    text: "text-amber-600 dark:text-amber-400",
    border: "border-amber-200 dark:border-amber-900/40",
    bg: "bg-amber-50 dark:bg-amber-950/30",
    badgeBg: "bg-amber-100 dark:bg-amber-900/30",
    badgeText: "text-amber-700 dark:text-amber-300",
    icon: AlertTriangle,
    iconBg: "bg-amber-100 dark:bg-amber-900/40",
    iconText: "text-amber-600 dark:text-amber-400",
  },
  pending: {
    label: "확인 중",
    dot: "bg-muted-foreground/40",
    text: "text-muted-foreground",
    border: "border-border",
    bg: "bg-muted/30",
    badgeBg: "bg-muted",
    badgeText: "text-muted-foreground",
    icon: Loader2,
    iconBg: "bg-muted",
    iconText: "text-muted-foreground",
    iconSpin: true,
  },
});

/**
 * Ollama(로컬 AI 엔진) 상태 → tone + 사용자용 라벨.
 *
 * statusStore의 ollama 모듈 state-machine 값을 받는다:
 *   'running_healthy' | 'installed_stopped' | 'not_installed' | 'error' | 'unknown'.
 * 사용자에게는 install/start 구분 없이 "준비됨/문제 있음/확인 중"만 노출.
 * (세부 구분이 필요한 LocalAISetupWizard의 진단 화면은 별도 처리)
 *
 * @param {string} state
 * @returns {{ tone: 'ok'|'warning'|'pending', label: string, sub?: string }}
 */
export function getOllamaStatus(state) {
  if (state === "running_healthy" || state === "running") {
    return { tone: "ok", label: "AI 엔진 준비됨" };
  }
  if (state === "unknown" || state === "checking") {
    return { tone: "pending", label: "AI 엔진 확인 중" };
  }
  // installed_stopped | not_installed | error | 그 외 — 모두 "문제 있음"으로 통일
  return {
    tone: "warning",
    label: "AI 엔진 문제 있음",
    sub: "Ollama 설치 또는 실행이 필요해요",
  };
}

/**
 * AI 엔진(LLM) 상태 → tone + 라벨.
 *
 * 입력:
 *  - sidecarState: 'ok'|'checking'|'error'
 *  - llmReachable: null|true|false
 *
 * provider는 ollama 하나뿐이라 인자로 받지 않는다 — reachable === true일 때만 ok다.
 */
export function getLLMStatus({ sidecarState, llmReachable, model }) {
  const fullLabel = model ? `Ollama · ${model}` : "Ollama";

  if (sidecarState === "checking" || llmReachable === null) {
    return { tone: "pending", label: fullLabel, sub: "확인 중" };
  }
  if (sidecarState === "error") {
    return { tone: "warning", label: fullLabel, sub: "앱과 연결할 수 없어요" };
  }
  return llmReachable
    ? { tone: "ok", label: fullLabel }
    : { tone: "warning", label: fullLabel, sub: "Ollama가 실행되고 있지 않아요" };
}

/**
 * 보안 상태 → tone + 라벨.
 *
 * pendingCount > 0이면 "승인 대기"로 warning. AI 엔진 자체가 준비 전이면 pending.
 */
export function getSecurityStatus({ pendingCount, llmRunning }) {
  if (pendingCount > 0) {
    return {
      tone: "warning",
      label: "보안",
      sub: `승인 대기 ${pendingCount}건`,
    };
  }
  if (!llmRunning) {
    return { tone: "pending", label: "보안", sub: "AI 엔진이 준비되면 활성화돼요" };
  }
  return { tone: "ok", label: "보안", sub: "안전하게 보호 중" };
}
