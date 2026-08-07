/**
 * chatStore — 에이전트 채팅 도메인의 상태 소유자.
 *
 * 왜 새 store인가:
 *   기존에는 WorkspacePage의 ChatSidePanel이 세션 목록·로딩·승인 대기 상태를
 *   전부 지역 useState로 들고 있었다. 채팅이 워크스페이스 안쪽에만 있을 때는
 *   그래도 됐지만, 이제 채팅이 독립 페이지가 되고 사이드바(대화 목록)와
 *   본문(스레드)이 같은 데이터를 봐야 하므로 한 곳이 소유해야 한다.
 *   (CLAUDE.md 안티패턴: "같은 상태를 여러 컴포넌트가 각자 state로 들고 있기")
 *
 * 메시지 본문(agentMessages)과 activeSessionId는 appStore가 계속 소유한다 —
 * 대시보드 등 기존 구독처가 있고, 여기로 옮기면 그 경로가 전부 깨진다.
 * 이 store는 "세션 목록 + 진행 상태"만 맡는다.
 *
 * 액션(불러오기/삭제/전송)은 lib/chatManager.js에 있다. 이 파일은 상태와
 * 순수 setter만 갖는다.
 */
import { create } from "zustand";

const useChatStore = create((set) => ({
  // ── 세션 목록 ────────────────────────────────────────────────────────────
  /** @type {Array<{session_id: string, preview?: string, message_count?: number, last_message_at?: string}>} */
  sessions: [],
  sessionsLoading: false,
  /** sidecar가 chat_history를 지원하지 않으면 false — 사이드바에서 목록을 숨긴다. */
  sessionsAvailable: true,

  // ── 진행 상태 ────────────────────────────────────────────────────────────
  /** 메시지 전송/응답 대기 중 */
  sending: false,
  /** 진행 중 작업 설명 — 타이핑 버블에 표시 */
  taskLabel: "",

  // ── 엑셀 도구 상태 ───────────────────────────────────────────────────────
  /** CONFIRM 등급 엑셀 작업 승인 대기 (null이면 다이얼로그 닫힘) */
  pendingExcelApproval: null,
  excelApprovalBusy: false,
  excelSaving: false,
  insertingRange: false,

  // ── setters ──────────────────────────────────────────────────────────────
  setSessions: (sessions) => set({ sessions }),
  setSessionsLoading: (sessionsLoading) => set({ sessionsLoading }),
  setSessionsAvailable: (sessionsAvailable) => set({ sessionsAvailable }),
  setSending: (sending) => set({ sending }),
  setTaskLabel: (taskLabel) => set({ taskLabel }),
  setPendingExcelApproval: (pendingExcelApproval) => set({ pendingExcelApproval }),
  setExcelApprovalBusy: (excelApprovalBusy) => set({ excelApprovalBusy }),
  setExcelSaving: (excelSaving) => set({ excelSaving }),
  setInsertingRange: (insertingRange) => set({ insertingRange }),
}));

export default useChatStore;
