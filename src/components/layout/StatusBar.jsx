import React, { useEffect, useCallback, useState, useRef } from "react";
import {
  Bot,
  Cpu,
  ShieldCheck,
  Command,
  PanelLeftClose,
  PanelLeftOpen,
  Keyboard,
  MessageCircle,
  MessagesSquare,
  Hash,
} from "lucide-react";
import useAppStore from "@/store/appStore";
import useStatusStore from "@/store/statusStore";
import {
  healthCheck,
  getLLMSettings,
  securityStats,
  getCommandAuditLogs,
  telegramStatus,
  slackStatus,
  discordStatus,
} from "@/lib/api";
import { cn } from "@/lib/utils";
import {
  STATUS_TONE,
  getOpenClawStatus,
  getLLMStatus,
  getMessengerStatus,
  getSecurityStatus,
  MESSENGER_LABELS,
} from "@/lib/statusTokens";

// 페이지 키 → 한국어 라벨 (좌측 breadcrumb용)
const PAGE_LABELS = {
  dashboard: "대시보드",
  workspace: "워크스페이스",
  conversations: "대화",
  settings: "설정",
  credentials: "설정 / 자격증명",
  audit: "설정 / 실행 기록",
  security: "설정 / 보안",
  permissions: "설정 / 에이전트 허용 범위",
  messenger_settings: "설정 / 메신저",
  guide: "설정 / OpenClaw 설치",
};

// 활성 메신저별 아이콘
const MESSENGER_ICONS = {
  telegram: MessageCircle,
  slack: Hash,
  discord: MessagesSquare,
};

// 활성 메신저별 상태 fetch 함수
const MESSENGER_STATUS_FNS = {
  telegram: telegramStatus,
  slack: slackStatus,
  discord: discordStatus,
};

/**
 * 호버 200ms 후 등장하는 단순 popover.
 * 외부 의존성 없이 mouse enter/leave + setTimeout 으로만 구현.
 *
 * 통일된 톤 시스템 사용 — 도메인 매퍼가 반환한 tone을 그대로 받는다.
 */
function StatusSegment({ icon: Icon, label, tone, tooltip, badge, onClick }) {
  const [open, setOpen] = useState(false);
  const timerRef = useRef(null);
  const t = STATUS_TONE[tone] ?? STATUS_TONE.pending;

  const handleEnter = () => {
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => setOpen(true), 200);
  };
  const handleLeave = () => {
    if (timerRef.current) clearTimeout(timerRef.current);
    setOpen(false);
  };

  useEffect(() => () => timerRef.current && clearTimeout(timerRef.current), []);

  return (
    <div
      className="relative"
      onMouseEnter={handleEnter}
      onMouseLeave={handleLeave}
    >
      <button
        type="button"
        onClick={onClick}
        className="flex items-center gap-1.5 rounded px-2 py-1 hover:bg-muted/60 transition-colors"
      >
        <span className="relative flex items-center">
          <Icon className={cn("h-3.5 w-3.5", t.text)} />
          <span
            className={cn(
              "absolute -right-1 -top-1 h-1.5 w-1.5 rounded-full",
              t.dot,
              tone === "pending" && "animate-pulse"
            )}
            aria-hidden="true"
          />
        </span>
        <span className="text-foreground/80">{label}</span>
        {badge != null && badge > 0 && (
          <span className="ml-1 inline-flex h-4 min-w-[16px] items-center justify-center rounded-full bg-amber-500 px-1 text-[10px] font-bold text-white">
            {badge > 9 ? "9+" : badge}
          </span>
        )}
      </button>

      {open && tooltip && (
        <div className="absolute right-0 top-full z-50 mt-1 w-64 rounded-md border border-border bg-popover p-3 text-xs shadow-md">
          {tooltip}
        </div>
      )}
    </div>
  );
}

/** 톤별 한국어 상태 라벨 — tooltip 본문에서 일관되게 사용. */
function toneLabel(tone) {
  return STATUS_TONE[tone]?.label ?? "확인 중";
}

