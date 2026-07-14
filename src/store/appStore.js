/**
 * Global Zustand store for Office Claw.
 *
 * Tracks:
 *  - currentPage: which sidebar section is active
 *  - llmConfig: provider (ollama) + model name
 *  - sidecarStatus: whether the Python sidecar is reachable
 *  - onboardingComplete: whether the user has completed the onboarding wizard
 */
import { create } from "zustand";
import { persist } from "zustand/middleware";

/** @typedef {'chat'|'activity'|'workspace'|'conversations'|'credentials'|'audit'|'preferences'|'settings'|'security'|'permissions'|'guide'|'mobile_relay'} Page */

/**
 * @typedef {Object} ChatMessage
 * @property {'user'|'agent'|'system'} role
 * @property {string} text
 * @property {Array} [toolCalls]
 * @property {string} [error]
 */

/**
 * @typedef {Object} LLMConfig
 * @property {'ollama'} provider
 * @property {string} model
 */

/**
 * @typedef {Object} SidecarStatus
 * @property {'checking'|'ok'|'error'} state
 * @property {string} [message]
 */

const useAppStore = create(
  persist(
    (set) => ({
      /**
       * 시작 페이지는 홈이다 — `chat` 키가 홈(문서 그리드)을 가리킨다.
       * 최종 와이어프레임 B-1에서 시작 화면은 채팅 스레드가 아니라 문서 관리
       * 지면이고, 채팅은 그 위에 뜨는 패널(components/chat/ChatPanel.jsx)이다.
       * @type {Page}
       */
      currentPage: "chat",

      /** @type {LLMConfig} */
      llmConfig: {
        provider: "ollama",
        model: "skt/A.X-4.0-Light:latest",
      },

      /** @type {SidecarStatus} */
      sidecarStatus: {
        state: "checking",
        message: "사이드카 확인 중...",
      },

      /**
       * Whether the active LLM provider is actually reachable.
       * null = not yet checked, true = reachable, false = unreachable
       * @type {boolean|null}
       */
      llmReachable: null,

      /**
       * Whether the user has completed the first-run onboarding wizard.
       * Persisted to localStorage so it's only shown once.
       * @type {boolean}
       */
      onboardingComplete: false,

      /**
       * Sidebar collapsed state — persist되어 다음 세션에도 유지.
       * Cmd/Ctrl+B 토글 또는 StatusBar 버튼.
       * @type {boolean}
       */
      sidebarCollapsed: false,

      // ── Phase 1: officeclaw state ────────────────────────────────────────

      /**
       * 워크스페이스 경로 (표시용).
       * @type {string}
       */
      workspacePath: "~/officeclaw/Workspace",

      // ── 채팅 세션 state ────────────────────────────────────────────────────

      /**
       * 현재 활성 채팅 세션 ID (chat_history 영속화용).
       * null이면 다음 메시지 전송 시 프론트에서 새 세션 ID를 생성한다.
       * @type {string|null}
       */
      activeSessionId: null,

      // ── Actions ──────────────────────────────────────────────────────────────

      /** Navigate to a page */
      setCurrentPage: (/** @type {Page} */ page) => set({ currentPage: page }),

      /** Update LLM config (partial update supported) */
      setLLMConfig: (/** @type {Partial<LLMConfig>} */ config) =>
        set((state) => ({ llmConfig: { ...state.llmConfig, ...config } })),

      /** Update sidecar connection status */
      setSidecarStatus: (/** @type {SidecarStatus} */ status) =>
        set({ sidecarStatus: status }),

      /** Update whether the active LLM provider is reachable */
      setLLMReachable: (/** @type {boolean|null} */ reachable) =>
        set({ llmReachable: reachable }),

      /** Mark onboarding as complete — hides the wizard permanently */
      completeOnboarding: () => set({ onboardingComplete: true }),

      /** Update the active chat session ID */
      setActiveSessionId: (/** @type {string|null} */ sessionId) =>
        set({ activeSessionId: sessionId }),

      /**
       * 에이전트 채팅 메시지 목록. 렌더는 ChatPanel, 액션은 lib/chatManager.js가 맡고
       * 여기서는 상태만 소유한다(세션 목록·진행 상태는 store/chatStore.js).
       * system 메시지는 타임아웃 거부 등 시스템 이벤트를 채팅 히스토리에 영구 기록한다.
       * @type {ChatMessage[]}
       */
      agentMessages: [],

      /** 에이전트 채팅 메시지 추가 */
      addAgentMessage: (/** @type {ChatMessage} */ message) =>
        set((state) => ({ agentMessages: [...state.agentMessages, message] })),

      /** 에이전트 채팅 메시지 목록 교체 */
      setAgentMessages: (/** @type {ChatMessage[]} */ messages) =>
        set({ agentMessages: messages }),

      /** Sidebar collapse 토글 (또는 명시적 set) */
      setSidebarCollapsed: (/** @type {boolean|((prev:boolean)=>boolean)} */ next) =>
        set((state) => ({
          sidebarCollapsed:
            typeof next === "function" ? next(state.sidebarCollapsed) : !!next,
        })),
    }),
    {
      name: "office-claw-store",
      // Only persist non-transient state
      partialize: (state) => ({
        onboardingComplete: state.onboardingComplete,
        llmConfig: state.llmConfig,
        sidebarCollapsed: state.sidebarCollapsed,
      }),
    }
  )
);

export default useAppStore;
