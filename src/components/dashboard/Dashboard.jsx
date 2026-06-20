/**
 * Dashboard.jsx — 작업 요약 중심 대시보드.
 *
 * 핵심 역할:
 *   1) 진행 중 / 완료 작업 요약 (감사 로그 기반 집계)
 *   2) OpenClaw 게이트웨이 상태 (가장 큰 카드 — 비개발자가 가장 먼저 봐야 함)
 *   3) 보안 상태 한눈에 보기 (마스킹/차단 통계)
 *   4) 최근 활동 타임라인
 *
 * 의도적으로 제거한 것:
 *   - 인라인 에이전트 채팅 (→ "대화" 페이지로 이동)
 *   - 메일/엑셀/문서 빠른시작 카드 (deprecated 모듈)
 *   - LLM/Gmail/Telegram 개별 카드 (Settings에서 관리 — 대시보드는 핵심 신호만)
 */
import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  Activity,
  Bot,
  CheckCircle2,
  Clock,
  Shield,
  ShieldAlert,
  ArrowRight,
  MessageCircle,
  Copy,
  Sparkles,
  RefreshCw,
} from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { StatusBanner } from "@/components/ui/status";
import { getOpenClawStatus } from "@/lib/statusTokens";
import { cn } from "@/lib/utils";
import useAppStore from "@/store/appStore";
import useStatusStore from "@/store/statusStore";
import {
  getCommandAuditLogs,
  getCommandAuditStats,
  securityStats,
  telegramStatus,
} from "@/lib/api";

// ── 작업 요약 카드 (실행중/완료) ──────────────────────────────────────────────

function SummaryCard({ icon: Icon, label, value, hint, accent, loading, onClick }) {
  return (
    <Card
      className={cn(
        "transition-all",
        onClick && "cursor-pointer hover:border-primary/40 hover:shadow-sm"
      )}
      onClick={onClick}
    >
      <CardContent className="flex items-start gap-4 p-5">
        <div className={cn("flex h-10 w-10 shrink-0 items-center justify-center rounded-lg", accent)}>
          <Icon className="h-5 w-5" />
        </div>
        <div className="flex-1 min-w-0">
          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            {label}
          </p>
          {loading ? (
            <div className="mt-1.5 h-7 w-16 animate-pulse rounded bg-muted" />
          ) : (
            <p className="mt-0.5 text-2xl font-bold">{value}</p>
          )}
          {hint && <p className="mt-0.5 text-xs text-muted-foreground">{hint}</p>}
        </div>
      </CardContent>
    </Card>
  );
}

// ── OpenClaw 상태 배너 (가장 강조되는 영역) ────────────────────────────────────
//
// 통일된 톤 시스템 사용 — getOpenClawStatus가 반환한 tone/label을 그대로 표시.
// 어디서든 동일하게 "준비됨/문제 있음/확인 중" 어휘로 표시된다.
//
function OpenClawBanner({ state, port, message, onConfigure }) {
  const { tone } = getOpenClawStatus(state);

  if (tone === "ok") {
    return (
      <StatusBanner
        tone="ok"
        icon={Bot}
        title="OpenClaw 준비됨"
        description="AI 명령을 받을 준비가 됐어요."
      />
    );
  }

  if (tone === "pending") {
    return (
      <StatusBanner
        tone="pending"
        icon={Bot}
        title="OpenClaw 확인 중"
        description="현재 상태를 확인하고 있어요..."
      />
    );
  }

  // warning — 설치 안 됨 또는 실행 안 됨
  // 사용자 친화적 메시지만 표시, 기술적 message 필드는 숨김 (필요 시 위저드에서 확인)
  return (
    <StatusBanner
      tone="warning"
      icon={Bot}
      title="OpenClaw 준비가 필요해요"
      description="비개발자도 따라할 수 있는 단계별 설치 가이드를 준비했어요."
      actions={
        <Button size="sm" onClick={onConfigure}>
          지금 자동 설치
          <ArrowRight className="ml-1.5 h-3.5 w-3.5" />
        </Button>
      }
    />
  );
}

// ── 보안 상태 카드 ────────────────────────────────────────────────────────────

