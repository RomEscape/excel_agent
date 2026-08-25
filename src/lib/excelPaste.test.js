import test from "node:test";
import assert from "node:assert/strict";

import {
  buildPasteBlock,
  displayMessageText,
  isExcelSelectionPaste,
  looksLikeExcelPaste,
  pasteHasValues,
  pasteShape,
  rangeShape,
} from "./excelPaste.js";

// Excel에서 A1:D3을 복사하면 이런 모양으로 붙는다.
const EXCEL_TABLE = "날짜\t지역\t담당자\t금액\n2026-01-01\t서울\t김철수\t120000\n2026-01-02\t경기\t이영희\t85000";

test("엑셀 표 붙여넣기를 알아본다", () => {
  assert.equal(looksLikeExcelPaste(EXCEL_TABLE), true);
});

test("한 줄이어도 여러 칸이면 표로 본다", () => {
  assert.equal(looksLikeExcelPaste("서울\t120000\t완료"), true);
});

test("CRLF 줄바꿈도 처리한다", () => {
  assert.equal(looksLikeExcelPaste("a\tb\r\nc\td"), true);
});

test("평범한 문장은 표가 아니다", () => {
  assert.equal(looksLikeExcelPaste("매출 시트 정렬해줘"), false);
  assert.equal(looksLikeExcelPaste(""), false);
  assert.equal(looksLikeExcelPaste(null), false);
});

test("탭이 하나 섞인 산문은 표가 아니다", () => {
  // 열 수가 들쭉날쭉하면 표로 보지 않는다 — 오탐이 나면 사용자의 문장이 사라진다.
  assert.equal(looksLikeExcelPaste("이건 설명\t그리고 다음 줄\n한 칸짜리"), false);
});

test("탭 없는 여러 줄은 표가 아니다", () => {
  assert.equal(looksLikeExcelPaste("첫 줄\n둘째 줄\n셋째 줄"), false);
});

test("붙여넣은 표의 크기를 센다", () => {
  assert.deepEqual(pasteShape(EXCEL_TABLE), { rows: 3, cols: 4 });
});

test("주소를 알면 범위 참조로 바꾼다", () => {
  const out = buildPasteBlock(EXCEL_TABLE, "a1:d3");
  assert.match(out, /\[\[EXCEL_RANGE:A1:D3\]\]/);
  assert.match(out, /3행 × 4열/);
  // 값 자체는 넣지 않는다 — 백엔드가 실제 워크북에서 읽는다.
  assert.equal(out.includes("김철수"), false);
});

// 2026-08-17 실측(스크린샷): 붙여넣은 텍스트로 세면 빈 줄이 걸러져
// "9행 × 4열 — A1:D13"처럼 서로 안 맞는 숫자가 나갔다. 사용자: "인식되는 범위도 다르고".
test("안내 문구의 행×열은 범위와 항상 일치한다", () => {
  // 3줄짜리 텍스트여도 범위가 A1:D13이면 13행이라고 말해야 한다.
  const out = buildPasteBlock(EXCEL_TABLE, "A1:D13");
  assert.match(out, /13행 × 4열 — A1:D13/);
});

test("범위에서 행×열을 계산한다", () => {
  assert.deepEqual(rangeShape("A1:D13"), { rows: 13, cols: 4 });
  assert.deepEqual(rangeShape("b2:c4"), { rows: 3, cols: 2 });
  assert.equal(rangeShape("B2"), null); // 한 칸은 텍스트 기준으로 센다
  assert.equal(rangeShape(""), null);
});

test("주소를 못 알아내면 붙여넣은 내용을 그대로 둔다", () => {
  // Excel이 꺼져 있거나 다른 앱에서 복사한 경우다. 사용자 입력을 잃으면 안 된다.
  assert.equal(buildPasteBlock(EXCEL_TABLE, ""), EXCEL_TABLE);
  assert.equal(buildPasteBlock(EXCEL_TABLE, null), EXCEL_TABLE);
});

