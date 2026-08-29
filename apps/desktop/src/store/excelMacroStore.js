/**
 * Excel 매크로 진행 상태 저장소.
 *
 * "대시보드 만들어줘" 한 문장이 여러 하위 명령으로 펼쳐졌을 때, 그 계획과 진행 상황을
 * 여기 한 곳에 둔다. 채팅 화면은 구독만 하고 진행 로직은 excelMacroManager가 맡는다.
 *
 * ## 상태 흐름
 *
 *   idle          — 매크로 없음
 *   planned       — 계획을 받아 승인 대기
 *   running       — 한 단계씩 실행 중
 *   waiting_input — 하위 명령이 되물어서 답을 기다리는 중
 *   halted        — 실패해서 멈춤. 이어서/되돌리기/멈추기 선택 대기
 *   done          — 전부 끝남
 *   aborted       — 사용자가 중단
 */
import { create } from "zustand";

/** @typedef {'idle'|'planned'|'running'|'waiting_input'|'halted'|'done'|'aborted'} MacroStatus */

/**
 * @typedef {object} MacroStep
 * @property {number} index 1-based 순번
 * @property {string} command 사용자에게 보여줄 하위 명령 문장
 * @property {boolean} destructive 데이터를 지울 가능성이 있는지
 * @property {string[]} warnings 계획 검증에서 걸린 주의사항
 * @property {'pending'|'done'|'failed'|'skipped'} status
 * @property {string} action 실행 후 확정된 액션명
 * @property {string} detail 실행 결과나 실패 사유
 */

/** 매크로가 없을 때의 초기값 */
function emptyMacro() {
  return {
    /** @type {string} */
    macroId: "",
    /** @type {MacroStatus} */
    status: "idle",
    /** @type {string} */
    originalMessage: "",
    /** @type {MacroStep[]} */
    steps: [],
    /** @type {number} */
    total: 0,
    /** @type {number} */
    completed: 0,
    /** @type {number} 지금 실행 중이거나 멈춰 선 단계 번호 (1-based) */
    cursor: 0,
    /** @type {number[]} 승인 화면에서 체크를 해제한 단계 번호 */
    skipIndices: [],
    /** @type {string} 되묻기 질문 */
    followUpQuestion: "",
    /** @type {string} 실패 사유 */
    failureReason: "",
    /** @type {string} 매크로 시작 시점 백업 경로 (되돌리기 가능 여부) */
    backupPath: "",
    /** @type {boolean} 요청이 진행 중인지 — 버튼 중복 클릭 방지용 */
    busy: false,
  };
}

const useExcelMacroStore = create((set) => ({
  ...emptyMacro(),

  /** 사이드카가 준 매크로 스냅샷을 그대로 반영한다 */
  applySnapshot: (/** @type {Partial<ReturnType<emptyMacro>>} */ patch) => set(patch),

  /** 승인 화면에서 항목 체크를 토글한다 */
  toggleSkip: (/** @type {number} */ index) =>
    set((state) => ({
      skipIndices: state.skipIndices.includes(index)
        ? state.skipIndices.filter((value) => value !== index)
        : [...state.skipIndices, index],
    })),

  setBusy: (/** @type {boolean} */ busy) => set({ busy }),

  /** 매크로를 치운다 (완료·중단·취소 공통) */
  reset: () => set(emptyMacro()),
}));

export default useExcelMacroStore;