function SecurityStatusCard({ stats, loading, onClick }) {
  const masked = stats?.masking?.total ?? 0;
  const blocked = stats?.blocked_count?.total ?? 0;

  return (
    <Card
      className="cursor-pointer transition-all hover:border-primary/40 hover:shadow-sm"
      onClick={onClick}
    >
      <CardHeader className="pb-2">
        <div className="flex items-center gap-2">
          <Shield className="h-4 w-4 text-primary" />
          <CardTitle className="text-sm">보안 활동</CardTitle>
        </div>
      </CardHeader>
      <CardContent className="space-y-2">
        {loading ? (
          <div className="space-y-2">
            <div className="h-3 w-32 animate-pulse rounded bg-muted" />
            <div className="h-3 w-24 animate-pulse rounded bg-muted" />
          </div>
        ) : (
          <>
            <div className="flex items-center justify-between text-sm">
              <span className="text-muted-foreground">자동 마스킹</span>
              <span className="font-semibold">{masked.toLocaleString()}건</span>
            </div>
            <div className="flex items-center justify-between text-sm">
              <span className="text-muted-foreground">차단된 명령</span>
              <span className="font-semibold">{blocked.toLocaleString()}건</span>
            </div>
            <p className="pt-1 text-xs text-muted-foreground">
              민감정보는 AI에 전달되기 전에 자동 마스킹됩니다.
            </p>
          </>
        )}
      </CardContent>
    </Card>
  );
}

// ── 최근 활동 타임라인 ────────────────────────────────────────────────────────

