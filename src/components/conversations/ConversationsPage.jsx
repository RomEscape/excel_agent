/**
 * ConversationsPage.jsx — 메신저 채널 모니터링.
 *
 * R2 변경:
 *   - 앱 내 에이전트 채팅(AgentChatPane) 제거 → WorkspacePage 사이드 패널로 이동
 *   - 미연결 메신저는 "+ 채널 추가" 그룹으로 묶어 시각 노이즈 감소
 *
 * 좌측: 연결된 채널 리스트 + 미연결 그룹 (Telegram/Slack/Discord).
 * 우측: 선택된 채널의 연결 상태 + 최근 명령 감사 기록.
 */
import React, { useCallback, useEffect, useState } from "react";
import {
  MessageCircle,
  MessagesSquare,
  AlertCircle,
  Plus,
  Settings as SettingsIcon,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { StatusBadge } from "@/components/ui/status";
import { STATUS_TONE, getMessengerStatus } from "@/lib/statusTokens";
import EmptyState from "@/components/ui/empty-state";
import { cn } from "@/lib/utils";
import useAppStore from "@/store/appStore";
import {
  telegramStatus,
  slackStatus,
  discordStatus,
  getCommandAuditLogs,
} from "@/lib/api";

const CHANNELS = [
  {
    id: "telegram",
    label: "텔레그램",
    description: "Telegram 봇 채널",
    icon: MessageCircle,
    accent: "text-blue-500",
    statusFn: telegramStatus,
  },
  {
    id: "slack",
    label: "슬랙",
    description: "Slack 워크스페이스",
    icon: MessagesSquare,
    accent: "text-[#4A154B]",
    statusFn: slackStatus,
  },
  {
    id: "discord",
    label: "디스코드",
    description: "Discord 길드",
    icon: MessagesSquare,
    accent: "text-[#5865F2]",
    statusFn: discordStatus,
  },
];

/**
 * 채널 상태 → StatusBadge 톤/라벨로 변환.
 * 'connected' → ok / 'disconnected' → warning / 'pending' → pending.
 * MessengerSettings, StatusBar와 동일한 어휘 사용.
 */
function ChannelStatusBadge({ state }) {
  if (state === "connected") {
    return <StatusBadge tone="ok">연결됨</StatusBadge>;
  }
  if (state === "pending") {
    return <StatusBadge tone="pending">확인 중</StatusBadge>;
  }
  return <StatusBadge tone="warning">미연결</StatusBadge>;
}

// ── 채널 사이드 리스트 ────────────────────────────────────────────────────────

function ChannelList({ activeId, statuses, onSelect, onAddChannel }) {
  const connected = CHANNELS.filter((c) => statuses[c.id] === "connected");
  const disconnected = CHANNELS.filter((c) => statuses[c.id] !== "connected");

  return (
    <nav
      className="w-60 shrink-0 space-y-3 border-r border-border pr-3"
      aria-label="대화 채널"
    >
      <div>
        <h2 className="mb-2 px-3 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          연결된 채널
        </h2>
        {connected.length === 0 ? (
          <p className="px-3 text-xs text-muted-foreground">아직 연결된 채널이 없습니다.</p>
        ) : (
          <div className="space-y-1">
            {connected.map((c) => (
              <ChannelRow
                key={c.id}
                channel={c}
                status="connected"
                active={activeId === c.id}
                onSelect={onSelect}
              />
            ))}
          </div>
        )}
      </div>

      {disconnected.length > 0 && (
        <div>
          <h2 className="mb-2 px-3 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            추가 가능
          </h2>
          <div className="space-y-1">
            {disconnected.map((c) => (
              <ChannelRow
                key={c.id}
                channel={c}
                status="disconnected"
                active={activeId === c.id}
                onSelect={onSelect}
                muted
              />
            ))}
          </div>
          <button
            type="button"
            onClick={onAddChannel}
            className="mt-2 flex w-full items-center gap-2 rounded-md border border-dashed border-border px-3 py-2 text-xs font-medium text-muted-foreground hover:bg-muted/40 hover:text-foreground"
          >
            <Plus className="h-3.5 w-3.5" />
            채널 추가 (설정으로 이동)
          </button>
        </div>
      )}
    </nav>
  );
}

function ChannelRow({ channel, status, active, onSelect, muted }) {
  const { id, label, description, icon: Icon, accent } = channel;
  return (
    <button
      onClick={() => onSelect(id)}
      className={cn(
        "flex w-full items-start gap-3 rounded-md px-3 py-2 text-left transition-colors",
        active ? "bg-primary/10" : "hover:bg-muted",
        muted && "opacity-70"
      )}
      aria-current={active ? "page" : undefined}
    >
      <Icon className={cn("mt-0.5 h-4 w-4 shrink-0", accent)} />
      <div className="flex-1 min-w-0">
        <div className="flex items-center justify-between gap-2">
          <span className={cn("text-sm font-medium", active && "text-primary")}>
            {label}
          </span>
          <span
            className={cn(
              "h-1.5 w-1.5 shrink-0 rounded-full",
              status === "connected"
                ? STATUS_TONE.ok.dot
                : STATUS_TONE.warning.dot
            )}
            aria-label={status === "connected" ? "연결됨" : "미연결"}
          />
        </div>
        <p className="text-xs text-muted-foreground truncate">{description}</p>
      </div>
    </button>
  );
}

// ── 메신저 채널 활동 패널 ────────────────────────────────────────────────────

function MessengerActivityPane({ channelId, channelLabel, status }) {
  const setCurrentPage = useAppStore((s) => s.setCurrentPage);
  const [logs, setLogs] = useState(null);
  const [loadingLogs, setLoadingLogs] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoadingLogs(true);
    getCommandAuditLogs(50, 0)
      .then((result) => {
        if (cancelled) return;
        const all = result?.logs ?? [];
        // source 필드로 채널별 필터 (sidecar가 source=telegram|slack|discord 기록한다는 가정)
        const filtered = all.filter((log) => {
          const src = (log.source || log.channel || "").toLowerCase();
          return src.includes(channelId);
        });
        setLogs(filtered);
      })
      .catch(() => !cancelled && setLogs([]))
      .finally(() => !cancelled && setLoadingLogs(false));
    return () => {
      cancelled = true;
    };
  }, [channelId]);

  if (status !== "connected") {
    return (
      <Card className="border-dashed">
        <CardContent className="p-0">
          <EmptyState
            icon={AlertCircle}
            title={`${channelLabel} 미연결`}
            description="메신저 봇 토큰을 입력하면 이 채널의 활동을 확인할 수 있습니다."
            action={{
              label: "메신저 설정으로 이동",
              onClick: () => setCurrentPage("messenger_settings"),
            }}
          />
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-3">
      <div className="rounded-md border border-border bg-muted/30 px-4 py-3 text-xs text-muted-foreground">
        실제 대화는 {channelLabel}에서 이루어지며, 이 화면에서는 봇이 처리한 최근 명령 기록을 확인할 수 있습니다.
      </div>

      {loadingLogs ? (
        <div className="space-y-2">
          {[0, 1, 2].map((n) => (
            <Card key={n}>
              <CardContent className="py-3">
                <div className="h-3 w-2/3 animate-pulse rounded bg-muted" />
              </CardContent>
            </Card>
          ))}
        </div>
      ) : !logs || logs.length === 0 ? (
        <Card>
          <CardContent className="p-0">
            <EmptyState
              icon={MessagesSquare}
              title="아직 처리된 명령이 없습니다."
              description={`${channelLabel}에서 봇에게 메시지를 보내면 여기에 기록됩니다.`}
            />
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-2">
          {logs.map((log) => (
            <Card key={log.id ?? `${log.timestamp}-${log.command}`}>
              <CardContent className="py-3">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium">{log.command ?? log.action ?? "-"}</p>
                    {(log.reason || log.target) && (
                      <p className="mt-0.5 text-xs text-muted-foreground">{log.reason ?? log.target}</p>
                    )}
                  </div>
                  <div className="flex shrink-0 flex-col items-end gap-1">
                    {log.classification && (
                      <span
                        className={cn(
                          "rounded-full border px-2 py-0.5 text-[10px] font-medium",
                          log.classification === "safe" && "border-green-200 bg-green-50 text-green-700",
                          log.classification === "confirm" && "border-amber-200 bg-amber-50 text-amber-700",
                          log.classification === "denied" && "border-red-200 bg-red-50 text-red-700"
                        )}
                      >
                        {log.classification.toUpperCase()}
                      </span>
                    )}
                    <span className="text-[10px] text-muted-foreground">
                      {log.timestamp ? new Date(log.timestamp).toLocaleTimeString("ko-KR") : ""}
                    </span>
                  </div>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}

// ── 메인 컴포넌트 ────────────────────────────────────────────────────────────

export default function ConversationsPage() {
  const setCurrentPage = useAppStore((s) => s.setCurrentPage);

  const [activeChannel, setActiveChannel] = useState("telegram");
  const [statuses, setStatuses] = useState({
    telegram: "disconnected",
    slack: "disconnected",
    discord: "disconnected",
  });

  // 메신저 연결 상태 폴링
  const refreshStatuses = useCallback(async () => {
    const checks = await Promise.allSettled([
      telegramStatus(),
      slackStatus(),
      discordStatus(),
    ]);
    setStatuses({
      telegram: checks[0].status === "fulfilled" && checks[0].value?.running ? "connected" : "disconnected",
      slack: checks[1].status === "fulfilled" && checks[1].value?.running ? "connected" : "disconnected",
      discord: checks[2].status === "fulfilled" && checks[2].value?.running ? "connected" : "disconnected",
    });
  }, []);

  useEffect(() => {
    refreshStatuses();
    const timer = setInterval(refreshStatuses, 10000);
    return () => clearInterval(timer);
  }, [refreshStatuses]);

  const channelMeta = CHANNELS.find((c) => c.id === activeChannel) ?? CHANNELS[0];
  const channelStatus = statuses[activeChannel] ?? "disconnected";

  return (
    <div className="flex h-full flex-col gap-4">
      {/* 헤더 */}
      <div>
        <h1 className="text-2xl font-bold">대화</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          메신저로 들어온 명령과 봇이 처리한 결과를 한곳에서 확인합니다.
        </p>
      </div>

      {/* 본문: 좌측 채널 리스트 + 우측 활동 영역 */}
      <div className="flex flex-1 gap-6 overflow-hidden">
        <ChannelList
          activeId={activeChannel}
          statuses={statuses}
          onSelect={setActiveChannel}
          onAddChannel={() => setCurrentPage("messenger_settings")}
        />

        <section className="flex flex-1 flex-col overflow-hidden">
          {/* 활성 채널 헤더 */}
          <div className="mb-3 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <channelMeta.icon className={cn("h-5 w-5", channelMeta.accent)} />
              <h2 className="text-lg font-semibold">{channelMeta.label}</h2>
              <ChannelStatusBadge state={channelStatus} />
            </div>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setCurrentPage("messenger_settings")}
            >
              <SettingsIcon className="mr-1.5 h-3.5 w-3.5" />
              채널 설정
            </Button>
          </div>

          <div className="flex-1 overflow-y-auto">
            <MessengerActivityPane
              channelId={activeChannel}
              channelLabel={channelMeta.label}
              status={channelStatus}
            />
          </div>
        </section>
      </div>
    </div>
  );
}
