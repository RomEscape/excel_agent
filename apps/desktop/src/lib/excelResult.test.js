import test from "node:test";
import assert from "node:assert/strict";

import {
  columnLetter,
  startColumnIndex,
  startRowNumber,
  toNumber,
  toResultView,
  formatResultText,
  SHEET_PREVIEW_MAX_ROWS,
  SHEET_PREVIEW_MAX_COLS,
  BARS_MAX_ITEMS,
} from "./excelResult.js";

test("columnLetter — 0-based 인덱스를 엑셀 열 문자로", () => {
  assert.equal(columnLetter(0), "A");
  assert.equal(columnLetter(25), "Z");
  assert.equal(columnLetter(26), "AA");
  assert.equal(columnLetter(27), "AB");
  assert.equal(columnLetter(-1), "");
});

test("startColumnIndex / startRowNumber — 범위 주소에서 시작 위치를 뽑는다", () => {
  assert.equal(startColumnIndex("A1:C3"), 0);
  assert.equal(startColumnIndex("C2:E10"), 2);
  assert.equal(startColumnIndex("AA5"), 26);
  assert.equal(startRowNumber("C2:E10"), 2);
  // 파싱 실패는 A1 기준으로 눕힌다 — 표가 안 그려지는 것보다 낫다.
  assert.equal(startColumnIndex(""), 0);
  assert.equal(startRowNumber("이상한값"), 1);
});

test("toNumber — 천단위 구분자를 포함한 숫자 문자열을 해석", () => {
  assert.equal(toNumber(1234), 1234);
  assert.equal(toNumber("1,234"), 1234);
  assert.equal(toNumber("  42 "), 42);
  assert.equal(toNumber("N/A"), null);
  assert.equal(toNumber(""), null);
  assert.equal(toNumber(Infinity), null);
});

test("read_range — 표 카드 모델로 승격되고 열 문자가 실제 주소를 따른다", () => {
  const view = toResultView("excel_live.read_range", {
    address: "C2:E4",
    row_count: 3,
    col_count: 3,
    values: [
      ["제품군", "매출", "전월대비"],
      ["클라우드", 3200, "+12%"],
      ["보안모듈", 1850, "+4%"],
    ],
  });

  assert.equal(view.kind, "sheet");
  // C열부터 읽었으므로 헤더는 A가 아니라 C에서 시작해야 한다.
  assert.deepEqual(view.columns, ["C", "D", "E"]);
  // 행 번호도 2부터.
  assert.deepEqual(
    view.rows.map((r) => r.number),
    [2, 3, 4]
  );
  assert.deepEqual(view.rows[1].cells, ["클라우드", "3200", "+12%"]);
  assert.equal(view.truncated, false);
  assert.match(view.summary, /C2:E4 범위를 읽었습니다 \(3행 × 3열\)/);
});

test("read_range — 미리보기 상한을 넘으면 잘라내고 truncated를 세운다", () => {
  const values = Array.from({ length: 30 }, (_, r) =>
    Array.from({ length: 12 }, (_, c) => `r${r}c${c}`)
  );
  const view = toResultView("excel_live.read_range", {
    address: "A1:L30",
    row_count: 30,
    col_count: 12,
    values,
  });

  assert.equal(view.rows.length, SHEET_PREVIEW_MAX_ROWS);
  assert.equal(view.columns.length, SHEET_PREVIEW_MAX_COLS);
  assert.equal(view.rows[0].cells.length, SHEET_PREVIEW_MAX_COLS);
  assert.equal(view.truncated, true);
  // 잘렸어도 summary는 실제 전체 크기를 말해야 한다.
  assert.match(view.summary, /30행 × 12열/);
});

test("read_range — values가 비어도 표 모델은 깨지지 않는다", () => {
  const view = toResultView("excel_live.read_range", {
    address: "A1",
    row_count: 0,
    col_count: 0,
  });
  assert.equal(view.kind, "sheet");
  assert.deepEqual(view.rows, []);
  assert.deepEqual(view.columns, []);
});

