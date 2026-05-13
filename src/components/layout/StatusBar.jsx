import React, { useEffect, useCallback, useState, useRef } from "react";
import {
  Bot,
  Cpu,
  ShieldCheck,
  Command,
  PanelLeftClose,
  PanelLeftOpen,
  Keyboard,
} from "lucide-react";
import useAppStore from "@/store/appStore";
import {
  healthCheck,
  getLLMSettings,
  openclawStatus,
  securityStats,
  getCommandAuditLogs,
} from "@/lib/api";
import { cn } from "@/lib/utils";

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

/**
 * 호버 200ms 후 등장하는 단순 popover.
 * 외부 의존성 없이 mouse enter/leave + setTimeout 으로만 구현.
 */
function StatusSegment({ icon: Icon, label, color, tooltip, badge, onClick }) {
  const [open, setOpen] = useState(false);
  const timerRef = useRef(null);

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
          <Icon className={cn("h-3.5 w-3.5", color)} />
          <span
            className={cn(
              "absolute -right-1 -top-1 h-1.5 w-1.5 rounded-full",
              color === "text-green-500"
                ? "bg-green-500"
                : color === "text-amber-500"
                ? "bg-amber-500"
                : "bg-destructive"
            )}
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

export default function StatusBar() {
  const sidecarStatus = useAppStore((s) => s.sidecarStatus);
  const llmConfig = useAppStore((s) => s.llmConfig);
  const llmReachable = useAppStore((s) => s.llmReachable);
  const setSidecarStatus = useAppStore((s) => s.setSidecarStatus);
  const setLLMConfig = useAppStore((s) => s.setLLMConfig);
  const setLLMReachable = useAppStore((s) => s.setLLMReachable);
  const ocStatus = useAppStore((s) => s.openclawStatus);
  const setOpenClawStatus = useAppStore((s) => s.setOpenClawStatus);
  const currentPage = useAppStore((s) => s.currentPage);
  const setCurrentPage = useAppStore((s) => s.setCurrentPage);
  const setPendingApproval = useAppStore((s) => s.setPendingApproval);
  const sidebarCollapsed = useAppStore((s) => s.sidebarCollapsed);
  const setSidebarCollapsed = useAppStore((s) => s.setSidebarCollapsed);

  // 보안 통계 (마스킹/차단 합산)
  const [secStats, setSecStats] = useState(null);
  // 승인 대기 건수
  const [pendingCount, setPendingCount] = useState(0);
  // 가장 오래된 pending audit (배지 클릭 시 ApprovalDialog로 변환)
  const [oldestPending, setOldestPending] = useState(null);

  // sidecar 헬스 + LLM 도달성
  const checkHealth = useCallback(async () => {
    try {
      const result = await healthCheck();
      setSidecarStatus({ state: "ok", message: "연결됨" });
      if (llmConfig.provider === "ollama") {
        setLLMReachable(result?.ollama_status === "connected");
      } else {
        setLLMReachable(null);
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

  // OpenClaw 상태 폴링 (StatusBar에서도 직접 확인 — 페이지 이동과 무관하게 항상 갱신)
  const refreshOpenClaw = useCallback(async () => {
    try {
      const oc = await openclawStatus();
      setOpenClawStatus({
        state: oc?.state ?? "error",
        message: oc?.message ?? "",
        port: oc?.port,
      });
    } catch {
      setOpenClawStatus({ state: "error", message: "OpenClaw 상태 확인 실패" });
    }
  }, [setOpenClawStatus]);

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

  useEffect(() => {
    checkHealth();
    loadLLMConfig();
    refreshOpenClaw();
    refreshSecurity();

    const t1 = setInterval(checkHealth, 30_000);
    const t2 = setInterval(refreshOpenClaw, 30_000);
    const t3 = setInterval(refreshSecurity, 30_000);
    return () => {
      clearInterval(t1);
      clearInterval(t2);
      clearInterval(t3);
    };
  }, [checkHealth, loadLLMConfig, refreshOpenClaw, refreshSecurity]);

  // ── 색상/툴팁 도출 ──────────────────────────────────────────────────────
  const ocColor =
    ocStatus.state === "running"
      ? "text-green-500"
      : ocStatus.state === "checking"
      ? "text-amber-500"
      : "text-destructive";

  const ocTooltip = (
    <div className="space-y-1">
      <p className="font-semibold text-foreground">OpenClaw 게이트웨이</p>
      <p className="text-muted-foreground">
        상태: {ocStatus.state === "running" ? "정상" : ocStatus.state === "checking" ? "확인 중" : "오프라인"}
      </p>
      {ocStatus.port && <p className="text-muted-foreground">포트: {ocStatus.port}</p>}
      {ocStatus.message && (
        <p className="text-[11px] text-muted-foreground">{ocStatus.message}</p>
      )}
      <p className="pt-1 text-[11px] text-muted-foreground">클릭 → 설정 / OpenClaw 설치</p>
    </div>
  );

  const masked = secStats?.masking?.total ?? 0;
  const blocked = secStats?.blocked_count?.total ?? 0;
  const lastBlockedAt = secStats?.last_blocked_at;

  const secColor =
    pendingCount > 0
      ? "text-amber-500"
      : ocStatus.state === "running"
      ? "text-green-500"
      : "text-amber-500";

  const secTooltip = (
    <div className="space-y-1">
      <p className="font-semibold text-foreground">보안 (Zero-Trust)</p>
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

  const engineName = llmConfig.provider === "claude" ? "Claude API" : "Ollama";
  const modelLabel = llmConfig.model || "";
  const providerLabel = modelLabel ? `${engineName} · ${modelLabel}` : engineName;
  const llmColor =
    sidecarStatus.state === "error"
      ? "text-destructive"
      : llmReachable === true
      ? "text-green-500"
      : llmReachable === false
      ? "text-destructive"
      : "text-amber-500";

  const llmTooltip = (
    <div className="space-y-1">
      <p className="font-semibold text-foreground">AI 엔진</p>
      <p className="text-muted-foreground">{providerLabel}</p>
      <p className="text-muted-foreground">
        {sidecarStatus.state === "error"
          ? "백그라운드 서비스와 연결할 수 없습니다"
          : llmReachable === true
          ? "정상 연결됨"
          : llmReachable === false
          ? "연결할 수 없습니다 — 실행 중인지 확인해 주세요"
          : "연결 상태 확인 중"}
      </p>
      <p className="pt-1 text-[11px] text-muted-foreground">클릭 → 설정 / 일반</p>
    </div>
  );

  // ── 클릭 핸들러 ──────────────────────────────────────────────────────────
  const handleSecurityClick = () => {
    // 승인 대기가 있으면 ApprovalDialog로 변환해 자동 오픈.
    // danger 추정은 ApprovalDialog 내부의 toolName 기반 fallback에 위임 (sidecar가 명시 grade를 주면 그 값 우선).
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

      {/* 우측: 3-segment status hub + ⌘K hint */}
      <div className="flex items-center gap-1">
        <StatusSegment
          icon={Bot}
          label="OpenClaw"
          color={ocColor}
          tooltip={ocTooltip}
          onClick={() => setCurrentPage("guide")}
        />
        <StatusSegment
          icon={ShieldCheck}
          label="보안"
          color={secColor}
          badge={pendingCount}
          tooltip={secTooltip}
          onClick={handleSecurityClick}
        />
        <StatusSegment
          icon={Cpu}
          label={providerLabel}
          color={llmColor}
          tooltip={llmTooltip}
          onClick={() => setCurrentPage("settings")}
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
