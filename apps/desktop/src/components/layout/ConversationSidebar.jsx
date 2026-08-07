/**
 * ConversationSidebar — 목업(desktop-app) 2~5번 화면의 왼쪽 열.
 *
 * 펼침(w-72) 구성(위→아래):
 *   1) 브랜드 + 사용자 칩 — "로컬 사용자 / ● Local Protected"
 *   2) + 새 대화
 *   3) 대화 목록          — 오늘 / 어제 / 지난 7일 / 이전 그룹
 *   4) 내비게이션 푸터    — 채팅 · 대시보드 · 워크스페이스 · 대화 모니터링 · 설정
 *
 * 4번은 목업에 없다. 목업은 채팅 표면만 그렸기 때문에 나머지 페이지로 가는 길이
 * 아예 없는데, 앱에는 그 페이지들이 실재하므로 진입 경로를 잃을 수 없다.
 * 목업의 상단 구성을 그대로 두고 아래에만 붙였다.
 *
 * 접힘(w-16)은 아이콘 레일이다 — 대화 목록만 숨고 내비게이션은 남는다.
 * 통째로 사라지게 하면 접는 순간 모든 페이지 진입 경로를 잃고, 돌아올 길이
 * StatusBar 구석 버튼과 Cmd+B뿐이라 사용자가 길을 잃는다.
 *
 * 상태는 갖지 않는다 — chatStore/appStore를 구독하고 chatManager를 호출만 한다.
 */
import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  Bot,
  FolderOpen,
  LayoutDashboard,
  Lock,
  MessageCircle,
  MessagesSquare,
  Plus,
  Settings as SettingsIcon,
  Trash2,
} from "lucide-react";
import { BrandMark, BrandWordmark } from "@/components/ui/logo";
import AlertDialog from "@/components/ui/dialog";
import { cn } from "@/lib/utils";
import { version } from "../../../package.json";
import { groupSessions, sessionTitle } from "@/lib/chatSessions";
import { getOllamaStatus } from "@/lib/statusTokens";
import {
  refreshSessions,
  loadSession,
  deleteSession,
  startNewSession,
} from "@/lib/chatManager";
import useAppStore from "@/store/appStore";
import useChatStore from "@/store/chatStore";
import useStatusStore from "@/store/statusStore";

/**
 * 내비게이션 — gated 항목은 AI 엔진이 준비돼야 열린다.
 *
 * "채팅"이 첫 항목인 이유: 대화 목록에서 세션을 고르거나 새 대화를 시작하는
 * 경로만 있으면, 대시보드에 갔다가 *보던 대화로* 돌아올 방법이 없다.
 */
const NAV_ITEMS = [
  { id: "chat", label: "채팅", icon: Bot, gated: false },
  { id: "dashboard", label: "대시보드", icon: LayoutDashboard, gated: false },
  { id: "workspace", label: "워크스페이스", icon: FolderOpen, gated: true },
  { id: "conversations", label: "대화 모니터링", icon: MessagesSquare, gated: true },
];

const SETTINGS_PAGES = new Set([
  "settings",
  "credentials",
  "audit",
  "security",
  "permissions",
  "messenger_settings",
  "guide",
]);

/**
 * 접힘 상태에서 200ms hover 후 뜨는 라벨 tooltip.
 * 아이콘만 남으면 라벨이 사라지므로 이게 유일한 이름 확인 수단이다.
 */
function RailTooltip({ label, gated }) {
  const [show, setShow] = useState(false);
  const timerRef = useRef(null);

  const handleEnter = () => {
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => setShow(true), 200);
  };
  const handleLeave = () => {
    if (timerRef.current) clearTimeout(timerRef.current);
    setShow(false);
  };
  useEffect(() => () => timerRef.current && clearTimeout(timerRef.current), []);

  return (
    <span className="absolute inset-0" onMouseEnter={handleEnter} onMouseLeave={handleLeave}>
      {show && (
        <span
          role="tooltip"
          className="pointer-events-none absolute left-full top-1/2 z-50 ml-2 flex -translate-y-1/2 flex-col gap-0.5 whitespace-nowrap rounded-md border border-border bg-popover px-2 py-1 text-xs text-popover-foreground shadow-md"
        >
          <span className="font-medium">{label}</span>
          {gated && (
            <span className="flex items-center gap-1 text-[10px] text-amber-600 dark:text-amber-400">
              <Lock className="h-2.5 w-2.5" />
              AI 엔진이 준비되면 사용할 수 있어요
            </span>
          )}
        </span>
      )}
    </span>
  );
}