// 2026-08-17 실측(스크린샷): 마크업이 사용자 말풍선에 그대로 떴다.
test("말풍선에는 마크업 대신 사람용 문구를 쓴다", () => {
  const raw = `${buildPasteBlock(EXCEL_TABLE, "A1:D9")} 이 부분은 원래대로 초기화해줄 수 있어? 표 없애줘`;
  const shown = displayMessageText(raw);
  assert.equal(shown.includes("[[EXCEL_"), false, "마크업이 그대로 노출된다");
  // 행×열은 범위(A1:D9) 기준이다 — 텍스트 기준(3행)과 어긋나면 안 된다.
  assert.match(shown, /엑셀에서 붙여넣은 9행 × 4열/);
  assert.match(shown, /표 없애줘/);
});

test("마크업이 없는 문장은 그대로 둔다", () => {
  assert.equal(displayMessageText("매출 시트 정렬해줘"), "매출 시트 정렬해줘");
  assert.equal(displayMessageText(""), "");
});

test("범위 태그만 있는 메시지도 안내 문구는 남긴다", () => {
  const shown = displayMessageText(buildPasteBlock(EXCEL_TABLE, "A1:D3"));
  assert.match(shown, /A1:D3 범위로 인식했습니다/);
  assert.equal(shown.includes("[[EXCEL_"), false);
});

// ---- 2026-08-19: 다른 앱·통합문서에서 가져온 표는 값을 살려 보낸다 ----
test("빈 셀만 복사한 붙여넣기는 값이 없다", () => {
  assert.equal(pasteHasValues("\t\t\n\t\t\n"), false);
  assert.equal(pasteHasValues("지역\t주문건수\n수도권\t10452"), true);
});

test("값을 살릴 때는 탭·줄바꿈 그대로 표를 이어 붙인다", () => {
  const out = buildPasteBlock("지역\t주문건수\r\n수도권\t10452\r\n", "A1", { keepValues: true });
  const lines = out.split("\n");
  assert.equal(lines[0], "[[EXCEL_RANGE:A1]]");
  assert.match(lines[1], /밖에서 가져온 표 2행 × 2열 — A1부터/);
  assert.deepEqual(lines.slice(2), ["지역\t주문건수", "수도권\t10452"]);
  // 말풍선에는 안내와 값이 보이고 마크업은 안 보인다.
  const shown = displayMessageText(`${out} 입력해줘`);
  assert.equal(shown.includes("[[EXCEL_"), false);
  assert.match(shown, /^📋 밖에서 가져온 표/);
  assert.match(shown, /수도권\t10452/);
});

test("값을 살리지 않는 기본 동작은 그대로다", () => {
  const out = buildPasteBlock("지역\t주문건수\n수도권\t10452", "A1:B2");
  assert.equal(out.split("\n").length, 2);
  assert.match(out, /엑셀에서 붙여넣은 2행 × 2열 — A1:B2 범위로 인식했습니다/);
});

// ---- 2026-08-25 실측: Excel은 빈 범위를 복사하면 클립보드에 탭 격자가 아니라 ----
// `\r\n`만 넣는다 (A1:D6 빈 범위 → "\r\n", A1에만 값 → "x\r\n"). 탭만 보는 관문은
// 여기서 떨어져 "저기 위치의 셀 정보가 입력이 안되는데?"가 됐다.
test("빈 범위 복사(클립보드가 줄바꿈뿐)도 Excel 선택으로 보고 주소를 물어본다", () => {
  assert.equal(isExcelSelectionPaste("\r\n"), true);
  assert.equal(isExcelSelectionPaste("\r\n\r\n"), true);
  assert.equal(isExcelSelectionPaste("   "), true);
  // 탭 격자는 종전대로.
  assert.equal(isExcelSelectionPaste(EXCEL_TABLE), true);
});

test("글자가 든 평범한 붙여넣기는 여전히 건드리지 않는다", () => {
  assert.equal(isExcelSelectionPaste("매출 시트 정렬해줘"), false);
  assert.equal(isExcelSelectionPaste("x\r\n"), false); // 한 칸 값 — 문장으로 붙는 게 맞다
  assert.equal(isExcelSelectionPaste(""), false); // 빈 붙여넣기는 아무것도 아니다
  assert.equal(isExcelSelectionPaste(null), false);
});

test("빈 범위 주소로도 안내 블록이 범위 기준으로 만들어진다", () => {
  const out = buildPasteBlock("\r\n", "A1:D6");
  assert.match(out, /\[\[EXCEL_RANGE:A1:D6\]\]/);
  assert.match(out, /엑셀에서 붙여넣은 6행 × 4열 — A1:D6 범위로 인식했습니다/);
});
