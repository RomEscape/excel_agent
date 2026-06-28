import React, { useState, useRef, useEffect } from "react";
import appIcon from "@/assets/app-icon.png";
import { version } from "../../../package.json";
import {
  LayoutDashboard,
  FolderOpen,
  MessagesSquare,
  Settings as SettingsIcon,
  Lock,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { getOpenClawStatus } from "@/lib/statusTokens";
import useAppStore from "@/store/appStore";
import useStatusStore from "@/store/statusStore";

/**
 * 단순화된 Sidebar.
 *
 * 핵심 메뉴(상단) — 비개발자도 즉시 이해 가능한 4개 영역:
 *   1) 대시보드 — 작업 요약 (실행중/완료)
 *   2) 워크스페이스 — 파일/문서 작업 공간 (OpenClaw 미설치 시 자물쇠로 가드)
 *   3) 대화 — 메신저 모니터링 (OpenClaw 미설치 시 자물쇠로 가드)
 *
 * 설정(하단) — 모든 부가 메뉴를 탭으로 흡수.
 *
 * collapsed 모드: 64px (icon-only). expanded: 240px.
 *   - hover 200ms 후 라벨 tooltip
 *   - appStore의 zustand persist(`office-claw-store`)로 sidebarCollapsed 유지
 *   - 토글: Cmd+B (Layout이 처리)
 */
const PRIMARY_ITEMS = [
  { id: "dashboard", label: "대시보드", icon: LayoutDashboard, gated: false },
  { id: "workspace", label: "워크스페이스", icon: FolderOpen, gated: true },
  { id: "conversations", label: "대화", icon: MessagesSquare, gated: true },
];

// 200ms hover delay 후 등장하는 라벨 tooltip (collapsed 시).
// 게이팅된 항목은 라벨에 "OpenClaw 설치 필요"를 명시.
function CollapsedLabel({ label, gated }) {
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
    <span
      className="absolute inset-0"
      onMouseEnter={handleEnter}
      onMouseLeave={handleLeave}
    >
      {show && (
        <span
          role="tooltip"
          className="absolute left-full top-1/2 z-50 ml-2 flex -translate-y-1/2 flex-col gap-0.5 whitespace-nowrap rounded-md border border-border bg-popover px-2 py-1 text-xs text-popover-foreground shadow-md pointer-events-none"
        >
          <span className="font-medium">{label}</span>
          {gated && (
            <span className="flex items-center gap-1 text-[10px] text-amber-600 dark:text-amber-400">
              <Lock className="h-2.5 w-2.5" />
              OpenClaw가 준비되면 사용할 수 있어요
            </span>
          )}
        </span>
      )}
    </span>
  );
}

