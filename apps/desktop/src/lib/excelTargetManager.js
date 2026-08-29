// 지금 에이전트가 대상으로 삼는 통합문서·시트·선택 영역을 한 곳에서 소유한다.
//
// 왜 필요한가 (2026-08-16):
//   사용자가 "엑셀 편집중인 파일을 에이전트에 띄워서 지금 정확히 어떤 파일을
//   편집하는지 인지하게끔 하는 게 낫지 않나?"라고 물었다. 맞는 지적이다 —
//   지금까지 화면 어디에도 대상 파일이 안 보였고, 그래서 드래그가 안 먹던
//   버그도 사용자가 "인지가 안 되는 것 같다"로만 표현할 수밖에 없었다.
//   무엇을 보고 있는지 보이면 어긋났을 때 바로 드러난다.
//
// CLAUDE.md §4: 상태는 모듈이 소유하고 UI는 구독해서 읽기만 한다.
// 중복 fetch 금지 — 이 매니저 하나만 사이드카에 묻는다.

import { excelLiveStatus, excelLiveCommand } from "./api.js";

const listeners = new Set();

let state = {
  available: false,
  engine: "",
  workbookName: "",
  sheetName: "",
  selection: "",
  readOnly: false,
  checkedAt: 0,
  error: "",
};

function emit(next) {
  state = { ...state, ...next, checkedAt: Date.now() };
  for (const fn of listeners) fn(state);
}

export function getExcelTarget() {
  return state;
}

export function subscribeExcelTarget(fn) {
  listeners.add(fn);
  fn(state);
  return () => listeners.delete(fn);
}

/** 사이드카에 현재 대상을 묻는다. 실패해도 던지지 않는다 — 표시용이다. */
export async function refreshExcelTarget() {
  try {
    const status = await excelLiveStatus();
    const books = Array.isArray(status?.workbooks) ? status.workbooks : [];
    const first = books[0] || {};
    emit({
      available: Boolean(status?.available),
      engine: String(status?.engine || ""),
      // 사이드카가 name/id 중 무엇을 주는지 버전마다 달라서 둘 다 본다.
      workbookName: String(first?.name || first?.id || ""),
      error: "",
    });
  } catch (err) {
    // 사이드카가 죽어 있어도 화면은 살아 있어야 한다.
    emit({ available: false, workbookName: "", error: String(err?.message || err) });
  }
  return state;
}

/** 지금 끌어 둔 영역까지 읽는다. COM 왕복이 있으니 사용자가 눌렀을 때만 부른다. */
export async function refreshExcelSelection(sessionId) {
  try {
    const out = await excelLiveCommand(
      "지금 선택한 범위 읽어줘",
      null,
      null,
      sessionId,
      false,
    );
    const address = String(out?.result?.address || "").toUpperCase();
    if (address) emit({ selection: address });
  } catch {
    // 선택을 못 읽는 건 치명적이지 않다.
  }
  return state;
}

/** 명령 응답에서 알게 된 것을 반영한다(별도 조회 없이). */
export function noteExcelTargetFromResult(excelResult) {
  const result = excelResult?.result || {};
  const next = {};
  const address = String(result.address || "").toUpperCase();
  if (address) next.selection = address;
  if (result.sheet_name) next.sheetName = String(result.sheet_name);
  if (result.workbook_name) next.workbookName = String(result.workbook_name);
  // 사전 점검이 읽기 전용이라고 알려 준 경우.
  if (excelResult?.reason && String(excelResult.reason).includes("읽기 전용")) {
    next.readOnly = true;
  } else if (excelResult?.ok) {
    next.readOnly = false;
  }
  if (Object.keys(next).length) emit(next);
}
