import React, { lazy, Suspense, useCallback, useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import Sidebar from "./Sidebar";
import StatusBar from "./StatusBar";
// 통합 승인 다이얼로그: Phase 2 메신저 CONFIRM과 Phase 4 에이전트 스킬 CONFIRM이
// 동일한 pendingApproval 상태를 공유한다.
import ApprovalDialog from "@/components/security/ApprovalDialog";
import CommandPalette from "@/components/cmdk/CommandPalette";
import ShortcutHelp from "@/components/cmdk/ShortcutHelp";
import UpdateNotice from "@/components/updater/UpdateNotice";
import { Toast } from "@/components/ui/toast";
import useAppStore from "@/store/appStore";
import useToast from "@/hooks/useToast";
import { securityRespondApproval } from "@/lib/api";
import { toUserMessage } from "@/lib/errorMessages";

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
const Dashboard = lazy(() => import("@/components/dashboard/Dashboard"));
const WorkspacePage = lazy(() => import("@/components/workspace/WorkspacePage"));
const ConversationsPage = lazy(() => import("@/components/conversations/ConversationsPage"));
const SettingsHub = lazy(() => import("@/components/settings/SettingsHub"));

/**
 * Map page keys to their lazy components.
 *
 * 4개 핵심 페이지(dashboard/workspace/conversations/settings) 외에는
 * Settings 허브 안의 탭으로 흡수되었지만, 호환성을 위해 일부 키는
 * Settings 허브로 라우팅(설정 내부에서 자동으로 해당 탭이 열림).
 */
const PAGE_MAP = {
  dashboard: Dashboard,
  workspace: WorkspacePage,
  conversations: ConversationsPage,
  settings: SettingsHub,

  // ── 설정 허브 내부 탭으로 이동 — 외부에서 이 키로 진입해도 Settings로 라우팅 ──
  credentials: SettingsHub,
  audit: SettingsHub,
  security: SettingsHub,
  permissions: SettingsHub,
  messenger_settings: SettingsHub,
  guide: SettingsHub,
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
  const pendingApproval = useAppStore((s) => s.pendingApproval);
  const setPendingApproval = useAppStore((s) => s.setPendingApproval);
  const setSidebarCollapsed = useAppStore((s) => s.setSidebarCollapsed);
  const PageComponent = PAGE_MAP[currentPage] ?? Dashboard;

  // 로컬 토스트 상태 (승인/거부 결과 안내) — useToast 훅이 상태·자동 dismiss 소유
  const { toast: notifyToast, showToast: showNotify, dismissToast: dismissNotify } = useToast();

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
  }, [setSidebarCollapsed]);

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

  const handleApprove = useCallback(async (_auditId) => {
    if (!pendingApproval) return;
    const approvalId = pendingApproval.approval_id;
    setPendingApproval(null);
    try {
      await securityRespondApproval(approvalId, true);
      showNotify({ message: "승인되었습니다. 작업이 실행됩니다." });
    } catch (err) {
      showNotify({ message: toUserMessage(err, "승인 전달에 실패했습니다.") });
    }
  }, [pendingApproval, setPendingApproval]);

  const handleReject = useCallback(async (_auditId, reason) => {
    if (!pendingApproval) return;
    const approvalId = pendingApproval.approval_id;
    setPendingApproval(null);
    try {
      await securityRespondApproval(approvalId, false);
    } catch {
      // 거부 전달 실패는 조용히 처리
    }
    showNotify({
      message: reason
        ? `거부되었습니다. 사유: ${reason}`
        : "거부되었습니다. 작업이 취소되었습니다.",
    });
  }, [pendingApproval, setPendingApproval]);

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-background">
      {/* Left sidebar */}
      <Sidebar />

      {/* Main area */}
      <div className="flex flex-1 flex-col overflow-hidden">
        <StatusBar />
        <main className="flex-1 overflow-y-auto p-6">
          <Suspense fallback={<PageLoader />}>
            <PageComponent />
          </Suspense>
        </main>
      </div>

      {/* 전역 승인 다이얼로그 — 어느 페이지에서든 표시 (security/ApprovalDialog 단일화) */}
      {pendingApproval && (
        <ApprovalDialog
          open={true}
          command={pendingApproval.command ?? pendingApproval.summary ?? ""}
          reason={pendingApproval.reason ?? pendingApproval.summary ?? "에이전트 스킬 실행 승인 요청"}
          auditId={pendingApproval.audit_id ?? pendingApproval.approval_id}
          source={pendingApproval.source}
          toolName={pendingApproval.tool_display_name ?? pendingApproval.tool_name}
          sessionId={pendingApproval.session_id}
          danger={pendingApproval.danger}
          timeoutSeconds={60}
          onApprove={handleApprove}
          onReject={handleReject}
        />
      )}

      {/* 승인/거부 결과 토스트 */}
      <Toast
        toast={notifyToast}
        onDismiss={dismissNotify}
      />

      {/* 글로벌 명령 팔레트 — Cmd/Ctrl+K로 토글 */}
      <CommandPalette open={cmdkOpen} onClose={() => setCmdkOpen(false)} />

      {/* 단축키 도움말 — `?` 또는 Cmd/Ctrl+/ */}
      <ShortcutHelp open={shortcutOpen} onClose={() => setShortcutOpen(false)} />

      {/* 자동 업데이트 알림 — 앱 시작 5초 뒤 1회 check, 새 버전 시 우상단 dot */}
      <UpdateNotice />
    </div>
  );
}
