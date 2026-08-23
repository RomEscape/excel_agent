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
 * 치수는 Figma export 그대로다. 헷갈리기 쉬운 지점:
 *   - 페이지 제목은 **22px SemiBold**다(작업 기록의 24px와 다르다).
 *   - 그룹 머리(`8월 18일(화)`)는 **16px Medium**이다.
 *   - 카드 안 명령문은 **16px Medium #3D443C**이지 본문 검정이 아니다.
 *   - 탭 칩은 `#D0EEC6` 지면 + `#F9FDF7` 테두리 + 초록 글로우다.
 *
 * **카드에 파일명·툴 스텝·결과 문장이 없다.** 목록 API(`list_sessions`)가
 * `{session_id, last_message_at, message_count, preview}`만 돌려주기 때문이다.
 * 없는 값을 지어내지 않고, 프레임의 자리 배치와 타이포만 그대로 두고
 * 아는 값을 그 자리에 넣었다.
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

/**
 * 보기 토글 — Figma: 바깥 radius 26, 1px #CACFC7, padding 4.
 * 선택 칩은 radius 24, #D0EEC6 지면 + 1px #F9FDF7 + 초록 글로우, 글자 14px Medium #249000.
 */
function ViewToggle({ view, onChange }) {
  return (
    <div
      className="inline-flex items-center rounded-[26px] border border-ink-disabled bg-card p-1"
      role="radiogroup"
      aria-label="대화 목록 보기"
    >
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
            style={
              active ? { filter: "drop-shadow(0px 0px 3px rgba(101, 193, 15, 0.5))" } : undefined
            }
            className={cn(
              "flex items-center gap-1 rounded-[24px] border py-3 pl-3 pr-4 text-sm leading-5 transition-colors",
              active
                ? "border-secondary bg-brand-soft font-medium text-primary"
                : "border-transparent text-ink-faint hover:text-foreground"
            )}
          >
            <Icon className="h-4 w-4 shrink-0" />
            {CONVERSATION_VIEW_LABELS[v]}
          </button>
        );
      })}
    </div>
  );
}

/**
 * 대화 카드 — Figma: radius 12, 1px #E1E6DF, px20 py12, 내부 gap 12.
 *
 * 프레임의 상단 줄은 엑셀 아이콘 + 파일명(14px #2DB400)인데 세션에 파일 정보가
 * 없어서 그 자리를 시간이 대신한다. 자리와 타이포는 그대로 둔다.
 */
function ConversationCard({ session, onOpen }) {
  const at = session.last_message_at ? new Date(session.last_message_at) : null;
  const time =
    at && !Number.isNaN(at.getTime())
      ? at.toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit" })
      : "시간 미상";

  return (
    <button
      type="button"
      onClick={() => onOpen(session.session_id)}
      className="flex w-full flex-col gap-3 rounded-xl border border-border bg-card px-5 py-3 text-left transition-colors hover:border-primary/40 hover:bg-accent/30"
    >
      {/* 상단 — Figma: 아이콘 20 + 14px #2DB400, gap 6 */}
      <div className="flex items-center gap-1.5">
        <MessageCircle className="h-5 w-5 shrink-0 text-brand" />
        <span className="truncate text-sm leading-5 text-brand">{time}</span>
      </div>

      <div className="flex flex-col gap-1">
        {/* 명령문 — 16px Medium #3D443C */}
        <p className="text-base font-medium leading-[22px] tracking-[-0.64px] text-ink-body">
          {sessionTitle(session, 120)}
        </p>
        {/* 요약 줄 — 12px #B2B9B0 */}
        <p className="text-xs leading-4 text-ink-faint">
          메시지 {Number(session.message_count ?? 0).toLocaleString()}개
        </p>
      </div>
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
    /* Figma: 섹션 간 gap 40 */
    <div className="mx-auto flex max-w-[1280px] flex-col gap-10 pb-6 pt-3">
      <div className="flex items-center justify-between gap-4">
        {/* 22px SemiBold, leading 30 */}
        <h1 className="text-[22px] font-semibold leading-[30px] tracking-[-0.22px] text-foreground">
          대화 목록
        </h1>
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
        <div className="flex flex-col gap-10">
          {groups.map((group) => (
            /* Figma: 그룹 안 gap 12 */
            <section key={group.key || group.label} className="flex flex-col gap-3">
              <div className="flex items-center gap-3">
                {/* 그룹 머리 — 16px Medium #0C1909 */}
                <h2 className="text-base font-medium leading-[22px] tracking-[-0.64px] text-foreground">
                  {group.label}
                </h2>
                <ChevronDown className="h-5 w-5 text-ink-subtle" />
              </div>
              <div className="flex flex-col gap-3">
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
