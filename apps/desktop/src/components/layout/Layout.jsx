import React, { lazy, Suspense, useEffect, useState } from "react";
import { Loader2, MessageCircle } from "lucide-react";
import ConversationSidebar from "./ConversationSidebar";
import StatusBar from "./StatusBar";
import CommandPalette from "@/components/cmdk/CommandPalette";
import ShortcutHelp from "@/components/cmdk/ShortcutHelp";
import UpdateNotice from "@/components/updater/UpdateNotice";
import ChatPanel from "@/components/chat/ChatPanel";
import { cn } from "@/lib/utils";
import useAppStore from "@/store/appStore";
import useChatStore from "@/store/chatStore";
import { reservesLayoutSpace } from "@/lib/chatPanel";
import { toggleTheme } from "@/lib/themeManager";

/**
 * 단일키(`?`, `Y`, `N` 등) 단축키는 input/textarea/contentEditable에 포커스가
 * 있는 동안 무시해야 한다. 그렇지 않으면 사용자가 글자를 입력하다가 모달이 뜬다.
 * 조합키(Cmd/Ctrl)는 일반적으로 입력 가로채기가 OK이므로 별도 처리 안 함.
 */
function isTypingTarget(target) {
  if (!target) return false;
  const tag = target.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return true;
  if (target.isContentEditable) return true;
  return false;
}

/**
 * IME composition 중 단일키 단축키가 트리거되지 않도록 한다.
 * 한글/일본어/중국어 입력 시 변환 중간 상태에서 `?` `Y` 등이 흘러나오면 모달이 뜸.
 *
 *   - `e.isComposing` (표준): 조합 중이면 true
 *   - `e.nativeEvent.isComposing`: React SyntheticEvent에서도 동일
 *   - `e.keyCode === 229`: 일부 브라우저(특히 Windows IME)에서 조합 중 신호
 */
function isImeComposing(e) {
  if (!e) return false;
  if (e.isComposing) return true;
  if (e.nativeEvent && e.nativeEvent.isComposing) return true;
  if (e.keyCode === 229) return true;
  return false;
}

// Lazy-loaded page components — each module is its own code-split chunk.
// This keeps the initial bundle small and defers loading of heavy modules
// until the user first navigates to them.
//
// `chat` 키는 이제 홈(문서 그리드)을 가리킨다 — 최종 와이어프레임 B-1에서
// 시작 화면이 채팅 스레드가 아니라 문서 관리 지면이고, 채팅은 그 위에 뜨는
// 패널(ChatPanel)이기 때문이다.
const HomePage = lazy(() => import("@/components/home/HomePage"));
const ActivityPage = lazy(() => import("@/components/activity/ActivityPage"));
const WorkspacePage = lazy(() => import("@/components/workspace/WorkspacePage"));
const ConversationHistoryPage = lazy(() =>
  import("@/components/conversations/ConversationHistoryPage")
);
const SettingsHub = lazy(() => import("@/components/settings/SettingsHub"));
const PreferencesPage = lazy(() => import("@/components/settings/PreferencesPage"));

/**
 * Map page keys to their lazy components.
 *
 * 5개 핵심 페이지(chat/activity/workspace/conversations/settings) 외에는
 * Settings 허브 안의 탭으로 흡수되었지만, 호환성을 위해 일부 키는
 * Settings 허브로 라우팅(설정 내부에서 자동으로 해당 탭이 열림).
 */
const PAGE_MAP = {
  chat: HomePage,
  activity: ActivityPage,
  workspace: WorkspacePage,
  // 사이드바 `대화목록` — 지난 대화를 요일별/파일별로 훑는 화면.
  conversations: ConversationHistoryPage,

  // 사이드바 푸터의 `환경 설정` — 와이어프레임의 단일 페이지.
  preferences: PreferencesPage,

  // 탭 허브는 남는다. 와이어프레임에 없는 5개 기능(로컬 AI·자격증명·보안·
  // 허용 범위·실행 기록)의 유일한 진입 경로가 Cmd+K → 아래 키들이다.
  settings: SettingsHub,

  // ── 설정 허브 내부 탭으로 이동 — 외부에서 이 키로 진입해도 Settings로 라우팅 ──
  credentials: SettingsHub,
  audit: SettingsHub,
  security: SettingsHub,
  permissions: SettingsHub,
  guide: SettingsHub,
  mobile_relay: SettingsHub,
};

/** Full-page loading fallback shown while a lazy chunk is being fetched. */
function PageLoader() {
  return (
    <div className="flex h-full items-center justify-center">
      <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
    </div>
  );
}