export default function StatusBar() {
  const sidecarStatus = useAppStore((s) => s.sidecarStatus);
  const llmConfig = useAppStore((s) => s.llmConfig);
  const llmReachable = useAppStore((s) => s.llmReachable);
  const setSidecarStatus = useAppStore((s) => s.setSidecarStatus);
  const setLLMConfig = useAppStore((s) => s.setLLMConfig);
  const setLLMReachable = useAppStore((s) => s.setLLMReachable);
  const currentPage = useAppStore((s) => s.currentPage);
  const setCurrentPage = useAppStore((s) => s.setCurrentPage);
  const setPendingApproval = useAppStore((s) => s.setPendingApproval);
  const sidebarCollapsed = useAppStore((s) => s.sidebarCollapsed);
  const setSidebarCollapsed = useAppStore((s) => s.setSidebarCollapsed);
  const selectedMessenger = useAppStore((s) => s.selectedMessenger);
  // OpenClaw 상태는 중앙 statusStore에서 — App의 useStatusPoller가 자동 갱신.
  const ocModule = useStatusStore((s) => s.modules.openclaw);
  // 기존 코드 호환 (getOpenClawStatus가 받는 string state)
  const ocState = ocModule.running
    ? "running"
    : ocModule.state === "unknown"
    ? "checking"
    : "stopped";

  // 보안 통계 (마스킹/차단 합산)
  const [secStats, setSecStats] = useState(null);
  // 승인 대기 건수
  const [pendingCount, setPendingCount] = useState(0);
  // 가장 오래된 pending audit (배지 클릭 시 ApprovalDialog로 변환)
  const [oldestPending, setOldestPending] = useState(null);
  // 활성 메신저 상태 — null = 미확인 (pending), 객체 = 응답 받음
  const [messengerState, setMessengerState] = useState(null);

  // sidecar 헬스 + LLM 도달성
  const checkHealth = useCallback(async () => {
    try {
      const result = await healthCheck();
      setSidecarStatus({ state: "ok", message: "연결됨" });
      if (llmConfig.provider === "ollama") {
        setLLMReachable(result?.ollama_status === "connected");
      } else {
        // Claude API는 sidecar 응답만으로 판단 — reachable=null 대신 true로
        setLLMReachable(true);
      }
    } catch {
      setSidecarStatus({ state: "error", message: "연결 오류" });
      setLLMReachable(false);
    }
  }, [setSidecarStatus, setLLMReachable, llmConfig.provider]);

  const loadLLMConfig = useCallback(async () => {
    try {
      const config = await getLLMSettings();
      if (config && config.provider) setLLMConfig(config);
    } catch {
      // ignore
    }
  }, [setLLMConfig]);

  // OpenClaw 상태는 중앙 useStatusPoller가 폴링 — 여기선 별도 처리 불필요.

  // 보안 통계 + 승인 대기 폴링 (30s)
  const refreshSecurity = useCallback(async () => {
    try {
      const [secRes, logsRes] = await Promise.allSettled([
        securityStats(),
        getCommandAuditLogs(50, 0),
      ]);
      if (secRes.status === "fulfilled") setSecStats(secRes.value);

      // pending = grade=CONFIRM AND approved is null
      if (logsRes.status === "fulfilled") {
        const all = logsRes.value?.logs ?? [];
        const pending = all.filter(
          (l) => (l.grade === "CONFIRM" || l.classification === "confirm") && l.approved == null
        );
        setPendingCount(pending.length);
        // 가장 오래된 = 시간순 마지막 (logs는 최신순으로 옴)
        setOldestPending(pending.length ? pending[pending.length - 1] : null);
      }
    } catch {
      // 조용히 실패
    }
  }, []);

  // 활성 메신저 상태 폴링 (30s) — selectedMessenger가 바뀌면 즉시 재조회
  const refreshMessenger = useCallback(async () => {
    const statusFn = MESSENGER_STATUS_FNS[selectedMessenger];
    if (!statusFn) {
      setMessengerState({ running: false, configured: false, unknown: true });
      return;
    }
    try {
      const s = await statusFn();
      setMessengerState({
        running: !!s?.running,
        configured: !!(s?.configured ?? s?.bot_username),
        unknown: false,
      });
    } catch {
      setMessengerState({ running: false, configured: false, unknown: true });
    }
  }, [selectedMessenger]);

  useEffect(() => {
    checkHealth();
    loadLLMConfig();
    refreshSecurity();
    refreshMessenger();

    const t1 = setInterval(checkHealth, 30_000);
    const t3 = setInterval(refreshSecurity, 30_000);
    const t4 = setInterval(refreshMessenger, 30_000);
    return () => {
      clearInterval(t1);
      clearInterval(t3);
      clearInterval(t4);
    };
  }, [checkHealth, loadLLMConfig, refreshSecurity, refreshMessenger]);

  // ── 톤/라벨 도출 (모두 통합 매퍼 사용) ────────────────────────────────────
  const oc = getOpenClawStatus(ocState);
  const llm = getLLMStatus({
    sidecarState: sidecarStatus.state,
    llmReachable,
    provider: llmConfig.provider,
    model: llmConfig.model,
  });
  const messenger = getMessengerStatus({
    running: messengerState?.running,
    configured: messengerState?.configured,
    unknown: messengerState === null || messengerState.unknown,
    name: MESSENGER_LABELS[selectedMessenger] ?? "메신저",
  });
  const sec = getSecurityStatus({
    pendingCount,
    ocRunning: ocModule.running,
  });

  const ocTooltip = (
    <div className="space-y-1">
      <p className="font-semibold text-foreground">OpenClaw 게이트웨이</p>
      <p className="text-muted-foreground">상태: {toneLabel(oc.tone)}</p>
      {ocModule.port && <p className="text-muted-foreground">포트: {ocModule.port}</p>}
      {ocModule.version && (
        <p className="text-[11px] text-muted-foreground">버전: {ocModule.version}</p>
      )}
      {ocModule.message && (
        <p className="text-[11px] text-muted-foreground">{ocModule.message}</p>
      )}
      <p className="pt-1 text-[11px] text-muted-foreground">클릭 → 설정 / OpenClaw 설치</p>
    </div>
  );

  const masked = secStats?.masking?.total ?? 0;
  const blocked = secStats?.blocked_count?.total ?? 0;
  const lastBlockedAt = secStats?.last_blocked_at;
  const secTooltip = (
    <div className="space-y-1">
      <p className="font-semibold text-foreground">보안 (Zero-Trust)</p>
      <p className="text-muted-foreground">상태: {toneLabel(sec.tone)}</p>
      <p className="text-muted-foreground">자동 마스킹: {Number(masked).toLocaleString?.() ?? masked}건</p>
      <p className="text-muted-foreground">차단된 명령: {Number(blocked).toLocaleString?.() ?? blocked}건</p>
      {lastBlockedAt && (
        <p className="text-[11px] text-muted-foreground">
          마지막 차단: {new Date(lastBlockedAt).toLocaleString?.("ko-KR") ?? lastBlockedAt}
        </p>
      )}
      {pendingCount > 0 && (
        <p className="pt-1 text-amber-600 dark:text-amber-400">
          승인 대기 {pendingCount}건 — 클릭하면 검토합니다
        </p>
      )}
    </div>
  );

  const llmTooltip = (
    <div className="space-y-1">
      <p className="font-semibold text-foreground">AI 엔진</p>
      <p className="text-muted-foreground">{llm.label}</p>
      <p className="text-muted-foreground">상태: {toneLabel(llm.tone)}</p>
      {llm.sub && <p className="text-[11px] text-muted-foreground">{llm.sub}</p>}
      <p className="pt-1 text-[11px] text-muted-foreground">클릭 → 설정 / 일반</p>
    </div>
  );

  const MessengerIcon = MESSENGER_ICONS[selectedMessenger] ?? MessageCircle;
  const messengerTooltip = (
    <div className="space-y-1">
      <p className="font-semibold text-foreground">{messenger.label} 봇</p>
      <p className="text-muted-foreground">상태: {toneLabel(messenger.tone)}</p>
      {messenger.sub && (
        <p className="text-[11px] text-muted-foreground">{messenger.sub}</p>
      )}
      <p className="pt-1 text-[11px] text-muted-foreground">
        클릭 → {messenger.tone === "ok" ? "대화 페이지" : "설정 / 메신저"}
      </p>
    </div>
  );

  // ── 클릭 핸들러 ──────────────────────────────────────────────────────────
  const handleSecurityClick = () => {
    // 승인 대기가 있으면 ApprovalDialog로 변환해 자동 오픈.
    if (pendingCount > 0 && oldestPending) {
      setPendingApproval({
        approval_id: oldestPending.approval_id ?? oldestPending.id,
        audit_id: oldestPending.id,
        command: oldestPending.command,
        reason: oldestPending.reason || "메신저로부터 승인 요청이 들어왔습니다.",
        summary: oldestPending.reason,
        source: oldestPending.source,
        tool_name: oldestPending.tool_name,
        tool_display_name: oldestPending.tool_display_name,
        session_id: oldestPending.session_id,
        danger: oldestPending.danger ?? oldestPending.is_dangerous,
      });
      return;
    }
    setCurrentPage("security");
  };

  const handleMessengerClick = () => {
    if (messenger.tone === "ok") {
      setCurrentPage("conversations");
    } else {
      setCurrentPage("messenger_settings");
    }
  };

  // ⌘K 트리거 — Layout이 들으므로 window event 발행
  const triggerCmdK = () => {
    window.dispatchEvent(new CustomEvent("private-claw:open-cmdk"));
  };

  const pageLabel = PAGE_LABELS[currentPage] ?? "ajou-ai";

  return (
    <header className="flex h-10 items-center justify-between gap-3 border-b bg-background px-4 text-xs">
      {/* 좌측: 페이지 라벨 (breadcrumb 대용) */}
      <div className="flex min-w-0 items-center gap-2 text-muted-foreground">
        <span className="truncate font-medium text-foreground/80">{pageLabel}</span>
      </div>

      {/* 우측: 4-segment status hub + ⌘K hint */}
      <div className="flex items-center gap-1">
        <StatusSegment
          icon={Bot}
          label="OpenClaw"
          tone={oc.tone}
          tooltip={ocTooltip}
          onClick={() => setCurrentPage("guide")}
        />
        <StatusSegment
          icon={ShieldCheck}
          label="보안"
          tone={sec.tone}
          badge={pendingCount}
          tooltip={secTooltip}
          onClick={handleSecurityClick}
        />
        <StatusSegment
          icon={Cpu}
          label={llm.label}
          tone={llm.tone}
          tooltip={llmTooltip}
          onClick={() => setCurrentPage("settings")}
        />
        <StatusSegment
          icon={MessengerIcon}
          label={messenger.label}
          tone={messenger.tone}
          tooltip={messengerTooltip}
          onClick={handleMessengerClick}
        />

        {/* ⌘K hint — 항상 노출 */}
        <button
          type="button"
          onClick={triggerCmdK}
          className="ml-2 flex items-center gap-1 rounded border border-border bg-muted/30 px-1.5 py-0.5 text-[11px] text-muted-foreground hover:bg-muted hover:text-foreground transition-colors"
          title="명령 팔레트 (Cmd/Ctrl+K)"
        >
          <Command className="h-3 w-3" />
          <span>K</span>
        </button>

        {/* Sidebar collapse 토글 (Cmd/Ctrl+B) */}
        <button
          type="button"
          onClick={() => setSidebarCollapsed((v) => !v)}
          className="flex items-center gap-1 rounded px-1.5 py-1 text-muted-foreground hover:bg-muted hover:text-foreground transition-colors"
          title={
            sidebarCollapsed
              ? "사이드바 펼치기 (Cmd/Ctrl+B)"
              : "사이드바 접기 (Cmd/Ctrl+B)"
          }
          aria-label={sidebarCollapsed ? "사이드바 펼치기" : "사이드바 접기"}
        >
          {sidebarCollapsed ? (
            <PanelLeftOpen className="h-3.5 w-3.5" />
          ) : (
            <PanelLeftClose className="h-3.5 w-3.5" />
          )}
        </button>

        {/* 단축키 도움말 (?) */}
        <button
          type="button"
          onClick={() =>
            window.dispatchEvent(new CustomEvent("private-claw:open-shortcut-help"))
          }
          className="flex items-center gap-1 rounded px-1.5 py-1 text-muted-foreground hover:bg-muted hover:text-foreground transition-colors"
          title="단축키 도움말 (?)"
          aria-label="단축키 도움말 열기"
        >
          <Keyboard className="h-3.5 w-3.5" />
        </button>
      </div>
    </header>
  );
}
