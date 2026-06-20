import test from "node:test";
import assert from "node:assert/strict";
import { splitExcelCompositeCommand } from "./excelCommandUtils.js";

test("splitExcelCompositeCommand: 단일 명령은 그대로 반환", () => {
  const out = splitExcelCompositeCommand("C3에 777 입력해줘");
  assert.deepEqual(out, ["C3에 777 입력해줘"]);
});

test("splitExcelCompositeCommand: 그리고/다음으로 분해", () => {
  const out = splitExcelCompositeCommand(
    "C3에 777 입력해줘 그리고 D열에서 0 이하를 파란색 표시 다음으로 엑셀 저장해줘",
  );
  assert.deepEqual(out, [
    "C3에 777 입력해줘",
    "D열에서 0 이하를 파란색 표시",
    "엑셀 저장해줘",
  ]);
});

test("splitExcelCompositeCommand: 영문 then 분해", () => {
  const out = splitExcelCompositeCommand("set C3 777 then save workbook");
  assert.deepEqual(out, ["set C3 777", "save workbook"]);
});

test("splitExcelCompositeCommand: 한국어 동사 연결형 분해", () => {
  const out = splitExcelCompositeCommand(
    "C3에 777 입력하고 D열 0 이하는 파란색 표시한 다음 엑셀 저장해줘",
  );
  assert.deepEqual(out, [
    "C3에 777 입력",
    "D열 0 이하는 파란색 표시",
    "엑셀 저장해줘",
  ]);
});

