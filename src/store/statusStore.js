/**
 * 시스템 상태 중앙 저장소.
 *
 * 사용자 요청: "각 모듈이 상태를 체크해서 가지고 있으면서 그걸 대시보드에서 보여주는 형식".
 *
 * 모듈별 슬롯(`modules.openclaw`, `modules.ollama`, ...)에 동일한 형태의 상태 객체를 보관:
 *
 *   {
 *     state: ModuleState,
 *     installed: boolean,
 *     version: string|null,
 *     running: boolean,
 *     port: number|null,
 *     models: Array,            // ollama만 사용
 *     message: string,
 *     operation: Operation|null, // 진행 중인 작업
 *     lastChecked: number|null,  // epoch ms
 *     lastError: string|null,
 *     lastInstallResult: object|null,  // 마지막 install/start 명령의 InstallResult (실패 컨텍스트)
 *   }
 *
 * ## State machine
 *
 *   unknown         — 아직 한 번도 check 안 함 (초기 상태)
 *   not_installed   — 바이너리/패키지가 시스템에 없음 — install 필요
 *   installed_stopped — 설치는 됐는데 데몬/게이트웨이가 안 돔 — start 필요
 *   running_healthy   — 설치 + 실행 + health check 통과 (정상)
 *   running_unhealthy — 실행 중이지만 응답이 이상함 (드물게)
 *   error           — check 자체가 실패 (sidecar 미응답 등)
 *
 * ## Operation
 *
 *   null         — idle
 *   "checking"   — check() 진행 중
 *   "installing" — install() 진행 중
 *   "starting"   — start() 진행 중
 *   "pulling"    — ollama 모델 pull 진행 중
 *
 * UI는 operation이 null이 아니면 버튼 비활성화 등으로 동시 작업 방지.
 */
import { create } from "zustand";

/** @typedef {'unknown'|'not_installed'|'installed_stopped'|'running_healthy'|'running_unhealthy'|'error'} ModuleState */
/** @typedef {null|'checking'|'installing'|'starting'|'pulling'} Operation */

/** 빈 모듈 슬롯 — 새 모듈 추가 시 기본값으로 사용 */
function emptyModule() {
  return {
    state: "unknown",
    installed: false,
    version: null,
    running: false,
    port: null,
    models: [],
    message: "",
    reasonCode: null,
    operation: null,
    lastChecked: null,
    lastError: null,
    lastInstallResult: null,
  };
}

/**
 * check() 결과 필드에서 state 머신 값을 도출.
 * 모듈마다 약간씩 다른 health 기준이 있어 module 정의 쪽에서도 override 가능.
 */
export function deriveState({ installed, running, healthy = null }) {
  if (!installed) return "not_installed";
  if (!running) return "installed_stopped";
  if (healthy === false) return "running_unhealthy";
  return "running_healthy";
}

const useStatusStore = create((set, get) => ({
  /**
   * 모듈 슬롯 — 알려진 모듈 ID로만 인덱싱.
   * 새 모듈을 추가하려면 여기 초기값 + statusManager.js에 정의 추가.
   */
  modules: {
    openclaw: emptyModule(),
    ollama: emptyModule(),
  },

  /**
   * 모듈 상태를 일부 갱신 (partial update).
   * 호출자는 변경된 필드만 넘기면 됨.
   */
  updateModule: (id, patch) =>
    set((s) => ({
      modules: {
        ...s.modules,
        [id]: { ...(s.modules[id] || emptyModule()), ...patch },
      },
    })),

  /** 진행 중인 작업 표시 — null이면 idle */
  setOperation: (id, operation) =>
    set((s) => ({
      modules: {
        ...s.modules,
        [id]: { ...(s.modules[id] || emptyModule()), operation },
      },
    })),

  /** 모듈을 unknown으로 리셋 (드물게 사용 — 디버그용) */
  resetModule: (id) =>
    set((s) => ({
      modules: { ...s.modules, [id]: emptyModule() },
    })),

  /** 헬퍼: 단일 모듈 상태 읽기 */
  getModule: (id) => get().modules[id] || emptyModule(),
}));

export default useStatusStore;