export default function Sidebar() {
  const currentPage = useAppStore((s) => s.currentPage);
  const setCurrentPage = useAppStore((s) => s.setCurrentPage);
  const sidebarCollapsed = useAppStore((s) => s.sidebarCollapsed);
  // 중앙 status store에서 OpenClaw 상태 — Dashboard/StatusBar와 동일한 데이터 소스
  const ocModule = useStatusStore((s) => s.modules.openclaw);

  // OpenClaw가 "준비됨" 상태가 아니면 워크스페이스/대화 메뉴를 자물쇠로 가드.
  // 통합 톤 매퍼(getOpenClawStatus)로 판정 — StatusBar/Dashboard와 정확히 일치.
  // unknown(=초기/확인 중)에서는 가드하지 않음 (확인 결과 나오기 전).
  const ocState = ocModule.running
    ? "running"
    : ocModule.state === "unknown"
    ? "checking"
    : "stopped";
  const ocBlocked = getOpenClawStatus(ocState).tone === "warning";
  const collapsed = !!sidebarCollapsed;

  const isSettingsActive =
    currentPage === "settings" ||
    currentPage === "credentials" ||
    currentPage === "audit" ||
    currentPage === "security" ||
    currentPage === "permissions" ||
    currentPage === "messenger_settings" ||
    currentPage === "guide";

  const handleClickPrimary = (item) => {
    if (item.gated && ocBlocked) {
      // OpenClaw가 죽어 있으면 자동 설치 prompt를 즉시 띄움 (설정 탭 경유 X)
      window.dispatchEvent(new CustomEvent("officeclaw:open-openclaw-install"));
      return;
    }
    setCurrentPage(item.id);
  };

  return (
    <aside
      className={cn(
        "flex h-full shrink-0 flex-col bg-sidebar text-sidebar-foreground transition-[width] duration-200 ease-out",
        collapsed ? "w-16" : "w-56"
      )}
      data-collapsed={collapsed}
      aria-label="앱 내비게이션"
    >
      {/* Brand */}
      <div
        className={cn(
          "flex items-center border-b border-sidebar-border",
          collapsed ? "justify-center px-2 py-4" : "gap-2 px-4 py-5"
        )}
      >
        <img src={appIcon} alt="officeclaw" className="h-7 w-7 shrink-0 rounded-md" />
        {!collapsed && (
          <span className="font-bold text-base tracking-tight">officeclaw</span>
        )}
      </div>

      {/* Primary navigation */}
      <nav className="flex-1 overflow-y-auto py-4" aria-label="메인 메뉴">
        <ul className="space-y-1 px-2">
          {PRIMARY_ITEMS.map((item) => {
            const { id, label, icon: Icon, gated } = item;
            const isGated = gated && ocBlocked;
            const active = currentPage === id;
            return (
              <li key={id} className="relative">
                <button
                  onClick={() => handleClickPrimary(item)}
                  className={cn(
                    "relative flex w-full items-center rounded-md text-sm font-medium transition-colors",
                    collapsed
                      ? "h-10 justify-center px-0"
                      : "gap-3 px-3 py-2.5",
                    active
                      ? "bg-sidebar-accent text-sidebar-accent-foreground"
                      : "text-sidebar-foreground/70 hover:bg-sidebar-accent/60 hover:text-sidebar-accent-foreground",
                    isGated && "opacity-60"
                  )}
                  aria-current={active ? "page" : undefined}
                  aria-label={collapsed ? label : undefined}
                  title={
                    !collapsed && isGated
                      ? "OpenClaw가 준비되면 사용할 수 있어요 — 클릭해서 설치 가이드로 이동"
                      : undefined
                  }
                >
                  <Icon className="h-4 w-4 shrink-0" />
                  {!collapsed && (
                    <>
                      <span className="flex-1 text-left">{label}</span>
                      {isGated && (
                        <Lock className="h-3 w-3 shrink-0 text-amber-500" aria-label="OpenClaw 준비 필요" />
                      )}
                    </>
                  )}
                  {collapsed && isGated && (
                    <span
                      className="absolute -right-0.5 -top-0.5 flex h-4 w-4 items-center justify-center rounded-full bg-amber-100 ring-2 ring-amber-300 dark:bg-amber-900/60 dark:ring-amber-700"
                      aria-label="OpenClaw 준비 필요 — 잠김"
                      title="OpenClaw가 준비되면 사용할 수 있어요"
                    >
                      <Lock className="h-2.5 w-2.5 text-amber-600 dark:text-amber-300" />
                    </span>
                  )}
                </button>
                {collapsed && <CollapsedLabel label={label} gated={isGated} />}
              </li>
            );
          })}
        </ul>
      </nav>

      {/* Settings — 시각적으로 분리된 footer 영역 */}
      <div className="border-t border-sidebar-border bg-sidebar-accent/20 p-2">
        <div className="relative">
          <button
            onClick={() => setCurrentPage("settings")}
            className={cn(
              "relative flex w-full items-center rounded-md text-sm font-medium transition-colors",
              collapsed
                ? "h-10 justify-center px-0"
                : "gap-3 px-3 py-2.5",
              isSettingsActive
                ? "bg-sidebar-accent text-sidebar-accent-foreground"
                : "text-sidebar-foreground/70 hover:bg-sidebar-accent/60 hover:text-sidebar-accent-foreground"
            )}
            aria-current={isSettingsActive ? "page" : undefined}
            aria-label={collapsed ? "설정" : undefined}
          >
            <SettingsIcon className="h-4 w-4 shrink-0" />
            {!collapsed && "설정"}
          </button>
          {collapsed && <CollapsedLabel label="설정" />}
        </div>
        {!collapsed && (
          <p className="mt-2 px-3 text-xs text-sidebar-foreground/40">v{version}</p>
        )}
      </div>
    </aside>
  );
}
