/**
 * toolSteps — 실행된 엑셀 액션 목록 → 툴 진행 스텝 칩 모델 (순수).
 *
 * 최종 와이어프레임 B-7의 `문서 형식 파악 완료` → `데이터 처리 완료` 칩이다.
 * sidecar가 주는 `executed_actions`([{action, params, result}])를 사람이 읽는
 * 한 줄로 옮긴다.
 *
 * 와이어프레임의 두 문구를 그대로 박지 않는 이유: 실제로 어떤 함수가 돌지는
 * LLM이 정하므로 문구가 고정이면 "데이터 처리 완료"라고 써놓고 실제로는 정렬만
 * 한 상황이 생긴다. 액션별 문구를 두고, 모르는 액션은 일반 문구로 떨어뜨린다.
 */

/** 액션 → 칩 문구. 여기 없는 액션은 GENERIC_LABEL로 떨어진다. */
const STEP_LABELS = Object.freeze({
  "excel_live.list_workbooks": "문서 형식 파악 완료",
  "excel_live.read_range": "데이터 읽기 완료",
  "excel_live.write_range": "데이터 입력 완료",
  "excel_live.group_by_aggregate": "데이터 집계 완료",
  "excel_live.calculate_column_stat": "통계 계산 완료",
  "excel_live.highlight_by_condition": "조건부 강조 완료",
  "excel_live.apply_border": "경계선 적용 완료",
  "excel_live.set_formula": "수식 적용 완료",
  "excel_live.filter_rows": "행 필터링 완료",
  "excel_live.sort_rows": "정렬 완료",
  "excel_live.dedupe_rows": "중복 데이터 처리 완료",
  "excel_live.drop_column": "열 삭제 완료",
  "excel_live.rename_column": "열 이름 변경 완료",
  "excel_live.add_column": "열 추가 완료",
  "excel_live.save_workbook": "문서 저장 완료",
});

export const GENERIC_LABEL = "데이터 처리 완료";

/** 액션 하나의 칩 문구. */
export function stepLabel(action) {
  return STEP_LABELS[action] ?? GENERIC_LABEL;
}

/**
 * executed_actions → 칩 모델 배열.
 *
 * 같은 액션이 연속으로 여러 번 실행되면 칩도 여러 개가 되는데, 같은 문구가
 * 줄줄이 쌓이면 진행이 아니라 고장으로 보인다. 연속 중복은 하나로 접는다.
 *
 * @param {Array<{action?: string}>} executed
 * @returns {Array<{id: string, label: string, done: boolean}>}
 */
export function toToolSteps(executed) {
  const list = Array.isArray(executed) ? executed : [];
  const steps = [];

  for (const item of list) {
    const label = stepLabel(item?.action);
    if (steps.length > 0 && steps[steps.length - 1].label === label) continue;
    // id는 위치 기반 — 액션명은 중복될 수 있어 key로 못 쓴다.
    steps.push({ id: `step-${steps.length}`, label, done: true });
  }

  return steps;
}