function RecentActivity({ logs, loading }) {
  if (loading) {
    return (
      <div className="space-y-2">
        {[0, 1, 2].map((n) => (
          <Card key={n}>
            <CardContent className="py-3">
              <div className="h-3 w-2/3 animate-pulse rounded bg-muted" />
            </CardContent>
          </Card>
        ))}
      </div>
    );
  }

  if (!logs || logs.length === 0) {
    return (
      <Card>
        <CardContent className="py-10 text-center text-sm text-muted-foreground">
          <Clock className="mx-auto mb-2 h-7 w-7 opacity-30" />
          아직 활동 기록이 없습니다.
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-2">
      {logs.map((log) => {
        const cls = (log.classification || "").toLowerCase();
        const isApproved = log.approved === true || cls === "safe";
        const isRejected = log.approved === false || cls === "denied";
        const StatusIcon = isApproved ? CheckCircle2 : isRejected ? ShieldAlert : Clock;
        const statusColor = isApproved
          ? "text-green-500"
          : isRejected
          ? "text-red-500"
          : "text-amber-500";

        return (
          <Card key={log.id ?? `${log.timestamp}-${log.command}`}>
            <CardContent className="flex items-center gap-3 py-3">
              <StatusIcon className={cn("h-4 w-4 shrink-0", statusColor)} />
              <div className="flex-1 min-w-0">
                <p className="truncate text-sm font-medium">
                  {log.command ?? log.action ?? "-"}
                </p>
                {log.source && (
                  <p className="text-xs text-muted-foreground">
                    {log.source} · {cls.toUpperCase() || "처리됨"}
                  </p>
                )}
              </div>
              <span className="shrink-0 text-xs text-muted-foreground">
                {log.timestamp ? new Date(log.timestamp).toLocaleTimeString("ko-KR") : ""}
              </span>
            </CardContent>
          </Card>
        );
      })}
    </div>
  );
}

// ── 첫 명령 가이드 카드 ──────────────────────────────────────────────────────
//
// 활동 0건 + OpenClaw 정상 + 텔레그램 봇 정보가 있을 때만 표시.
// 비개발자가 "다음에 뭘 해야 하나?"를 모르는 상태를 해결한다.
//

function FirstCommandGuide({ botUsername, onGoToConversations, onGoToMessenger }) {
  const [copied, setCopied] = useState(false);
  const example = "A1:C3 범위 읽어줘";

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(example);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // 권한 없거나 비secure context — 무시 (사용자에게 input 박스로 보여줌)
    }
  };

  const deepLink = botUsername ? `https://t.me/${botUsername}` : null;

  return (
    <Card className="border-primary/30 bg-gradient-to-br from-primary/5 to-background">
      <CardContent className="flex items-start gap-4 p-5">
        <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-full bg-primary/10">
          <Sparkles className="h-6 w-6 text-primary" />
        </div>
        <div className="flex-1 min-w-0">
          <p className="font-semibold">첫 명령을 보내볼까요?</p>
          <p className="mt-0.5 text-sm text-muted-foreground">
            텔레그램에서 봇에게 메시지를 보내면 결과가 여기 대시보드에 기록됩니다.
          </p>

          <div className="mt-3 flex flex-wrap items-center gap-2">
            {deepLink ? (
              <a
                href={deepLink}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground hover:bg-primary/90"
              >
                <MessageCircle className="h-3.5 w-3.5" />
                @{botUsername} 열기
              </a>
            ) : (
              <Button size="sm" variant="outline" onClick={onGoToMessenger}>
                <MessageCircle className="mr-1.5 h-3.5 w-3.5" />
                텔레그램 봇 설정
              </Button>
            )}

            <button
              type="button"
              onClick={handleCopy}
              className="inline-flex items-center gap-1.5 rounded-md border border-border bg-background px-3 py-1.5 text-xs font-medium hover:bg-muted"
            >
              <Copy className="h-3.5 w-3.5" />
              {copied ? "복사됨!" : `예시 명령 "${example}" 복사`}
            </button>

            <Button size="sm" variant="ghost" onClick={onGoToConversations}>
              대화 화면 보기
              <ArrowRight className="ml-1 h-3.5 w-3.5" />
            </Button>
          </div>

          <div className="mt-3 rounded-md border border-dashed border-border bg-muted/30 p-3 text-xs text-muted-foreground">
            <p className="font-medium text-foreground">다음 명령도 시도해 보세요</p>
            <ul className="mt-1.5 space-y-0.5">
              <li>• "A1:C3 범위 읽어줘"</li>
              <li>• "B2:D2에 이름,수량,금액 입력"</li>
              <li>• "A열 20보다 큰 값 빨간색으로 칠해줘"</li>
            </ul>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

// ── 메인 ──────────────────────────────────────────────────────────────────────

export default function Dashboard() {
  const setCurrentPage = useAppStore((s) => s.setCurrentPage);
  // 중앙 status store에서 OpenClaw 모듈 상태 구독 — App의 useStatusPoller가 자동 갱신.
  // Dashboard는 더 이상 자체 openclawStatus fetch를 하지 않는다.
  const ocModule = useStatusStore((s) => s.modules.openclaw);
  // 기존 코드 호환을 위해 ocStatus 동등 객체 변환 (state/port/message)
  const ocStatus = useMemo(
    () => ({
      state: ocModule.running
        ? "running"
        : ocModule.state === "unknown"
        ? "checking"
        : "stopped",
      port: ocModule.port ?? 18789,
      message: ocModule.message,
    }),
    [ocModule]
  );

  const [loading, setLoading] = useState(true);
  const [stats, setStats] = useState(null);
  const [recentLogs, setRecentLogs] = useState([]);
  const [secStats, setSecStats] = useState(null);
  const [botUsername, setBotUsername] = useState(null);

  // OpenClaw 상태는 중앙 store가 담당 → 여기선 audit/security/telegram만 fetch.
  const loadData = useCallback(async () => {
    setLoading(true);
    const [statsResult, logsResult, secResult, tgResult] = await Promise.allSettled([
      getCommandAuditStats(),
      getCommandAuditLogs(5, 0),
      securityStats(),
      telegramStatus(),
    ]);

    setStats(statsResult.status === "fulfilled" ? statsResult.value : null);
    setRecentLogs(
      logsResult.status === "fulfilled" ? logsResult.value?.logs ?? [] : []
    );
    setSecStats(secResult.status === "fulfilled" ? secResult.value : null);
    if (tgResult.status === "fulfilled") {
      // bot_username 또는 username 필드 둘 다 대응
      const u = tgResult.value?.bot_username ?? tgResult.value?.username ?? null;
      setBotUsername(u);
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    loadData();
  }, [loadData]);

  // 작업 요약 — confirm은 "승인 대기"로 간주
  // sidecar가 confirm_pending을 직접 노출하면 그 값을 우선 사용 (StatusBar와 동일 산식 보장).
  // 없으면 derive 산식으로 graceful fallback.
  const totalCommands = stats?.total ?? 0;
  const completed = (stats?.safe ?? 0) + (stats?.confirm_approved ?? 0);
  const derivedPending =
    (stats?.confirm ?? 0) - (stats?.confirm_approved ?? 0) - (stats?.confirm_rejected ?? 0);
  const pending = stats?.confirm_pending ?? Math.max(0, derivedPending);
  const blocked = (stats?.denied ?? 0) + (stats?.confirm_rejected ?? 0);

  // 첫 명령 가이드 표시 조건: 활동 0건 + OpenClaw 정상
  const showFirstCommandGuide =
    !loading && totalCommands === 0 && ocStatus.state === "running";

  // 승인 대기 카드 클릭 시 — 실행 기록 페이지로 이동하면서 confirm_pending 필터 적용
  const goToPending = () => {
    if (typeof window !== "undefined") window.__privateClaw_auditFilter = "confirm_pending";
    setCurrentPage("audit");
  };

  return (
    <div className="mx-auto max-w-[1280px] space-y-6">
      {/* 헤더 */}
      <div className="flex items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold">대시보드</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            작업 요약 및 시스템 핵심 상태
          </p>
        </div>
        <Button
          variant="outline"
          size="sm"
          onClick={loadData}
          disabled={loading}
          title="대시보드 새로고침"
        >
          <RefreshCw className={cn("mr-1.5 h-3.5 w-3.5", loading && "animate-spin")} />
          새로고침
        </Button>
      </div>

      {/* OpenClaw 상태 — 비개발자가 가장 먼저 봐야 할 영역.
          미설치 시 App 레벨 prompt를 즉시 재개해 1-click 설치 흐름으로 진입. */}
      <OpenClawBanner
        state={ocStatus.state}
        port={ocStatus.port}
        message={ocStatus.message}
        onConfigure={() =>
          window.dispatchEvent(new CustomEvent("private-claw:open-openclaw-install"))
        }
      />

      {/* 첫 명령 가이드 — 활동 0건일 때만 */}
      {showFirstCommandGuide && (
        <FirstCommandGuide
          botUsername={botUsername}
          onGoToConversations={() => setCurrentPage("conversations")}
          onGoToMessenger={() => setCurrentPage("messenger_settings")}
        />
      )}

      {/* 작업 요약 — 실행중/완료/차단 */}
      <div>
        <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-muted-foreground">
          작업 요약
        </h2>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <SummaryCard
            icon={Activity}
            label="전체 명령"
            value={totalCommands.toLocaleString()}
            hint="누적 처리 건수"
            accent="bg-primary/10 text-primary"
            loading={loading}
            onClick={() => setCurrentPage("audit")}
          />
          <SummaryCard
            icon={Clock}
            label="승인 대기"
            value={Math.max(0, pending).toLocaleString()}
            hint="사용자 확인 필요"
            accent="bg-amber-100 text-amber-600"
            loading={loading}
            onClick={goToPending}
          />
          <SummaryCard
            icon={CheckCircle2}
            label="완료"
            value={completed.toLocaleString()}
            hint="자동 실행 + 승인"
            accent="bg-green-100 text-green-600"
            loading={loading}
            onClick={() => setCurrentPage("audit")}
          />
          <SummaryCard
            icon={ShieldAlert}
            label="차단"
            value={blocked.toLocaleString()}
            hint="보안 정책 위반"
            accent="bg-red-100 text-red-600"
            loading={loading}
            onClick={() => setCurrentPage("security")}
          />
        </div>
      </div>

      {/* 보안 + 최근 활동 (2열) */}
      <div className="grid gap-4 lg:grid-cols-3">
        {/* 보안 — 1열 */}
        <div className="lg:col-span-1">
          <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-muted-foreground">
            보안
          </h2>
          <SecurityStatusCard
            stats={secStats}
            loading={loading}
            onClick={() => setCurrentPage("security")}
          />
        </div>

        {/* 최근 활동 — 2열 */}
        <div className="lg:col-span-2">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
              최근 활동
            </h2>
            <button
              onClick={() => setCurrentPage("audit")}
              className="text-xs font-medium text-primary hover:underline"
            >
              전체 보기
            </button>
          </div>
          <RecentActivity logs={recentLogs} loading={loading} />
        </div>
      </div>
    </div>
  );
}
