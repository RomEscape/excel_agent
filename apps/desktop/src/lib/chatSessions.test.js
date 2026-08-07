import test from "node:test";
import assert from "node:assert/strict";

import {
  groupLabelFor,
  groupSessions,
  sessionTitle,
  GROUP_TODAY,
  GROUP_YESTERDAY,
  GROUP_WEEK,
  GROUP_OLDER,
} from "./chatSessions.js";

// 로컬 타임존 기준 고정 시각 — 날짜 경계 판정이 UTC로 새지 않는지 같이 본다.
const NOW = new Date(2026, 7, 6, 14, 30); // 2026-08-06 14:30 (로컬)

function at(year, month, day, hour = 12) {
  return new Date(year, month, day, hour).toISOString();
}

test("groupLabelFor — 오늘/어제/지난 7일/이전 경계", () => {
  assert.equal(groupLabelFor(at(2026, 7, 6, 9), NOW), GROUP_TODAY);
  // 같은 날 자정 직후도 오늘
  assert.equal(groupLabelFor(at(2026, 7, 6, 0), NOW), GROUP_TODAY);
  assert.equal(groupLabelFor(at(2026, 7, 5, 23), NOW), GROUP_YESTERDAY);
  assert.equal(groupLabelFor(at(2026, 7, 2), NOW), GROUP_WEEK);
  // 6일 전까지가 "지난 7일", 7일 전부터 "이전"
  assert.equal(groupLabelFor(at(2026, 6, 31), NOW), GROUP_WEEK);
  assert.equal(groupLabelFor(at(2026, 6, 30), NOW), GROUP_OLDER);
});

test("groupLabelFor — 시각이 없거나 깨졌으면 이전으로", () => {
  assert.equal(groupLabelFor(null, NOW), GROUP_OLDER);
  assert.equal(groupLabelFor(undefined, NOW), GROUP_OLDER);
  assert.equal(groupLabelFor("not-a-date", NOW), GROUP_OLDER);
});

test("groupLabelFor — 미래 시각은 오늘로 취급한다", () => {
  // 기기 시계가 어긋나 미래로 찍힌 세션이 목록 맨 아래로 밀리면
  // 사용자는 방금 만든 대화를 찾지 못한다.
  assert.equal(groupLabelFor(at(2026, 7, 20), NOW), GROUP_TODAY);
});

test("groupSessions — 그룹 순서가 고정되고 빈 그룹은 빠진다", () => {
  const groups = groupSessions(
    [
      { session_id: "c", last_message_at: at(2026, 6, 1) },
      { session_id: "a", last_message_at: at(2026, 7, 6, 10) },
      { session_id: "b", last_message_at: at(2026, 7, 5) },
    ],
    NOW
  );

  assert.deepEqual(
    groups.map((g) => g.label),
    [GROUP_TODAY, GROUP_YESTERDAY, GROUP_OLDER]
  );
  // "지난 7일"에 해당하는 세션이 없으므로 그룹 자체가 없어야 한다.
  assert.equal(
    groups.some((g) => g.label === GROUP_WEEK),
    false
  );
});

test("groupSessions — 그룹 안은 최신 순", () => {
  const [today] = groupSessions(
    [
      { session_id: "old", last_message_at: at(2026, 7, 6, 8) },
      { session_id: "new", last_message_at: at(2026, 7, 6, 13) },
      { session_id: "mid", last_message_at: at(2026, 7, 6, 11) },
    ],
    NOW
  );
  assert.deepEqual(
    today.items.map((s) => s.session_id),
    ["new", "mid", "old"]
  );
});

test("groupSessions — session_id 없는 항목과 비배열 입력을 흘려보낸다", () => {
  assert.deepEqual(groupSessions(null, NOW), []);
  assert.deepEqual(groupSessions(undefined, NOW), []);
  const groups = groupSessions(
    [null, { preview: "id 없음" }, { session_id: "ok", last_message_at: at(2026, 7, 6) }],
    NOW
  );
  assert.equal(groups.length, 1);
  assert.equal(groups[0].items.length, 1);
});

test("sessionTitle — 공백 정리 + 길이 제한, 빈 preview는 안내 문구", () => {
  assert.equal(sessionTitle({ preview: "8월 매출 데이터 분석" }), "8월 매출 데이터 분석");
  assert.equal(sessionTitle({ preview: "  줄바꿈\n포함  " }), "줄바꿈 포함");
  assert.equal(sessionTitle({ preview: "" }), "(빈 대화)");
  assert.equal(sessionTitle({}), "(빈 대화)");
  assert.equal(sessionTitle(null), "(빈 대화)");

  const long = "가".repeat(60);
  const title = sessionTitle({ preview: long }, 10);
  assert.equal(title.length, 10);
  assert.ok(title.endsWith("…"));
});
