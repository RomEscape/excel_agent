/**
 * Global Zustand store for Office Claw.
 *
 * Tracks:
 *  - currentPage: which sidebar section is active
 *  - llmConfig: provider (ollama|claude) + model name
 *  - sidecarStatus: whether the Python sidecar is reachable
 *  - onboardingComplete: whether the user has completed the onboarding wizard
 */
import { create } from "zustand";
import { persist } from "zustand/middleware";

/** @typedef {'dashboard'|'telegram'|'workspace'|'conversations'|'credentials'|'audit'|'settings'|'security'|'permissions'|'messenger_settings'|'guide'} Page */

/**
 * @typedef {Object} ChatMessage
 * @property {'user'|'agent'|'system'} role
 * @property {string} text
 * @property {Array} [toolCalls]
 * @property {string} [error]
 */

/**
 * @typedef {Object} LLMConfig
 * @property {'ollama'|'claude'} provider
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
      /** @type {Page} */
      currentPage: "dashboard",

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
       * 텔레그램 봇 연결 상태.
       * @type {boolean}
       */
      telegramConnected: false,

      /**
       * 워크스페이스 경로 (표시용).
       * @type {string}
       */
      workspacePath: "~/officeclaw/Workspace",

      /**
       * 온보딩에서 선택된 메신저.
       * @type {'telegram'|'slack'|'discord'}
       */
      selectedMessenger: "telegram",

      // ── 채팅 세션 / 승인 state ─────────────────────────────────────────────

      /**
       * 현재 활성 채팅 세션 ID (chat_history 영속화용).
       * null이면 다음 메시지 전송 시 프론트에서 새 세션 ID를 생성한다.
       * @type {string|null}
       */
      activeSessionId: null,

      /**
       * 현재 대기 중인 CONFIRM 승인 요청 (메신저 보안 CONFIRM 봇 오프라인 폴백).
       * command/reason/audit_id 또는 tool_name/summary 필드가 채워질 수 있다.
       *
       * Layout.jsx의 ApprovalDialog가 이 상태를 렌더링한다. null이면 미표시.
       * @type {{
       *   approval_id?: string;
       *   tool_name?: string;
       *   tool_display_name?: string;
       *   summary?: string;
       *   args_preview?: Record<string, unknown>;
       *   session_id?: string;
       *   created_at?: string;
       *   command?: string;
       *   reason?: string;
       *   audit_id?: number;
       * } | null}
       */
      pendingApproval: null,

      // ── Actions ──────────────────────────────────────────────────────────────

      /** Navigate to a page */
      setCurrentPage: (/** @type {Page} */ page) => set({ currentPage: page }),

      /** Update telegram connection status */
      setTelegramConnected: (/** @type {boolean} */ connected) =>
        set({ telegramConnected: connected }),

      /** Update selected messenger */
      setSelectedMessenger: (/** @type {'telegram'|'slack'|'discord'} */ messenger) =>
        set({ selectedMessenger: messenger }),

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
       * 에이전트 채팅 메시지 목록 (대시보드 AgentChatPanel과 공유).
       * system 메시지는 타임아웃 거부 등 시스템 이벤트를 채팅 히스토리에 영구 기록한다.
       * @type {ChatMessage[]}
       */
      agentMessages: [],

      /** 승인 요청 설정 (null이면 다이얼로그 닫힘) */
      setPendingApproval: (approval) => set({ pendingApproval: approval }),

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
        telegramConnected: state.telegramConnected,
        selectedMessenger: state.selectedMessenger,
        sidebarCollapsed: state.sidebarCollapsed,
      }),
    }
  )
);

export default useAppStore;