test("group_by_aggregate — 막대 모델은 값 내림차순 정렬 + max 계산", () => {
  const view = toResultView("excel_live.group_by_aggregate", {
    group_column: "제품군",
    agg: "합계",
    groups: [
      { key: "백업", value: 1320 },
      { key: "클라우드", value: "3,200" },
      { key: "보안", value: 1850 },
    ],
  });

  assert.equal(view.kind, "bars");
  assert.deepEqual(
    view.items.map((i) => i.label),
    ["클라우드", "보안", "백업"]
  );
  assert.equal(view.items[0].value, 3200);
  assert.equal(view.max, 3200);
  assert.equal(view.title, "제품군별 합계");
});

test("group_by_aggregate — 숫자가 아닌 그룹은 막대에서 빠지고 값이 전부 0이어도 나누기 오류가 없다", () => {
  const view = toResultView("excel_live.group_by_aggregate", {
    group_column: "지역",
    agg: "합계",
    groups: [
      { key: "서울", value: "N/A" },
      { key: "부산", value: 0 },
    ],
  });

  assert.deepEqual(
    view.items.map((i) => i.label),
    ["부산"]
  );
  // max가 0이면 렌더에서 0으로 나누게 되므로 1로 눕혀야 한다.
  assert.equal(view.max, 1);
});

test("group_by_aggregate — 항목이 상한을 넘으면 자르고 truncated를 세운다", () => {
  const groups = Array.from({ length: BARS_MAX_ITEMS + 5 }, (_, i) => ({
    key: `g${i}`,
    value: i + 1,
  }));
  const view = toResultView("excel_live.group_by_aggregate", {
    group_column: "키",
    agg: "합계",
    groups,
  });
  assert.equal(view.items.length, BARS_MAX_ITEMS);
  assert.equal(view.truncated, true);
});

test("calculate_column_stat — 단일 통계 카드", () => {
  const view = toResultView("excel_live.calculate_column_stat", {
    header: "매출",
    stat: "합계",
    value: 6370,
  });
  assert.equal(view.kind, "stat");
  assert.equal(view.label, "매출 합계");
  assert.equal(view.value, "6370");
});

test("카드가 없는 action은 text 모델로 떨어진다", () => {
  const view = toResultView("excel_live.write_range", {
    address: "B2:D2",
    written_cells: 3,
  });
  assert.equal(view.kind, "text");
  assert.equal(view.summary, "B2:D2 범위에 3개 셀을 기록했습니다.");
});

test("알 수 없는 action과 잘못된 result는 기본 문장으로", () => {
  assert.equal(toResultView("excel_live.무언가", {}).summary, "엑셀 작업이 완료되었습니다.");
  assert.equal(toResultView("excel_live.read_range", null).kind, "text");
});

test("formatResultText — 기존 문자열 동작을 그대로 유지한다", () => {
  // 세션 영속화·메신저 전송이 이 문자열에 의존하므로 회귀하면 안 된다.
  assert.equal(
    formatResultText("excel_live.save_workbook", { name: "매출.xlsx" }),
    "엑셀 파일을 저장했습니다 (매출.xlsx)."
  );
  assert.equal(
    formatResultText("excel_live.list_workbooks", { workbooks: [] }),
    "열려 있는 엑셀 통합문서가 없습니다."
  );
  assert.equal(
    formatResultText("excel_live.dedupe_rows", { removed_duplicates: 2, kept_rows: 8 }),
    "중복 2개 행을 제거했습니다 (8개 유지)."
  );
  // 카드 모델로 승격된 action도 문자열은 종전과 같아야 한다.
  assert.equal(
    formatResultText("excel_live.read_range", {
      address: "A1:C3",
      row_count: 3,
      col_count: 3,
    }),
    "A1:C3 범위를 읽었습니다 (3행 × 3열)."
  );
});