/** 내비게이션 버튼 한 개 — 펼침/접힘 두 모양을 모두 그린다. */
function NavButton({ icon: Icon, label, active, gated, collapsed, trailing, onClick }) {
  return (
    <div className="relative">
      <button
        type="button"
        onClick={onClick}
        aria-current={active ? "page" : undefined}
        aria-label={collapsed ? label : undefined}
        title={
          !collapsed && gated
            ? "AI 엔진이 준비되면 사용할 수 있어요 — 클릭해서 설치 가이드로 이동"
            : undefined
        }
        className={cn(
          "relative flex w-full items-center rounded-md text-xs font-medium transition-colors",
          collapsed ? "h-10 justify-center px-0" : "gap-2.5 px-3 py-2",
          active
            ? "bg-accent text-accent-foreground"
            : "text-muted-foreground hover:bg-accent/60 hover:text-foreground",
          gated && "opacity-60"
        )}
      >
        <Icon className="h-3.5 w-3.5 shrink-0" />
        {!collapsed && (
          <>
            <span className="flex-1 text-left">{label}</span>
            {gated && (
              <Lock className="h-3 w-3 shrink-0 text-amber-500" aria-label="AI 엔진 준비 필요" />
            )}
            {trailing}
          </>
        )}
        {collapsed && gated && (
          <span
            className="absolute -right-0.5 -top-0.5 flex h-3.5 w-3.5 items-center justify-center rounded-full bg-amber-100 ring-2 ring-amber-300 dark:bg-amber-900/60 dark:ring-amber-700"
            aria-hidden="true"
          >
            <Lock className="h-2 w-2 text-amber-600 dark:text-amber-300" />
          </span>
        )}
      </button>
      {collapsed && <RailTooltip label={label} gated={gated} />}
    </div>
  );
}

/** 대화 한 줄 — hover 시에만 삭제 버튼이 나타난다. */
function SessionRow({ session, active, onOpen, onAskDelete }) {
  return (
    <div
      role="button"
      tabIndex={0}
      onClick={() => onOpen(session.session_id)}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onOpen(session.session_id);
        }
      }}
      className={cn(
        "group flex items-center gap-2 rounded-lg px-2.5 py-2 text-xs transition-colors",
        active ? "bg-primary/15 text-foreground" : "text-foreground/80 hover:bg-accent"
      )}
    >
      <MessageCircle className="h-3 w-3 shrink-0 text-muted-foreground" />
      <span className="min-w-0 flex-1 truncate" title={sessionTitle(session, 200)}>
        {sessionTitle(session)}
      </span>
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation();
          onAskDelete(session);
        }}
        className="shrink-0 text-muted-foreground opacity-0 transition-opacity hover:text-destructive focus:opacity-100 group-hover:opacity-100"
        aria-label="이 대화 삭제"
        title="이 대화 삭제"
      >
        <Trash2 className="h-3 w-3" />
      </button>
    </div>
  );
}

