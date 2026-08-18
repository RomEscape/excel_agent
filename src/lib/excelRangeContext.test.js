import test from "node:test";
import assert from "node:assert/strict";

import {
  applyRangeContextToCommand,
  hasExplicitRangeInCommand,
  looksLikeValueListWrite,
} from "./excelRangeContext.js";

test("좌표를 부른 문장은 범위가 있다", () => {
  assert.equal(hasExplicitRangeInCommand("A1:F6에 테두리"), true);
  assert.equal(hasExplicitRangeInCommand("b7에 합계 넣어줘"), true);
  assert.equal(hasExplicitRangeInCommand("표 전체 테두리"), false);
});

test("값 나열 안의 셀 닮은 토큰은 범위가 아니다", () => {
  const paste =
    "No,구분,자재코드,품목,현재수량; 1,자재,STL-400,철근 (D25),12.3톤; 9,자재,THK-050,단열재 (T100),310㎡ 입력해줘";
  assert.equal(looksLikeValueListWrite(paste), true);
  assert.equal(hasExplicitRangeInCommand(paste), false);
});

test("값 나열이라도 대상 범위를 불렀으면 범위가 있다", () => {
  assert.equal(hasExplicitRangeInCommand("A35:E37에 No,구분,자재코드; 1,자재,STL-400 입력"), true);
  assert.equal(hasExplicitRangeInCommand("B3부터 a,b,c; d,e,f 넣어줘"), true);
});

test("탭·줄바꿈으로 된 표(TSV) + 동사도 값 나열이다", () => {
  const tsv = "지역\t주문건수\n수도권\t10452\n충청권\t3892 입력해줘";
  assert.equal(looksLikeValueListWrite(tsv), true);
  assert.equal(hasExplicitRangeInCommand(tsv), false);
});

test("여기 지시어에는 붙여넣기 범위를 접두한다", () => {
  assert.equal(applyRangeContextToCommand("여기에 표 만들어줘", "A1:D9"), "A1:D9 여기에 표 만들어줘");
  assert.equal(applyRangeContextToCommand("정렬 좀", "A1:D9"), "정렬 좀");
  assert.equal(applyRangeContextToCommand("A2:B3 여기에 넣어줘", "A1:D9"), "A2:B3 여기에 넣어줘");
});
