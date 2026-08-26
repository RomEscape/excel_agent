/**
 * 모듈별 상태 매니저 — "각 모듈이 자기 상태를 체크해서 가지고 있는" 패턴.
 *
 * 각 모듈은 다음 메서드를 가진다:
 *
 *   check()    — 설치 위치/실행 상태 확인 → statusStore 업데이트
 *   install()  — 설치 명령 실행 → check()로 검증 → 상태 반영
 *   start()    — (해당되는 모듈만) 데몬 시작 → check()
 *   pullModel(model) — (ollama만) 모델 다운로드 → check()
 *
 * 사용자가 묘사한 흐름:
 *
 *   설치 위치 확인 → 값 있음 → 설치된 것
 *                ↘ 값 없음 → 설치 커맨드 → 결과 확인 → 설치 위치 확인 → 정상
 *
 * 모든 상태 변화는 statusStore를 거치므로 Dashboard/StatusBar/LocalAISetupWizard 등
 * 어디서든 동일한 데이터를 본다.
 */
import useStatusStore, { deriveState } from "@/store/statusStore";
import {
  ollamaStatus,
  installerInstallOllama,
  installerStartOllama,
  installerPullModel,
} from "@/lib/api";
import { hasModelInstalled } from "@/lib/localAISetup";

/**
 * 모듈 정의 모음.
 *
 * 각 모듈은 자기 슬롯(`modules[id]`)을 자기가 관리한다.
 * 외부에서는 `manager.<id>.<action>()` 호출만 하면 store가 자동 갱신됨.
 *
 * LLM 스택은 Ollama 단독이다 — 자연어 → 엑셀 실행은 Ollama의 OpenAI 호환
 * tool-calling으로 사이드카에서 처리되며, 별도 게이트웨이 프로세스가 없다.
 */
export const STATUS_MODULES = {
  // ── Ollama (로컬 LLM 데몬) ─────────────────────────────────────────────────
  ollama: {
    id: "ollama",
    label: "Ollama",

    /**
     * `ollama --version` + 11434 HTTP + /api/tags 모델 목록을 한 번에.
     * Rust get_ollama_status가 세 정보를 종합해 반환.
     */
    async check() {
      const store = useStatusStore.getState();
      store.setOperation("ollama", "checking");
      try {
        const res = await ollamaStatus();
        const installed = !!res?.installed;
        const running = !!res?.running;
        const models = Array.isArray(res?.models) ? res.models : [];

        store.updateModule("ollama", {
          installed,
          version: res?.version ?? null,
          running,
          port: res?.port ?? 11434,
          models,
          message: "",
          state: deriveState({ installed, running }),
          lastChecked: Date.now(),
          lastError: null,
          operation: null,
        });
        return store.getModule("ollama");
      } catch (e) {
        store.updateModule("ollama", {
          state: "error",
          lastError: String(e),
          lastChecked: Date.now(),
          operation: null,
        });
        return store.getModule("ollama");
      }
    },

    /**
     * Ollama 설치.
     * - macOS: ollama.com 공식 배포본을 받아 /Applications에 설치 (brew 비의존)
     * - Windows: winget, 없으면 공식 설치 프로그램 내려받아 무인 설치
     */
    async install() {
      const store = useStatusStore.getState();
      store.setOperation("ollama", "installing");
      let result = null;
      try {
        result = await installerInstallOllama();
        store.updateModule("ollama", { lastInstallResult: result });
        if (result?.ok) {
          await STATUS_MODULES.ollama.check();
        } else {
          store.updateModule("ollama", {
            lastError: result?.message || "설치 실패",
            operation: null,
          });
        }
        return result;
      } catch (e) {
        store.updateModule("ollama", {
          lastError: String(e),
          operation: null,
        });
        throw e;
      }
    },

    /**
     * Ollama 실행.
     * - macOS: 탐지된 Ollama.app을 open (brew services 비의존)
     * - Windows: Ollama 앱 프로세스 시작
     */
    async start() {
      const store = useStatusStore.getState();
      store.setOperation("ollama", "starting");
      let result = null;
      try {
        result = await installerStartOllama();
        store.updateModule("ollama", { lastInstallResult: result });
        if (result?.ok) {
          // 데몬 ready까지 잠시 대기 후 check
          for (let i = 0; i < 30; i++) {
            await new Promise((r) => setTimeout(r, 500));
            const probe = await ollamaStatus().catch(() => ({ running: false }));
            if (probe?.running) break;
          }
          await STATUS_MODULES.ollama.check();
        } else {
          store.updateModule("ollama", {
            lastError: result?.message || "데몬 시작 실패",
            operation: null,
          });
        }
        return result;
      } catch (e) {
        store.updateModule("ollama", {
          lastError: String(e),
          operation: null,
        });
        throw e;
      }
    },

    /**
     * `ollama pull <model>` — 모델 다운로드 후 check로 모델 목록 갱신.
     * 모델명은 Rust 측에서 validate_model_name으로 사전 검증.
     */
    async pullModel(model) {
      const store = useStatusStore.getState();
      store.setOperation("ollama", "pulling");
      let result = null;
      try {
        result = await installerPullModel(model);
        store.updateModule("ollama", { lastInstallResult: result });
        if (result?.ok) {
          await STATUS_MODULES.ollama.check();
        } else {
          store.updateModule("ollama", {
            lastError: result?.message || "모델 다운로드 실패",
            operation: null,
          });
        }
        return result;
      } catch (e) {
        store.updateModule("ollama", {
          lastError: String(e),
          operation: null,
        });
        throw e;
      }
    },

    /** 특정 모델이 이미 설치되어 있는지 — store의 models 배열 검사 */
    hasModel(model) {
      const m = useStatusStore.getState().getModule("ollama");
      return hasModelInstalled(m.models, model);
    },
  },
};

/** 모든 모듈의 check()를 동시 실행 — 폴링/초기 로드에 사용 */
export async function refreshAllModules() {
  await Promise.all(
    Object.values(STATUS_MODULES).map((m) =>
      m.check().catch((e) => {
        // 개별 실패는 격리 — 다른 모듈 check에는 영향 없음
        console.warn(`[statusManager] ${m.id} check failed:`, e);
      })
    )
  );
}

/**
 * statusStore의 Ollama 모듈 상태를 `localAISetupCore.js`의 `buildPlan`/`isAllReady`가
 * 받는 diag 형태로 변환한다.
 */
export function getDerivedDiag() {
  const oll = useStatusStore.getState().modules.ollama;
  return {
    oll: {
      installed: oll.installed,
      running: oll.running,
      models: oll.models,
      version: oll.version,
    },
  };
}
