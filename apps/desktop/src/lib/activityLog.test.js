import test from "node:test";
import assert from "node:assert/strict";

import {
  ACTIVITY_STATUS,
  activityTime,
  buildPageList,
  buildSummary,
  deviceLabel,
  filterRows,
  pageCount,
  statusOf,
  toActivityRows,
} from "./activityLog.js";

test("deviceLabel: 메신저 채널은 모바일로 접히고 미지의 값은 그대로 남는다", () => {
  assert.equal(deviceLabel("desktop"), "데스크탑");
  assert.equal(deviceLabel("telegram"), "모바일");
  assert.equal(deviceLabel("mobile"), "모바일");
  assert.equal(deviceLabel(""), "데스크탑");
  // 새 채널이 붙어도 표에서 사라지지 않아야 한다.
  assert.equal(deviceLabel("kakao"), "kakao");
});

test("statusOf: approved가 classification보다 우선한다", () => {
  assert.equal(statusOf({ approved: true, classification: "denied" }), "done");
  assert.equal(statusOf({ approved: false, classification: "safe" }), "blocked");
  assert.equal(statusOf({ classification: "safe" }), "done");
  assert.equal(statusOf({ classification: "denied" }), "blocked");
  assert.equal(statusOf({ classification: "confirm" }), "pending");
  assert.equal(statusOf(null), "pending");
});

test("activityTime: 오늘은 시각, 그 이전은 상대 표기", () => {
  const now = new Date("2026-08-23T21:00:00");
  assert.match(activityTime(new Date("2026-08-23T08:00:00").toISOString(), now), /\d/);
  assert.equal(activityTime(new Date("2026-08-17T08:00:00").toISOString(), now), "6일 전");
  assert.equal(activityTime(new Date("2026-08-09T08:00:00").toISOString(), now), "2주일 전");
  assert.equal(activityTime(new Date("2026-06-23T08:00:00").toISOString(), now), "2달 전");
  // 값이 없거나 깨진 타임스탬프는 빈 문자열 — 표에 "Invalid Date"가 새지 않는다.
  assert.equal(activityTime(null, now), "");
  assert.equal(activityTime("nonsense", now), "");
});

test("toActivityRows: 로그를 표 행으로 옮기고 null은 걸러낸다", () => {
  const now = new Date("2026-08-23T21:00:00");
  const rows = toActivityRows(
    [
      {
        id: 1,
        source: "desktop",
        command: "아시아 주문 현황만 분리해 새 시트 생성",
        file_name: "8월_매출표.xlsx",
        classification: "safe",
        timestamp: "2026-08-23T20:00:00",
      },
      null,
    ],
    now
  );
  assert.equal(rows.length, 1);
  assert.equal(rows[0].device, "데스크탑");
  assert.equal(rows[0].file, "8월_매출표.xlsx");
  assert.equal(rows[0].status, "done");
  assert.equal(rows[0].statusLabel, ACTIVITY_STATUS.done.label);
});

test("filterRows: 명령문과 파일명 양쪽을 검색한다", () => {
  const rows = toActivityRows([
    { id: 1, command: "평균 단가 조회해줘", file_name: "8월_매출표.xlsx" },
    { id: 2, command: "새 시트 생성", file_name: "7월_보고서.xlsx" },
  ]);
  assert.equal(filterRows(rows, "단가").length, 1);
  assert.equal(filterRows(rows, "7월").length, 1);
  assert.equal(filterRows(rows, "").length, 2);
  assert.equal(filterRows(rows, "없는말").length, 0);
});

test("buildSummary: 승인 대기 자리에 자동 마스킹이 들어간다", () => {
  const cards = buildSummary(
    { total: 1270, safe: 1000, confirm_approved: 165, denied: 100, confirm_rejected: 5 },
    { masking: { total: 276 } }
  );
  assert.deepEqual(
    cards.map((c) => c.id),
    ["total", "done", "blocked", "masked"]
  );
  assert.equal(cards[0].value, 1270);
  assert.equal(cards[1].value, 1165);
  assert.equal(cards[2].value, 105);
  assert.equal(cards[3].value, 276);
});

test("buildSummary: 통계가 없어도 0으로 떨어진다", () => {
  const cards = buildSummary(null, null);
  assert.deepEqual(cards.map((c) => c.value), [0, 0, 0, 0]);
});

test("pageCount: 0건이어도 1페이지", () => {
  assert.equal(pageCount(0), 1);
  assert.equal(pageCount(20), 1);
  assert.equal(pageCount(21), 2);
  assert.equal(pageCount(null), 1);
});

test("buildPageList: 총 페이지가 적으면 전부 나열한다", () => {
  assert.deepEqual(buildPageList(1, 5), [1, 2, 3, 4, 5]);
});

test("buildPageList: 앞쪽에서는 뒤에만 gap이 붙는다", () => {
  assert.deepEqual(buildPageList(1, 30), [1, 2, 3, 4, 5, "gap", 30]);
});

test("buildPageList: 끝쪽에서는 앞에 gap이 붙어 현재 위치가 보인다", () => {
  const list = buildPageList(30, 30);
  assert.equal(list[0], 1);
  assert.equal(list[1], "gap");
  assert.ok(list.includes(30));
  assert.ok(list.includes(29));
});

test("buildPageList: 범위를 벗어난 현재 페이지도 안전하게 접힌다", () => {
  assert.ok(buildPageList(999, 30).includes(30));
  assert.ok(buildPageList(-5, 30).includes(1));
});
