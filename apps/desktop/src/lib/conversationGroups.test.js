import test from "node:test";
import assert from "node:assert/strict";

import {
  CONVERSATION_VIEWS,
  dayKey,
  dayLabel,
  fileOf,
  groupSessions,
  groupSessionsByDay,
  groupSessionsByFile,
  hasFileInfo,
  normalizeView,
} from "./conversationGroups.js";

const s = (id, at, extra = {}) => ({ session_id: id, last_message_at: at, ...extra });

test("normalizeView: 모르는 값은 요일별로 떨어진다", () => {
  assert.deepEqual([...CONVERSATION_VIEWS], ["day", "file"]);
  assert.equal(normalizeView("file"), "file");
  assert.equal(normalizeView("nonsense"), "day");
  assert.equal(normalizeView(null), "day");
});

test("dayLabel: 와이어프레임 형식 `8월 18일(화)`", () => {
  assert.equal(dayLabel("2026-08-18T10:00:00"), "8월 18일(화)");
  assert.equal(dayLabel("2026-08-23T10:00:00"), "8월 23일(일)");
  assert.equal(dayLabel("nonsense"), "날짜 미상");
});

test("dayKey: 로컬 날짜 기준 키", () => {
  assert.equal(dayKey("2026-08-18T23:30:00"), "2026-08-18");
  assert.equal(dayKey("nonsense"), "");
});

test("groupSessionsByDay: 최신 날짜가 먼저, 그룹 안도 최신이 먼저", () => {
  const groups = groupSessionsByDay([
    s("a", "2026-08-17T09:00:00"),
    s("b", "2026-08-18T09:00:00"),
    s("c", "2026-08-18T20:00:00"),
  ]);
  assert.deepEqual(groups.map((g) => g.label), ["8월 18일(화)", "8월 17일(월)"]);
  assert.deepEqual(groups[0].items.map((i) => i.session_id), ["c", "b"]);
});

test("groupSessionsByDay: 타임스탬프가 깨져도 버리지 않고 맨 뒤로 모은다", () => {
  const groups = groupSessionsByDay([s("bad", null), s("ok", "2026-08-18T09:00:00")]);
  assert.equal(groups.length, 2);
  assert.equal(groups[groups.length - 1].label, "날짜 미상");
  assert.equal(groups[groups.length - 1].items[0].session_id, "bad");
});

test("groupSessionsByDay: session_id 없는 항목은 걸러낸다", () => {
  assert.deepEqual(groupSessionsByDay([null, {}, s("a", "2026-08-18T09:00:00")]).length, 1);
  assert.deepEqual(groupSessionsByDay(null), []);
});

test("fileOf / hasFileInfo: 현재 백엔드 세션에는 파일 정보가 없다", () => {
  assert.equal(fileOf(s("a", "2026-08-18T09:00:00")), "");
  assert.equal(hasFileInfo([s("a", "2026-08-18T09:00:00")]), false);
  // 필드가 생기면 그대로 잡힌다.
  assert.equal(fileOf(s("a", "x", { file_name: "8월_매출표.xlsx" })), "8월_매출표.xlsx");
  assert.equal(hasFileInfo([s("a", "x", { file: "b.xlsx" })]), true);
});

test("groupSessionsByFile: 파일 모르는 세션은 맨 뒤 `파일 없음`", () => {
  const groups = groupSessionsByFile([
    s("a", "2026-08-18T09:00:00"),
    s("b", "2026-08-18T09:00:00", { file_name: "8월_매출표.xlsx" }),
  ]);
  assert.deepEqual(groups.map((g) => g.label), ["8월_매출표.xlsx", "파일 없음"]);
});

test("groupSessions: 보기 값으로 분기한다", () => {
  const list = [s("a", "2026-08-18T09:00:00", { file_name: "x.xlsx" })];
  assert.equal(groupSessions(list, "day")[0].label, "8월 18일(화)");
  assert.equal(groupSessions(list, "file")[0].label, "x.xlsx");
  assert.equal(groupSessions(list, "무엇")[0].label, "8월 18일(화)");
});
