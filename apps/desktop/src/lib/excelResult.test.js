import test from "node:test";
import assert from "node:assert/strict";

import { formatResultText } from "./excelResult.js";

/*
 * 이 문자열은 말풍선 본문 · 세션 영속화 · 메신저 전송 세 곳이 그대로 쓴다.
 * 문구가 바뀌면 저장된 대화 기록의 톤도 같이 바뀌므로 고정한다.
 *
 * 예전에 있던 표/막대/통계 뷰모델 테스트는 인라인 결과 카드를 걷어내면서
 * 같이 지웠다 (최종 와이어프레임 14화면에 결과 카드가 없다).
 */

test("read_range — 실제 크기를 문장에 담는다", () => {
  assert.equal(
    formatResultText("excel_live.read_range", {
      address: "A1:C3",
      row_count: 3,
      col_count: 3,
    }),
    "A1:C3 범위를 읽었습니다 (3행 × 3열)."
  );
});

test("read_range — row_count가 없으면 values 배열에서 크기를 센다", () => {
  // sidecar가 카운트를 안 채워 보내도 "0행 × 0열"이 나오면 안 된다.
  assert.equal(
    formatResultText("excel_live.read_range", {
      address: "C2:E4",
      values: [
        ["제품군", "매출", "전월대비"],
        ["클라우드", 3200, "+12%"],
      ],
    }),
    "C2:E4 범위를 읽었습니다 (2행 × 3열)."
  );
});

test("group_by_aggregate — 앞 5개까지 미리보기하고 나머지는 줄임표", () => {
  const many = Array.from({ length: 7 }, (_, i) => ({ key: `g${i}`, value: i }));
  const text = formatResultText("excel_live.group_by_aggregate", {
    group_column: "지역",
    agg: "합계",
    groups: many,
  });
  assert.match(text, /^지역별 합계 — /);
  assert.match(text, /…$/);
  assert.ok(!text.includes("g5"), "6번째 이후 그룹은 문장에 없어야 한다");
});

test("calculate_column_stat — 열·통계·값을 한 줄로", () => {
  assert.equal(
    formatResultText("excel_live.calculate_column_stat", {
      header: "매출",
      stat: "합계",
      value: 6370,
    }),
    "매출 열 합계 = 6370"
  );
});

test("변경 계열 action들의 문장을 고정한다", () => {
  assert.equal(
    formatResultText("excel_live.write_range", { address: "B2:D2", written_cells: 3 }),
    "B2:D2 범위에 3개 셀을 기록했습니다."
  );
  assert.equal(
    formatResultText("excel_live.save_workbook", { name: "매출.xlsx" }),
    "엑셀 파일을 저장했습니다 (매출.xlsx)."
  );
  assert.equal(
    formatResultText("excel_live.dedupe_rows", { removed_duplicates: 2, kept_rows: 8 }),
    "중복 2개 행을 제거했습니다 (8개 유지)."
  );
  assert.equal(
    formatResultText("excel_live.sort_rows", { column: "매출", sorted_rows: 12 }),
    "매출 기준으로 12개 행을 정렬했습니다."
  );
  assert.equal(
    formatResultText("excel_live.rename_column", { old_name: "A", new_name: "제품" }),
    "'A' 열을 '제품'로 변경했습니다."
  );
});

test("list_workbooks — 비었을 때와 있을 때의 문장이 다르다", () => {
  assert.equal(
    formatResultText("excel_live.list_workbooks", { workbooks: [] }),
    "열려 있는 엑셀 통합문서가 없습니다."
  );
  assert.equal(
    formatResultText("excel_live.list_workbooks", {
      workbooks: [{ name: "매출.xlsx" }, { workbook_id: "wb2" }],
    }),
    "열린 통합문서 2개: 매출.xlsx, wb2"
  );
});

test("알 수 없는 action과 잘못된 result는 기본 문장으로", () => {
  assert.equal(formatResultText("excel_live.무언가", {}), "엑셀 작업이 완료되었습니다.");
  assert.equal(formatResultText("excel_live.read_range", null), "엑셀 작업이 완료되었습니다.");
  assert.equal(formatResultText(undefined, undefined), "엑셀 작업이 완료되었습니다.");
});