export default function ConversationSidebar() {
  const currentPage = useAppStore((s) => s.currentPage);
  const setCurrentPage = useAppStore((s) => s.setCurrentPage);
  const activeSessionId = useAppStore((s) => s.activeSessionId);
  const collapsed = !!useAppStore((s) => s.sidebarCollapsed);

  const sessions = useChatStore((s) => s.sessions);
  const sessionsAvailable = useChatStore((s) => s.sessionsAvailable);
  const sessionsLoading = useChatStore((s) => s.sessionsLoading);

  const ollamaState = useStatusStore((s) => s.modules.ollama.state);
  const aiBlocked = getOllamaStatus(ollamaState).tone === "warning";

  const [confirmDelete, setConfirmDelete] = useState(null);

  // 사이드바가 살아 있는 동안 목록을 한 번 확보한다. 이후 갱신은
  // chatManager가 전송/삭제 시점에 알아서 한다.
  useEffect(() => {
    refreshSessions();
  }, []);

  // 그룹핑 기준 시각은 렌더마다 새로 만들지 않는다 — 매 렌더 new Date()면
  // 세션 목록이 그대로여도 groups 참조가 계속 바뀐다.
  const groups = useMemo(() => groupSessions(sessions, new Date()), [sessions]);

  const handleNav = (item) => {
    if (item.gated && aiBlocked) {
      window.dispatchEvent(new CustomEvent("officeclaw:open-ai-setup"));
      return;
    }
    setCurrentPage(item.id);
  };

  const handleOpenSession = (sid) => {
    setCurrentPage("chat");
    loadSession(sid);
  };

  const handleNewChat = () => {
    setCurrentPage("chat");
    startNewSession();
  };

  const settingsActive = SETTINGS_PAGES.has(currentPage);

  return (
    <aside
      className={cn(
        "flex h-full shrink-0 flex-col border-r border-border bg-muted/40 transition-[width] duration-200 ease-out",
        collapsed ? "w-16" : "w-72"
      )}
      data-collapsed={collapsed}
      aria-label="대화 목록"
    >
      {/* 브랜드 + 사용자 칩 */}
      <div
        className={cn(
          "flex flex-col",
          collapsed ? "items-center gap-3 px-2 pb-3 pt-4" : "gap-4 px-5 pb-4 pt-5"
        )}
      >
        {collapsed ? (
          <BrandMark className="h-7 w-7 rounded-md" />
        ) : (
          <BrandWordmark className="h-7 w-auto self-start" />
        )}
        {!collapsed && (
          <div className="flex items-center gap-2.5">
            <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-primary text-xs font-bold text-primary-foreground">
              나
            </span>
            <span className="flex min-w-0 flex-col gap-0.5">
              <span className="text-xs font-bold text-foreground">로컬 사용자</span>
              <span className="flex items-center gap-1 text-xs text-primary">
                <Lock className="h-2.5 w-2.5" />
                Local Protected
              </span>
            </span>
          </div>
        )}
      </div>

      {/* 새 대화 */}
      <div className={cn(collapsed ? "px-2 pb-2" : "px-5 pb-3")}>
        <div className="relative">
          <button
            type="button"
            onClick={handleNewChat}
            aria-label={collapsed ? "새 대화" : undefined}
            className={cn(
              "flex w-full items-center justify-center rounded-lg border border-border bg-background font-bold text-foreground transition-colors hover:border-primary/50 hover:bg-accent",
              collapsed ? "h-10" : "gap-1.5 py-2.5 text-xs"
            )}
          >
            <Plus className="h-3.5 w-3.5" />
            {!collapsed && "새 대화"}
          </button>
          {collapsed && <RailTooltip label="새 대화" />}
        </div>
      </div>

      {/* 대화 목록 — 접힘 상태에서는 폭이 없어 의미가 없으므로 숨긴다.
          대신 아래 내비게이션의 "채팅"이 남아 있어 대화로 돌아갈 수 있다. */}
      {collapsed ? (
        <div className="min-h-0 flex-1" />
      ) : (
        <div className="min-h-0 flex-1 overflow-y-auto px-3 pb-2">
          {!sessionsAvailable ? (
            <p className="px-2 py-6 text-center text-xs text-muted-foreground">
              대화 기록을 사용할 수 없습니다.
            </p>
          ) : groups.length === 0 ? (
            <p className="px-2 py-6 text-center text-xs text-muted-foreground">
              {sessionsLoading ? "불러오는 중..." : "아직 대화가 없습니다."}
            </p>
          ) : (
            groups.map((group) => (
              <section key={group.label} className="mb-3">
                <h2 className="px-2.5 pb-1.5 pt-1 text-xs font-bold text-muted-foreground">
                  {group.label}
                </h2>
                <div className="flex flex-col gap-0.5">
                  {group.items.map((s) => (
                    <SessionRow
                      key={s.session_id}
                      session={s}
                      active={s.session_id === activeSessionId && currentPage === "chat"}
                      onOpen={handleOpenSession}
                      onAskDelete={setConfirmDelete}
                    />
                  ))}
                </div>
              </section>
            ))
          )}
        </div>
      )}

      {/* 내비게이션 — 목업에 없지만 앱에는 필요한 진입 경로. 접혀도 남는다. */}
      <nav className="border-t border-border p-2" aria-label="메인 메뉴">
        <ul className="flex flex-col gap-0.5">
          {NAV_ITEMS.map((item) => (
            <li key={item.id}>
              <NavButton
                icon={item.icon}
                label={item.label}
                active={currentPage === item.id}
                gated={item.gated && aiBlocked}
                collapsed={collapsed}
                onClick={() => handleNav(item)}
              />
            </li>
          ))}
          <li>
            <NavButton
              icon={SettingsIcon}
              label="설정"
              active={settingsActive}
              collapsed={collapsed}
              onClick={() => setCurrentPage("settings")}
              trailing={
                <span className="text-[10px] text-muted-foreground/60">v{version}</span>
              }
            />
          </li>
        </ul>
      </nav>

      <AlertDialog
        open={!!confirmDelete}
        title="대화 삭제"
        description={
          confirmDelete
            ? `"${sessionTitle(confirmDelete)}" 대화를 삭제하시겠습니까? 이 작업은 되돌릴 수 없습니다.`
            : ""
        }
        confirmLabel="삭제"
        confirmVariant="destructive"
        onConfirm={() => {
          const target = confirmDelete;
          setConfirmDelete(null);
          if (target) deleteSession(target.session_id);
        }}
        onCancel={() => setConfirmDelete(null)}
      />
    </aside>
  );
}
