import test from "node:test";
import assert from "node:assert/strict";

import {
  extractRangeTag,
  stripContextBlock,
  hasExplicitRange,
  applyRangeToCommand,
  stringifyTsv,
  buildRangeContextBlock,
  toOutboundCommand,
  TSV_MAX_ROWS,
  TSV_MAX_COLS,
} from "./excelRangeContext.js";

const BLOCK = [
  "[[EXCEL_RANGE:A1:C3]]",
  "[[EXCEL_VALUES_TSV]]",
  "제품군\t매출",
  "클라우드\t3200",
  "[[/EXCEL_VALUES_TSV]]",
].join("\n");

test("extractRangeTag — 태그에서 주소를 대문자로 뽑는다", () => {
  assert.equal(extractRangeTag(BLOCK), "A1:C3");
  assert.equal(extractRangeTag("[[excel_range:b2:d5]] 어쩌고"), "B2:D5");
  assert.equal(extractRangeTag("태그 없음"), null);
  assert.equal(extractRangeTag(""), null);
});

test("stripContextBlock — 태그와 값 블록을 모두 걷어낸다", () => {
  const input = `${BLOCK}\n\n이 범위 합계 알려줘`;
  assert.equal(stripContextBlock(input), "이 범위 합계 알려줘");
});

test("stripContextBlock — 값 블록이 여러 개여도 전부 제거", () => {
  const input = `${BLOCK}\n${BLOCK}\n질문`;
  assert.equal(stripContextBlock(input), "질문");
});

test("hasExplicitRange — 명령문에 주소가 이미 있는지", () => {
  assert.equal(hasExplicitRange("A1:C3 읽어줘"), true);
  assert.equal(hasExplicitRange("B2에 입력"), true);
  assert.equal(hasExplicitRange("A:A 정렬"), true);
  assert.equal(hasExplicitRange("합계 알려줘"), false);
});

test("applyRangeToCommand — 지시대명사가 있을 때만 주소를 앞에 세운다", () => {
  assert.equal(applyRangeToCommand("이 범위 합계", "A1:C3"), "A1:C3 이 범위 합계");
  assert.equal(applyRangeToCommand("여기 정렬해줘", "A1:C3"), "A1:C3 여기 정렬해줘");
  assert.equal(applyRangeToCommand("선택한 범위 지워", "A1:C3"), "A1:C3 선택한 범위 지워");
});

test("applyRangeToCommand — 주소를 직접 쓴 명령은 건드리지 않는다", () => {
  // 사용자가 명시한 범위가 삽입된 범위에 밀리면 엉뚱한 셀이 바뀐다.
  assert.equal(applyRangeToCommand("B5:B9 이 범위 합계", "A1:C3"), "B5:B9 이 범위 합계");
});

test("applyRangeToCommand — 범위와 무관한 명령은 그대로 둔다", () => {
  assert.equal(applyRangeToCommand("이 파일 요약해줘", "A1:C3"), "이 파일 요약해줘");
  assert.equal(applyRangeToCommand("안녕", "A1:C3"), "안녕");
  assert.equal(applyRangeToCommand("이 범위 합계", null), "이 범위 합계");
});

test("stringifyTsv — 상한까지만 탭/줄바꿈으로 직렬화", () => {
  assert.equal(stringifyTsv([["a", "b"], ["c", null]]), "a\tb\nc\t");
  assert.equal(stringifyTsv([]), "");
  assert.equal(stringifyTsv(null), "");

  const wide = Array.from({ length: 20 }, () =>
    Array.from({ length: 20 }, (_, c) => `c${c}`)
  );
  const out = stringifyTsv(wide);
  assert.equal(out.split("\n").length, TSV_MAX_ROWS);
  assert.equal(out.split("\n")[0].split("\t").length, TSV_MAX_COLS);
});

test("buildRangeContextBlock — 블록 형태를 고정한다", () => {
  const { block, address, rows, cols } = buildRangeContextBlock({
    address: "a1:b2",
    row_count: 2,
    col_count: 2,
    values: [["제품", "매출"], ["클라우드", 3200]],
  });
  assert.equal(address, "A1:B2");
  assert.equal(rows, 2);
  assert.equal(cols, 2);
  assert.ok(block.startsWith("[[EXCEL_RANGE:A1:B2]]"));
  assert.ok(block.includes("제품\t매출"));
  assert.ok(block.trimEnd().endsWith("[[/EXCEL_VALUES_TSV]]"));
  // 상한 이내면 잘림 안내가 붙지 않아야 한다.
  assert.equal(block.includes("미리보기 제한"), false);
});

test("buildRangeContextBlock — 상한을 넘으면 안내 줄이 붙는다", () => {
  const { block } = buildRangeContextBlock({
    address: "A1:Z100",
    row_count: 100,
    col_count: 26,
    values: [["x"]],
  });
  assert.ok(block.includes("미리보기 제한"));
  assert.ok(block.includes("실제 범위 100행 x 26열"));
});

test("buildRangeContextBlock — 빈 범위와 주소 누락 처리", () => {
  const { block } = buildRangeContextBlock({ address: "A1", row_count: 0, col_count: 0 });
  assert.ok(block.includes("(빈 범위)"));
  assert.throws(() => buildRangeContextBlock({}), /선택 범위 주소/);
});

test("toOutboundCommand — 블록을 떼고 주소를 반영한 최종 명령문", () => {
  assert.equal(toOutboundCommand(`${BLOCK}\n\n이 범위 합계 알려줘`), "A1:C3 이 범위 합계 알려줘");
  // 블록이 없으면 원문 그대로
  assert.equal(toOutboundCommand("  안녕하세요  "), "안녕하세요");
});

test("toOutboundCommand — 블록만 있고 명령문이 없으면 빈 문자열이 아니라 원문으로 폴백", () => {
  // 빈 문자열을 sidecar로 보내면 의미 없는 오류가 난다.
  const out = toOutboundCommand(BLOCK);
  assert.ok(out.length > 0);
});
