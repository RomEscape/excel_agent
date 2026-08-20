/**
 * chatStore — 에이전트 채팅 도메인의 상태 소유자.
 *
 * 왜 새 store인가:
 *   기존에는 WorkspacePage의 ChatSidePanel이 세션 목록·로딩·승인 대기 상태를
 *   전부 지역 useState로 들고 있었다. 채팅이 워크스페이스 안쪽에만 있을 때는
 *   그래도 됐지만, 이제 채팅 패널이 어느 페이지 위에든 뜨고 사이드바(대화 목록)와
 *   패널(스레드)이 같은 데이터를 봐야 하므로 한 곳이 소유해야 한다.
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
import { persist } from "zustand/middleware";
import { DEFAULT_PANEL_MODE, nextPanelMode, normalizePanelMode } from "@/lib/chatPanel";

const useChatStore = create(
  persist(
    (set) => ({
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
      /** CONFIRM 등급 엑셀 작업 승인 대기 (null이면 인라인 승인 버튼이 사라진다) */
      pendingExcelApproval: null,
      excelApprovalBusy: false,
      excelSaving: false,
      insertingRange: false,

      // ── 채팅 패널 (와이어프레임 B-2 / B-3) ───────────────────────────────────
      /**
       * 패널이 떠 있는지. 홈(문서 그리드)에서는 닫혀 있고, 대화가 시작되거나
       * 사이드바에서 대화를 열면 뜬다.
       */
      panelOpen: false,
      /** @type {'docked'|'floating'} 패널 크기 모드 — 규칙은 lib/chatPanel.js */
      panelMode: DEFAULT_PANEL_MODE,

      /**
       * 진행 중인 툴 실행 스텝 (와이어프레임 B-7의 `문서 형식 파악 완료` 칩).
       * 응답이 끝나면 비운다 — 다음 턴까지 남으면 어느 요청의 진행인지 알 수 없다.
       * @type {Array<{id: string, label: string, done: boolean}>}
       */
      toolSteps: [],

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
      setPanelOpen: (panelOpen) => set({ panelOpen: !!panelOpen }),
      setPanelMode: (panelMode) => set({ panelMode: normalizePanelMode(panelMode) }),
      togglePanelMode: () =>
        set((state) => ({ panelMode: nextPanelMode(state.panelMode) })),
      setToolSteps: (toolSteps) => set({ toolSteps: toolSteps ?? [] }),
    }),
    {
      name: "office-claw-chat",
      // 패널 크기 모드만 기억한다. 세션 목록·진행 상태는 매번 새로 받아야 하고,
      // panelOpen까지 저장하면 앱을 켤 때마다 지난 대화가 떠 있어서
      // 홈(문서 그리드)이 첫 화면이라는 전제가 깨진다.
      partialize: (state) => ({ panelMode: state.panelMode }),
    }
  )
);

export default useChatStore;
