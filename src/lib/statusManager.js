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
  openclawStatus,
  openclawInstalled,
  openclawEnsureRunning,
  ollamaStatus,
  installerInstallOpenClaw,
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
 */
export const STATUS_MODULES = {
  // ── OpenClaw 게이트웨이 ────────────────────────────────────────────────────
  openclaw: {
    id: "openclaw",
    label: "OpenClaw 게이트웨이",

    /**
     * 설치/실행 상태 확인.
     *
     *   1) `openclaw --version` 가능 여부 (Rust is_openclaw_installed가 $SHELL -lc 폴백 포함)
     *   2) 18789 TCP 응답 여부
     *
     * 두 정보를 합쳐 state 결정.
     */
    async check() {
      const store = useStatusStore.getState();
      store.setOperation("openclaw", "checking");
      try {
        const [statusRes, installedRes] = await Promise.all([
          openclawStatus().catch(() => ({ state: "error", message: "조회 실패" })),
          openclawInstalled().catch(() => ({ installed: false })),
        ]);

        // Windows에서 GUI 런타임 PATH 이슈로 `openclaw --version` 감지가
        // 실패하는 경우가 있다. 게이트웨이가 실제 running이면 설치된 것으로 본다.
        const installed = !!installedRes?.installed || statusRes?.state === "running";
        const running = statusRes?.state === "running";

        store.updateModule("openclaw", {
          installed,
          version: installedRes?.version ?? null,
          running,
          port: statusRes?.port ?? 18789,
          message: statusRes?.message ?? "",
          state: deriveState({ installed, running }),
          lastChecked: Date.now(),
          lastError: null,
          operation: null,
        });
        return store.getModule("openclaw");
      } catch (e) {
        store.updateModule("openclaw", {
          state: "error",
          lastError: String(e),
          lastChecked: Date.now(),
          operation: null,
        });
        return store.getModule("openclaw");
      }
    },

    /**
     * `npm install -g openclaw@latest` (Rust $SHELL -lc 경유).
     *
     * 흐름: install → check (설치 위치 확인) → store 자동 갱신.
     * 실패 시 lastInstallResult에 stderr 꼬리/eacces 플래그/manual_command 저장.
     */
    async install() {
      const store = useStatusStore.getState();
      store.setOperation("openclaw", "installing");
      let result = null;
      try {
        result = await installerInstallOpenClaw();
        store.updateModule("openclaw", { lastInstallResult: result });

        if (result?.ok) {
          // 설치 직후 즉시 check로 설치 위치 확인 (사용자가 묘사한 패턴)
          await STATUS_MODULES.openclaw.check();
        } else {
          store.updateModule("openclaw", {
            lastError: result?.message || "설치 실패",
            operation: null,
          });
        }
        return result;
      } catch (e) {
        store.updateModule("openclaw", {
          lastError: String(e),
          operation: null,
        });
        throw e;
      }
    },

    /**
     * 게이트웨이 시작 (`openclaw gateway --port 18789` Rust 자식 spawn).
     * spawn_openclaw가 이미 $SHELL -lc 폴백 + ready 30s polling 포함.
     */
    async start() {
      const store = useStatusStore.getState();
      store.setOperation("openclaw", "starting");
      try {
        const result = await openclawEnsureRunning();
        const running = result?.state === "running";
        store.updateModule("openclaw", {
          running,
          port: result?.port ?? 18789,
          message: result?.message ?? "",
          state: running ? "running_healthy" : "error",
          lastChecked: Date.now(),
          operation: null,
        });
        if (!running) {
          store.updateModule("openclaw", {
            lastError: result?.message || "게이트웨이 ready 실패",
          });
        }
        return result;
      } catch (e) {
        store.updateModule("openclaw", {
          state: "error",
          lastError: String(e),
          operation: null,
        });
        throw e;
      }
    },
  },

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
     * `brew install ollama` (macOS만). Rust installer가 $SHELL -lc 경유.
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
     * `brew services start ollama`.
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

/** 단일 모듈의 check만 실행 (특정 모듈 변경 후 빠른 갱신용) */
export async function refreshModule(id) {
  const mod = STATUS_MODULES[id];
  if (!mod) return null;
  return mod.check();
}

/**
 * statusStore의 모듈 상태를 기존 `localAISetup.js`의 `buildPlan`/`isAllReady`가
 * 받는 diag 형태로 변환.
 *
 * 마이그레이션 호환성용 — 새 코드는 store에서 직접 읽는 것을 권장.
 */
export function getDerivedDiag() {
  const oc = useStatusStore.getState().modules.openclaw;
  const oll = useStatusStore.getState().modules.ollama;
  return {
    oc: {
      state: oc.running ? "running" : "stopped",
      message: oc.message,
      port: oc.port,
    },
    ocInstalled: { installed: oc.installed, version: oc.version },
    oll: {
      installed: oll.installed,
      running: oll.running,
      models: oll.models,
      version: oll.version,
    },
  };
}
