import test from "node:test";
import assert from "node:assert/strict";

import { splitExcelCompositeCommand } from "./excelCommandUtils.js";

test("접속사로 복합문을 나눈다", () => {
  assert.deepEqual(splitExcelCompositeCommand("C3에 777 입력하고 D열 굵게 해줘"), [
    "C3에 777 입력",
    "D열 굵게 해줘",
  ]);
});

test("여러 줄로 붙여넣은 값 나열은 줄바꿈을 살린 한 명령이다", () => {
  // 줄이 곧 행이다 — 뭉개면 사이드카가 한 줄로 받아 칸이 밀린다.
  const out = splitExcelCompositeCommand("지역,주문건수\r\n수도권,10452\n충청권,3892 입력해줘");
  assert.deepEqual(out, ["지역,주문건수\n수도권,10452\n충청권,3892 입력해줘"]);
});

test("가로 공백만 하나로 줄인다(탭은 TSV 칸 구분이라 남긴다)", () => {
  assert.deepEqual(splitExcelCompositeCommand("  표   전체에   테두리  "), ["표 전체에 테두리"]);
});

test("빈 문장은 빈 배열", () => {
  assert.deepEqual(splitExcelCompositeCommand("   \n  "), []);
});

test("탭(TSV 칸 구분)도 살린다", () => {
  const out = splitExcelCompositeCommand("지역\t주문건수\n수도권\t10452\n입력해줘");
  assert.deepEqual(out, ["지역\t주문건수\n수도권\t10452\n입력해줘"]);
});
