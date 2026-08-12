import test from "node:test";
import assert from "node:assert/strict";

import { GENERIC_LABEL, stepLabel, toToolSteps } from "./toolSteps.js";

test("stepLabel: 아는 액션은 전용 문구", () => {
  assert.equal(stepLabel("excel_live.dedupe_rows"), "중복 데이터 처리 완료");
  assert.equal(stepLabel("excel_live.read_range"), "데이터 읽기 완료");
});

test("stepLabel: 모르는 액션은 일반 문구로 떨어진다", () => {
  // 새 함수가 sidecar에 추가돼도 UI가 빈 칩을 그리면 안 된다.
  assert.equal(stepLabel("excel_live.brand_new_thing"), GENERIC_LABEL);
  assert.equal(stepLabel(undefined), GENERIC_LABEL);
});

test("toToolSteps: 실행 순서대로 칩을 만든다", () => {
  const steps = toToolSteps([
    { action: "excel_live.list_workbooks" },
    { action: "excel_live.read_range" },
    { action: "excel_live.dedupe_rows" },
  ]);
  assert.deepEqual(
    steps.map((s) => s.label),
    ["문서 형식 파악 완료", "데이터 읽기 완료", "중복 데이터 처리 완료"]
  );
  assert.ok(steps.every((s) => s.done));
});

test("toToolSteps: 연속 중복은 하나로 접는다", () => {
  // 같은 문구가 줄줄이 쌓이면 진행이 아니라 고장으로 보인다.
  const steps = toToolSteps([
    { action: "excel_live.write_range" },
    { action: "excel_live.write_range" },
    { action: "excel_live.write_range" },
    { action: "excel_live.sort_rows" },
  ]);
  assert.deepEqual(
    steps.map((s) => s.label),
    ["데이터 입력 완료", "정렬 완료"]
  );
});

test("toToolSteps: 떨어져 있는 같은 액션은 접지 않는다", () => {
  // 읽기 → 정렬 → 읽기는 실제로 두 번 읽은 것이므로 둘 다 보여야 한다.
  const steps = toToolSteps([
    { action: "excel_live.read_range" },
    { action: "excel_live.sort_rows" },
    { action: "excel_live.read_range" },
  ]);
  assert.equal(steps.length, 3);
});

test("toToolSteps: id는 서로 다르다", () => {
  const steps = toToolSteps([
    { action: "excel_live.read_range" },
    { action: "excel_live.sort_rows" },
    { action: "excel_live.read_range" },
  ]);
  assert.equal(new Set(steps.map((s) => s.id)).size, steps.length);
});

test("toToolSteps: 입력이 없거나 깨져도 빈 배열", () => {
  for (const input of [null, undefined, "nope", []]) {
    assert.deepEqual(toToolSteps(input), []);
  }
});
