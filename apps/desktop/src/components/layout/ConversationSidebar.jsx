/**
 * ConversationSidebar — 최종 와이어프레임의 왼쪽 열 240px (Frame 121).
 *
 * 펼침 구성(위→아래):
 *   1) 로고 워드마크 + 접기 아이콘
 *   2) 내비 4항목 — 대시보드 · 워크스페이스 · 작업 검색(확장) · 대화목록(확장)
 *   3) 푸터 2항목 — 도움말 · 환경 설정
 *
 * 구버전(PR #28)과 달라진 점:
 *   - 지면이 테마를 따라간다. 예전엔 라이트 모드에서도 사이드바만 다크였다.
 *   - 대화 목록이 상시 노출이 아니라 `대화목록` 항목의 chevron 확장 안으로 들어갔다.
 *   - `작업 검색`이 내비 항목으로 승격됐다 (기존엔 Cmd+K 명령 팔레트로만 있었다).
 *   - 내비에 `채팅` 항목이 없다 — 최종안에서 채팅은 페이지가 아니라 본문 위의
 *     패널이라(lib/chatPanel.js) 어느 페이지에서든 떠 있고, 홈 자체가 진입점이다.
 *     "보던 대화로 돌아올 경로"는 그래서 사라지지 않는다.
 *
 * 와이어프레임에 없지만 남긴 것: `대화목록`을 펼쳤을 때의 `+` 새 대화 버튼.
 * 최종안 사이드바에는 새 대화 진입점이 아예 없는데, 홈으로 나가야만 새 대화를
 * 시작할 수 있으면 대화 중에 새 주제를 꺼내는 동선이 끊긴다.
 *
 * 접힘(Cmd/Ctrl+B)은 통째 숨김이 아니라 64px 아이콘 레일이다 — 확장 목록만
 * 접히고 내비는 남는다. 통째로 없애면 접는 순간 모든 페이지 진입 경로를 잃는다.
 *
 * 상태는 갖지 않는다 — chatStore/appStore를 구독하고 chatManager를 호출만 한다.
 */
import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  ChevronDown,
  HelpCircle,
  LayoutDashboard,
  MessageCircle,
  PanelLeftClose,
  PanelLeftOpen,
  Plus,
  Settings as SettingsIcon,
  SquarePen,
  TextSearch,
  Trash2,
} from "lucide-react";
import { BrandMark, BrandWordmark } from "@/components/ui/logo";
import AlertDialog from "@/components/ui/dialog";
import { cn } from "@/lib/utils";
import { groupSessions, sessionTitle } from "@/lib/chatSessions";
import {
  refreshSessions,
  loadSession,
  deleteSession,
  startNewSession,
} from "@/lib/chatManager";
import useAppStore from "@/store/appStore";
import useChatStore from "@/store/chatStore";

/**
 * 내비게이션 — 와이어프레임 Frame 151의 4항목.
 *
 * 아이콘은 tabler 스펙에 lucide로 가장 가까운 것을 골랐다:
 *   layout-dashboard → LayoutDashboard · subtitles-edit → SquarePen
 *   input-search → TextSearch · message-circle → MessageCircle
 *
 * `expandable`인 항목은 클릭 시 페이지 이동이 아니라 아래 패널을 펼친다.
 */
const NAV_ITEMS = [
  { id: "dashboard", label: "대시보드", icon: LayoutDashboard },
  { id: "workspace", label: "워크스페이스", icon: SquarePen },
  { id: "search", label: "작업 검색", icon: TextSearch, expandable: true },
  { id: "conversations", label: "대화목록", icon: MessageCircle, expandable: true },
];

const FOOTER_ITEMS = [
  { id: "help", label: "도움말", icon: HelpCircle },
  { id: "settings", label: "환경 설정", icon: SettingsIcon },
];

const SETTINGS_PAGES = new Set([
  "settings",
  "credentials",
  "audit",
  "security",
  "permissions",
  "messenger_settings",
  "guide",
  "mobile_relay",
]);

/**
 * 접힘 상태에서 200ms hover 후 뜨는 라벨 tooltip.
 * 아이콘만 남으면 라벨이 사라지므로 이게 유일한 이름 확인 수단이다.
 */
function RailTooltip({ label }) {
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
          className="pointer-events-none absolute left-full top-1/2 z-50 ml-2 -translate-y-1/2 whitespace-nowrap rounded-md border border-border bg-popover px-2 py-1 text-xs font-medium text-popover-foreground shadow-md"
        >
          {label}
        </span>
      )}
    </span>
  );
}

/**
 * 내비 버튼 한 개 — 와이어프레임 기준 240×44.
 * 펼침/접힘 두 모양을 모두 그린다.
 */
