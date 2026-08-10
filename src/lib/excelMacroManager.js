/**
 * Excel 매크로 도메인 액션.
 *
 * 매크로는 사이드카가 소유한다 — 여기서 하는 일은 승인·진행·중단을 사이드카에 전달하고
 * 돌아온 스냅샷을 store에 넣는 것뿐이다. 하위 명령을 프론트가 쪼개거나 순서를 정하지
 * 않는다. 그건 이미 splitExcelCompositeCommand가 저지른 실수다.
 */

import { excelLiveMacroAbort, excelLiveMacroStep } from "@/lib/api";
import useExcelMacroStore from "@/store/excelMacroStore";

/** 한 번의 진행 요청이 겹쳐 들어오지 않게 붙잡아 두는 promise */
let inflight = null;

/**
 * 사이드카 스냅샷(snake_case)을 store 모양(camelCase)으로 옮긴다.
 *
 * @param {Record<string, any>} result
 * @param {string} reason
 */
function toStorePatch(result, reason = "") {
  const steps = Array.isArray(result?.steps) ? result.steps : [];
  const status = String(result?.status || "idle");
  return {
    macroId: String(result?.macro_id || ""),
    status,
    steps: steps.map((step) => ({
      index: Number(step?.index || 0),
      command: String(step?.command || ""),
      destructive: Boolean(step?.destructive),
      warnings: Array.isArray(step?.warnings) ? step.warnings.map(String) : [],
      status: String(step?.status || "pending"),
      action: String(step?.action || ""),
      detail: String(step?.detail || ""),
    })),
    total: Number(result?.total || steps.length),
    completed: Number(result?.completed || 0),
    cursor: Number(result?.cursor || 0),
    followUpQuestion: String(result?.follow_up_question || ""),
    failureReason: status === "halted" ? String(reason || "") : "",
    backupPath: String(result?.backup_path || ""),
  };
}

/**
 * 계획 응답(`excel_live.macro_plan`)을 승인 대기 상태로 올린다.
 *
 * @param {Record<string, any>} result /excel-live/command 응답의 result
 * @param {string} originalMessage 사용자가 실제로 입력한 문장
 */
export function startMacroPlan(result, originalMessage = "") {
  const store = useExcelMacroStore.getState();
  store.reset();
  store.applySnapshot({
    ...toStorePatch(result),
    status: "planned",
    originalMessage: String(originalMessage || result?.original_message || ""),
    skipIndices: [],
  });
}

/**
 * 한 단계를 진행하고 스냅샷을 반영한다.
 *
 * @param {{ skipIndices?: number[], answer?: string | null, skipCurrent?: boolean }} options
 * @returns {Promise<Record<string, any>>} 사이드카 응답
 */
async function stepOnce(options) {
  const { macroId } = useExcelMacroStore.getState();
  if (!macroId) throw new Error("진행 중인 매크로가 없습니다.");
  const response = await excelLiveMacroStep(macroId, options);
  const result = response?.result || {};
  useExcelMacroStore.getState().applySnapshot(toStorePatch(result, response?.reason));
  return response;
}

/**
 * 승인된 매크로를 끝까지(혹은 멈출 때까지) 진행한다.
 *
 * 사이드카가 상태를 소유하므로 여기서는 "아직 running인가"만 보고 다음 걸음을 당긴다.
 *
 * @param {{ answer?: string | null, skipCurrent?: boolean }} [resume]
 * @returns {Promise<Record<string, any> | null>} 마지막 응답
 */
export async function runMacro(resume = {}) {
  if (inflight) return inflight;

  inflight = (async () => {
    const store = useExcelMacroStore.getState();
    store.setBusy(true);
    let last = null;
    try {
      // 첫 걸음에만 제외 항목과 재개 정보를 싣는다. 이후는 순수 진행이다.
      let options = {
        skipIndices: store.status === "planned" ? store.skipIndices : [],
        answer: resume.answer ?? null,
        skipCurrent: resume.skipCurrent ?? false,
      };

      // 상한을 두는 이유: 사이드카가 예상 못 한 상태로 굳으면 무한 루프가 된다.
      const maxIterations = Math.max(1, store.total || 1) + 2;
      for (let attempt = 0; attempt < maxIterations; attempt += 1) {
        last = await stepOnce(options);
        options = { skipIndices: [], answer: null, skipCurrent: false };
        const status = useExcelMacroStore.getState().status;
        if (status !== "running") break;
      }
      return last;
    } finally {
      useExcelMacroStore.getState().setBusy(false);
      inflight = null;
    }
  })();

  return inflight;
}

/**
 * 되묻기에 답하고 멈춘 단계부터 이어서 진행한다.
 *
 * @param {string} answer
 */
export async function answerMacroFollowUp(answer) {
  return runMacro({ answer: String(answer || "").trim() });
}

/** 멈춰 선 단계를 건너뛰고 나머지를 계속한다 */
export async function continueMacroSkippingCurrent() {
  return runMacro({ skipCurrent: true });
}

/**
 * 매크로를 중단한다.
 *
 * @param {{ rollback?: boolean }} [options] rollback이면 매크로 시작 시점으로 되돌린다
 */
export async function abortMacro(options = {}) {
  const { macroId } = useExcelMacroStore.getState();
  if (!macroId) return null;
  useExcelMacroStore.getState().setBusy(true);
  try {
    const response = await excelLiveMacroAbort(macroId, Boolean(options.rollback));
    return response;
  } finally {
    useExcelMacroStore.getState().reset();
  }
}

/** 승인 화면에서 취소를 눌렀을 때 — 서버 쪽 대기 상태까지 치운다 */
export async function cancelMacroPlan() {
  return abortMacro({ rollback: false });
}

/** 완료된 매크로 카드를 화면에서 내린다 */
export function dismissMacro() {
  useExcelMacroStore.getState().reset();
}
