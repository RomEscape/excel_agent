/**
 * ConversationHistoryPage — 대화 목록 (와이어프레임 229:3237 · 229:3678).
 *
 * 사이드바 `대화목록`이 여기로 온다. 지난 대화를 `요일별` / `파일별`로 훑고,
 * 카드를 누르면 그 대화가 채팅 패널로 열린다.
 *
 * 같은 폴더의 `ConversationsPage`(메신저 채널 모니터링)와 다른 화면이다.
 * 그쪽은 "메신저로 들어온 명령"을 보는 곳이라 와이어프레임에 없고,
 * 페이지 키 `messenger_monitor`로 남아 `Cmd/Ctrl+K`로 들어간다.
 *
 * 상태는 갖되 도메인 로직은 갖지 않는다 — 그룹핑은 `lib/conversationGroups.js`,
 * 세션 수집·열기는 `lib/chatManager.js`가 맡는다.
 */
import React, { useEffect, useMemo, useState } from "react";
import { CalendarDays, ChevronDown, FileText, MessageCircle } from "lucide-react";

import { cn } from "@/lib/utils";
import { sessionTitle } from "@/lib/chatSessions";
import { loadSession, refreshSessions } from "@/lib/chatManager";
import {
  CONVERSATION_VIEWS,
  CONVERSATION_VIEW_LABELS,
  groupSessions,
  hasFileInfo,
} from "@/lib/conversationGroups";
import useChatStore from "@/store/chatStore";

const VIEW_ICONS = { day: CalendarDays, file: FileText };

/** 상단 우측 보기 토글 — 와이어프레임 Frame 311, 85×44 두 칸. */
function ViewToggle({ view, onChange }) {
  return (
    <div className="flex gap-1 rounded-lg border border-border bg-card p-1" role="radiogroup" aria-label="대화 목록 보기">
      {CONVERSATION_VIEWS.map((v) => {
        const Icon = VIEW_ICONS[v];
        const active = view === v;
        return (
          <button
            key={v}
            type="button"
            role="radio"
            aria-checked={active}
            onClick={() => onChange(v)}
            className={cn(
              "flex h-9 items-center gap-1.5 rounded-md px-3.5 text-sm transition-colors",
              active
                ? "bg-accent font-medium text-primary"
                : "text-ink-faint hover:bg-accent/50 hover:text-foreground"
            )}
          >
            <Icon className="h-4 w-4" />
            {CONVERSATION_VIEW_LABELS[v]}
          </button>
        );
      })}
    </div>
  );
}

/**
 * 대화 카드 한 장.
 *
 * 와이어프레임의 카드에는 대상 파일 · 툴 진행 스텝 · 결과 문장이 함께 있는데,
 * 목록 API(`list_sessions`)는 `{session_id, last_message_at, message_count,
 * preview}`만 돌려준다. 없는 값을 지어내지 않고 아는 것만 그린다 —
 * 나머지는 카드를 눌러 대화를 열면 스레드에 그대로 있다.
 */
function ConversationCard({ session, onOpen }) {
  const at = session.last_message_at ? new Date(session.last_message_at) : null;
  const time =
    at && !Number.isNaN(at.getTime())
      ? at.toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit" })
      : "";

  return (
    <button
      type="button"
      onClick={() => onOpen(session.session_id)}
      className="flex w-full flex-col gap-2 rounded-xl border border-border bg-card p-5 text-left transition-colors hover:border-primary/40 hover:bg-accent/30"
    >
      <div className="flex items-center gap-2">
        <MessageCircle className="h-4 w-4 text-primary" />
        <span className="text-sm font-medium text-primary">{time || "시간 미상"}</span>
      </div>
      <p className="text-base font-medium text-foreground">{sessionTitle(session, 120)}</p>
      <p className="text-sm text-ink-subtle">
        메시지 {Number(session.message_count ?? 0).toLocaleString()}개
      </p>
    </button>
  );
}

export default function ConversationHistoryPage() {
  const sessions = useChatStore((s) => s.sessions);
  const sessionsAvailable = useChatStore((s) => s.sessionsAvailable);
  const sessionsLoading = useChatStore((s) => s.sessionsLoading);
  const setPanelOpen = useChatStore((s) => s.setPanelOpen);

  const [view, setView] = useState("day");

  useEffect(() => {
    refreshSessions();
  }, []);

  const groups = useMemo(() => groupSessions(sessions, view), [sessions, view]);
  const fileInfoAvailable = useMemo(() => hasFileInfo(sessions), [sessions]);

  // 대화를 열면 패널이 같이 떠야 한다 — 안 그러면 목록만 바뀌고 화면에
  // 아무 변화가 없어서 클릭이 먹지 않은 것처럼 보인다.
  const handleOpen = (sid) => {
    loadSession(sid);
    setPanelOpen(true);
  };

  const showFileNotice = view === "file" && !fileInfoAvailable && sessions.length > 0;

  return (
    <div className="mx-auto max-w-[1280px] space-y-6">
      <div className="flex items-center justify-between gap-4">
        <div className="flex items-center gap-2">
          <MessageCircle className="h-6 w-6 text-foreground" />
          <h1 className="text-2xl font-semibold">대화 목록</h1>
        </div>
        <ViewToggle view={view} onChange={setView} />
      </div>

      {!sessionsAvailable ? (
        <div className="rounded-xl border border-dashed border-border py-24 text-center">
          <p className="text-base text-foreground">대화 기록을 사용할 수 없습니다.</p>
          <p className="mt-1 text-xs text-ink-faint">사이드카가 실행 중인지 확인해 주세요.</p>
        </div>
      ) : sessions.length === 0 ? (
        <div className="rounded-xl border border-dashed border-border py-24 text-center">
          <p className="text-base text-foreground">
            {sessionsLoading ? "불러오는 중..." : "아직 대화가 없습니다"}
          </p>
          <p className="mt-1 text-xs text-ink-faint">
            워크스페이스에서 김대리에게 말을 걸면 여기에 쌓입니다.
          </p>
        </div>
      ) : showFileNotice ? (
        <div className="rounded-xl border border-dashed border-border py-24 text-center">
          <p className="text-base text-foreground">파일별로 묶을 정보가 아직 없습니다</p>
          <p className="mx-auto mt-1 max-w-md text-xs text-ink-faint">
            대화가 어떤 파일을 다뤘는지 기록되기 시작하면 여기에서 파일별로 볼 수
            있습니다. 그때까지는 요일별 보기를 사용해 주세요.
          </p>
        </div>
      ) : (
        <div className="space-y-7">
          {groups.map((group) => (
            <section key={group.key || group.label} className="space-y-3">
              <div className="flex items-center gap-1.5">
                <h2 className="text-lg font-medium text-ink-body">{group.label}</h2>
                <ChevronDown className="h-4 w-4 text-ink-disabled" />
              </div>
              <div className="space-y-3">
                {group.items.map((s) => (
                  <ConversationCard key={s.session_id} session={s} onOpen={handleOpen} />
                ))}
              </div>
            </section>
          ))}
        </div>
      )}
    </div>
  );
}