function NavButton({ icon: Icon, label, active, collapsed, expanded, expandable, onClick }) {
  return (
    <div className="relative">
      <button
        type="button"
        onClick={onClick}
        aria-current={active ? "page" : undefined}
        aria-expanded={expandable ? expanded : undefined}
        aria-label={collapsed ? label : undefined}
        className={cn(
          "flex h-11 w-full items-center rounded-lg text-sm transition-colors",
          collapsed ? "justify-center px-0" : "gap-3 px-3",
          active
            ? "bg-accent font-semibold text-accent-foreground"
            : "font-medium text-foreground/75 hover:bg-accent/60 hover:text-foreground"
        )}
      >
        <Icon className="h-4 w-4 shrink-0" />
        {!collapsed && (
          <>
            <span className="flex-1 truncate text-left">{label}</span>
            {expandable && (
              <ChevronDown
                className={cn(
                  "h-4 w-4 shrink-0 text-muted-foreground transition-transform",
                  expanded && "rotate-180"
                )}
              />
            )}
          </>
        )}
      </button>
      {collapsed && <RailTooltip label={label} />}
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
        "group flex items-center gap-2 rounded-md px-2.5 py-1.5 text-xs transition-colors",
        active ? "bg-primary/15 text-foreground" : "text-foreground/75 hover:bg-accent"
      )}
    >
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
  const setSidebarCollapsed = useAppStore((s) => s.setSidebarCollapsed);

  const sessions = useChatStore((s) => s.sessions);
  const sessionsAvailable = useChatStore((s) => s.sessionsAvailable);
  const sessionsLoading = useChatStore((s) => s.sessionsLoading);
  const setPanelOpen = useChatStore((s) => s.setPanelOpen);

  // 확장형 내비 두 개는 서로 독립이다. 대화목록만 기본 펼침 — 검색은
  // 입력을 요구하므로 항상 열어두면 세로 공간만 먹는다.
  const [openSection, setOpenSection] = useState({ search: false, conversations: true });
  const [query, setQuery] = useState("");
  const [confirmDelete, setConfirmDelete] = useState(null);
  const searchRef = useRef(null);

  // 사이드바가 살아 있는 동안 목록을 한 번 확보한다. 이후 갱신은
  // chatManager가 전송/삭제 시점에 알아서 한다.
  useEffect(() => {
    refreshSessions();
  }, []);

  // 그룹핑 기준 시각은 렌더마다 새로 만들지 않는다 — 매 렌더 new Date()면
  // 세션 목록이 그대로여도 groups 참조가 계속 바뀐다.
  const groups = useMemo(() => groupSessions(sessions, new Date()), [sessions]);

  // 검색은 제목(=첫 사용자 메시지 미리보기) 대상. 서버 왕복 없이 이미 받아온
  // 목록에서 거른다 — 30개 상한이라 클라이언트 필터로 충분하다.
  const matches = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return [];
    return sessions.filter((s) => sessionTitle(s, 200).toLowerCase().includes(q));
  }, [sessions, query]);

  const toggleSection = (id) => {
    // 접혀 있으면 먼저 펼친다 — 64px 레일에서는 확장 패널을 그릴 자리가 없다.
    if (collapsed) setSidebarCollapsed(false);
    setOpenSection((prev) => ({ ...prev, [id]: !prev[id] }));
    if (id === "search") setTimeout(() => searchRef.current?.focus(), 0);
  };

  const handleNav = (item) => {
    if (item.expandable) {
      toggleSection(item.id);
      return;
    }
    setCurrentPage(item.id);
  };

  const handleFooter = (item) => {
    if (item.id === "help") {
      window.dispatchEvent(new CustomEvent("officeclaw:open-shortcut-help"));
      return;
    }
    setCurrentPage("settings");
  };

  // 대화를 열면 채팅 패널이 같이 떠야 한다 — 안 그러면 목록만 바뀌고
  // 화면에는 아무 변화가 없어서 클릭이 먹지 않은 것처럼 보인다.
  const handleOpenSession = (sid) => {
    loadSession(sid);
    setPanelOpen(true);
  };

  const handleNewChat = () => {
    startNewSession();
    setCurrentPage("chat");
    setPanelOpen(false);
  };

  const settingsActive = SETTINGS_PAGES.has(currentPage);
  const CollapseIcon = collapsed ? PanelLeftOpen : PanelLeftClose;

  return (
    <aside
      className={cn(
        "flex h-full shrink-0 flex-col border-r border-border bg-sidebar text-sidebar-foreground transition-[width] duration-200 ease-out",
        collapsed ? "w-16" : "w-60"
      )}
      data-collapsed={collapsed}
      aria-label="사이드바"
    >
      {/* 로고 + 접기 — 와이어프레임의 `아트보드 5 2` 126×36 + layout-sidebar-left-collapse */}
      <div
        className={cn(
          "flex shrink-0 items-center",
          collapsed ? "justify-center px-2 py-4" : "justify-between px-4 py-4"
        )}
      >
        {collapsed ? (
          <BrandMark className="h-7 w-7 rounded-md" />
        ) : (
          <BrandWordmark className="h-8 w-auto" />
        )}
        {!collapsed && (
          <button
            type="button"
            onClick={() => setSidebarCollapsed(true)}
            className="rounded-md p-1 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
            aria-label="사이드바 접기"
            title="사이드바 접기 (Cmd/Ctrl+B)"
          >
            <CollapseIcon className="h-5 w-5" />
          </button>
        )}
      </div>

      {collapsed && (
        <div className="flex justify-center pb-2">
          <button
            type="button"
            onClick={() => setSidebarCollapsed(false)}
            className="rounded-md p-1 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
            aria-label="사이드바 펼치기"
            title="사이드바 펼치기 (Cmd/Ctrl+B)"
          >
            <PanelLeftOpen className="h-5 w-5" />
          </button>
        </div>
      )}

      {/* 내비 + 확장 패널 */}
      <nav
        className={cn("min-h-0 flex-1 overflow-y-auto", collapsed ? "px-2" : "px-3")}
        aria-label="메인 메뉴"
      >
        <ul className="flex flex-col gap-0.5">
          {NAV_ITEMS.map((item) => (
            <li key={item.id}>
              <NavButton
                icon={item.icon}
                label={item.label}
                active={!item.expandable && currentPage === item.id}
                collapsed={collapsed}
                expandable={item.expandable}
                expanded={!!openSection[item.id]}
                onClick={() => handleNav(item)}
              />

              {/* 작업 검색 확장 — 이미 받아온 대화 목록을 제목으로 거른다. */}
              {item.id === "search" && !collapsed && openSection.search && (
                <div className="flex flex-col gap-1 pb-2 pl-3 pr-1 pt-1.5">
                  <input
                    ref={searchRef}
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    placeholder="대화 제목 검색"
                    className="h-8 rounded-md border border-border bg-background px-2.5 text-xs text-foreground placeholder:text-muted-foreground focus:border-primary focus:outline-none"
                  />
                  {query.trim() && (
                    <div className="flex flex-col gap-0.5 pt-1">
                      {matches.length === 0 ? (
                        <p className="px-2 py-2 text-xs text-muted-foreground">
                          일치하는 대화가 없습니다.
                        </p>
                      ) : (
                        matches.map((s) => (
                          <SessionRow
                            key={s.session_id}
                            session={s}
                            active={s.session_id === activeSessionId}
                            onOpen={handleOpenSession}
                            onAskDelete={setConfirmDelete}
                          />
                        ))
                      )}
                    </div>
                  )}
                  <button
                    type="button"
                    onClick={() =>
                      window.dispatchEvent(new CustomEvent("officeclaw:open-cmdk"))
                    }
                    className="px-2 py-1 text-left text-xs text-muted-foreground transition-colors hover:text-foreground"
                  >
                    전체 명령 검색 (Cmd/Ctrl+K)
                  </button>
                </div>
              )}

              {/* 대화목록 확장 — 오늘 / 어제 / 지난 7일 그룹 */}
              {item.id === "conversations" && !collapsed && openSection.conversations && (
                <div className="flex flex-col gap-1 pb-2 pl-3 pr-1 pt-1.5">
                  <button
                    type="button"
                    onClick={handleNewChat}
                    className="flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs font-semibold text-primary transition-colors hover:bg-accent"
                  >
                    <Plus className="h-3.5 w-3.5" />새 대화
                  </button>

                  {!sessionsAvailable ? (
                    <p className="px-2 py-3 text-xs text-muted-foreground">
                      대화 기록을 사용할 수 없습니다.
                    </p>
                  ) : groups.length === 0 ? (
                    <p className="px-2 py-3 text-xs text-muted-foreground">
                      {sessionsLoading ? "불러오는 중..." : "아직 대화가 없습니다."}
                    </p>
                  ) : (
                    groups.map((group) => (
                      <section key={group.label} className="pt-1">
                        <h2 className="px-2.5 pb-1 text-[11px] font-semibold text-muted-foreground">
                          {group.label}
                        </h2>
                        <div className="flex flex-col gap-0.5">
                          {group.items.map((s) => (
                            <SessionRow
                              key={s.session_id}
                              session={s}
                              active={s.session_id === activeSessionId}
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
            </li>
          ))}
        </ul>
      </nav>

      {/* 푸터 — 도움말 · 환경 설정 (와이어프레임 Frame 148, 216×44) */}
      <div className={cn("shrink-0 border-t border-border py-2", collapsed ? "px-2" : "px-3")}>
        <ul className="flex flex-col gap-0.5">
          {FOOTER_ITEMS.map((item) => (
            <li key={item.id}>
              <NavButton
                icon={item.icon}
                label={item.label}
                active={item.id === "settings" && settingsActive}
                collapsed={collapsed}
                onClick={() => handleFooter(item)}
              />
            </li>
          ))}
        </ul>
      </div>

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