export default function Layout() {
  const currentPage = useAppStore((s) => s.currentPage);
  const setSidebarCollapsed = useAppStore((s) => s.setSidebarCollapsed);
  const panelOpen = useChatStore((s) => s.panelOpen);
  const panelMode = useChatStore((s) => s.panelMode);
  const setPanelOpen = useChatStore((s) => s.setPanelOpen);
  const PageComponent = PAGE_MAP[currentPage] ?? HomePage;

  // 홈(문서 그리드)은 배경 장식·그리드 여백을 스스로 관리한다.
  // 여기서 p-6을 얹으면 장식 타원이 잘린다.
  const isHome = currentPage === "chat";

  // 도킹일 때만 본문이 자리를 내준다 — 플로팅은 본문 위에 겹친다.
  const panelDocked = panelOpen && reservesLayoutSpace(panelMode);

  // ── 글로벌 모달 상태 ────────────────────────────────────────────────────
  const [cmdkOpen, setCmdkOpen] = useState(false);
  const [shortcutOpen, setShortcutOpen] = useState(false);

  useEffect(() => {
    const handleKey = (e) => {
      const meta = e.metaKey || e.ctrlKey;
      const key = e.key;

      // ── 조합키 (입력 필드여도 가로챔) ──
      if (meta && (key === "k" || key === "K")) {
        e.preventDefault();
        setCmdkOpen((prev) => !prev);
        return;
      }
      if (meta && (key === "b" || key === "B")) {
        e.preventDefault();
        setSidebarCollapsed((prev) => !prev);
        return;
      }
      if (meta && key === "/") {
        e.preventDefault();
        setShortcutOpen((prev) => !prev);
        return;
      }
      // 채팅 패널 토글 — 최종안 내비에 `채팅` 항목이 없어졌으므로
      // 키보드로도 열 수 있어야 한다.
      if (meta && (key === "j" || key === "J")) {
        e.preventDefault();
        setPanelOpen(!useChatStore.getState().panelOpen);
        return;
      }
      if (meta && e.shiftKey && (key === "l" || key === "L")) {
        e.preventDefault();
        toggleTheme();
        return;
      }

      // ── 단일키 (입력 중 / IME 조합 중이면 무시) ──
      if (isTypingTarget(e.target)) return;
      if (isImeComposing(e)) return;

      if (key === "?") {
        // Shift+/ 가 ? 인 키보드 레이아웃에서도 동작
        e.preventDefault();
        setShortcutOpen((prev) => !prev);
        return;
      }
    };
    const handleOpenCmdk = () => setCmdkOpen(true);
    const handleOpenHelp = () => setShortcutOpen(true);
    window.addEventListener("keydown", handleKey);
    window.addEventListener("officeclaw:open-cmdk", handleOpenCmdk);
    window.addEventListener("officeclaw:open-shortcut-help", handleOpenHelp);
    return () => {
      window.removeEventListener("keydown", handleKey);
      window.removeEventListener("officeclaw:open-cmdk", handleOpenCmdk);
      window.removeEventListener("officeclaw:open-shortcut-help", handleOpenHelp);
    };
  }, [setSidebarCollapsed, setPanelOpen]);

  // 모달 Esc 처리 (개별 모달 외에 안전망).
  // IME 조합 중 Esc는 변환 취소용이므로 가로채지 않는다.
  useEffect(() => {
    const onEsc = (e) => {
      if (e.key !== "Escape") return;
      if (isImeComposing(e)) return;
      if (shortcutOpen) setShortcutOpen(false);
    };
    window.addEventListener("keydown", onEsc);
    return () => window.removeEventListener("keydown", onEsc);
  }, [shortcutOpen]);



  return (
    <div className="relative flex h-screen w-screen overflow-hidden bg-background">
      {/* 대화 목록 사이드바 — Cmd/Ctrl+B로 접으면 아이콘 레일이 된다.
          접힘 모양은 사이드바가 직접 소유하므로 여기서 언마운트하지 않는다. */}
      <ConversationSidebar />

      {/*
        본문 + 채팅 패널.

        relative인 이유: 플로팅 모드의 패널이 이 영역 기준으로 우하단에 뜬다
        (와이어프레임 Frame 169). 화면 전체 기준으로 잡으면 사이드바를 접었다
        폈을 때 패널이 같이 밀린다.
      */}
      <div className="relative flex min-w-0 flex-1 overflow-hidden">
        <div className={cn("flex min-w-0 flex-col overflow-hidden", panelDocked ? "flex-1" : "w-full")}>
          {/*
            홈에서는 StatusBar를 숨긴다.
            와이어프레임의 홈(B-1)에는 상단 바가 없고 우상단의 `로컬 에이전트 작동중`
            하나만 있는데, StatusBar를 같이 띄우면 같은 AI 상태가 40px 간격으로
            두 번 나온다. 나머지 페이지에서는 그대로 둔다 — 보안 상태와
            Cmd+K 힌트는 홈 밖에서 대체 경로가 없다.
          */}
          {!isHome && <StatusBar />}
          <main
            className={cn(
              "flex-1 overflow-hidden",
              isHome ? "flex flex-col" : "overflow-y-auto p-6"
            )}
          >
            <Suspense fallback={<PageLoader />}>
              <PageComponent />
            </Suspense>
          </main>
        </div>

        {/* 채팅 패널 — 어느 페이지 위에든 뜬다 (와이어프레임 B-2 / B-3) */}
        {panelOpen && <ChatPanel />}
      </div>

      {/* 패널이 닫혀 있고 홈이 아닐 때의 재진입 버튼.
          최종안 내비에 `채팅` 항목이 없어서, 이게 없으면 패널을 한 번 닫은 뒤
          워크스페이스에서 보던 대화로 돌아갈 길이 Cmd+J뿐이다. */}
      {!panelOpen && !isHome && (
        <button
          type="button"
          onClick={() => setPanelOpen(true)}
          className="absolute bottom-5 right-5 z-30 flex h-12 w-12 items-center justify-center rounded-full bg-primary text-primary-foreground shadow-lg transition-transform hover:scale-105"
          aria-label="채팅 열기"
          title="채팅 열기 (Cmd/Ctrl+J)"
        >
          <MessageCircle className="h-5 w-5" />
        </button>
      )}



      {/* 글로벌 명령 팔레트 — Cmd/Ctrl+K로 토글 */}
      <CommandPalette open={cmdkOpen} onClose={() => setCmdkOpen(false)} />

      {/* 단축키 도움말 — `?` 또는 Cmd/Ctrl+/ */}
      <ShortcutHelp open={shortcutOpen} onClose={() => setShortcutOpen(false)} />

      {/* 자동 업데이트 알림 — 앱 시작 5초 뒤 1회 check, 새 버전 시 우상단 dot */}
      <UpdateNotice />
    </div>
  );
}
