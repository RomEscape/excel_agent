import test from "node:test";
import assert from "node:assert/strict";

import { buildPasteBlock, displayMessageText, looksLikeExcelPaste, pasteShape } from "./excelPaste.js";

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
  assert.match(shown, /엑셀에서 붙여넣은 3행 × 4열/);
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
