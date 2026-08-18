import test from "node:test";
import assert from "node:assert/strict";

import {
  CHAT_WIDTH_DEFAULT,
  CHAT_WIDTH_MAX,
  CHAT_WIDTH_MIN,
  chatWidthUpperBound,
  clampChatWidth,
  readStoredChatWidth,
  resolveWorkspaceLayout,
} from "./workspaceLayout.js";

test("넓은 창에서는 저장한 폭을 그대로 쓴다", () => {
  // 1600px 컨테이너 — 파일 목록에 420px을 남기고도 720px까지 허용된다.
  assert.equal(chatWidthUpperBound(1600), CHAT_WIDTH_MAX);
  assert.equal(clampChatWidth(500, 1600), 500);
  assert.equal(clampChatWidth(720, 1600), 720);
});

test("창이 좁아지면 파일 목록에 420px을 남기도록 채팅 폭을 깎는다", () => {
  // 사용자 스크린샷 조건: ~900px 컨테이너에 720px 채팅 → 파일 목록 0에 가까움.
  assert.equal(chatWidthUpperBound(900), 480);
  assert.equal(clampChatWidth(720, 900), 480);
  // 요청이 상한보다 작으면 그대로.
  assert.equal(clampChatWidth(300, 900), 300);
});

test("아무리 좁아도 채팅 최소 폭 아래로는 내려가지 않는다", () => {
  // 600px 컨테이너면 600 - 420 = 180 < 280 → 280으로 고정(세로 쌓기가 맡는 영역).
  assert.equal(chatWidthUpperBound(600), CHAT_WIDTH_MIN);
  assert.equal(clampChatWidth(720, 600), CHAT_WIDTH_MIN);
  assert.equal(clampChatWidth(100, 1600), CHAT_WIDTH_MIN);
});

test("컨테이너 폭을 아직 모르면 절대 상한을 쓰고, 깨진 요청은 기본값으로 간다", () => {
  assert.equal(chatWidthUpperBound(0), CHAT_WIDTH_MAX);
  assert.equal(chatWidthUpperBound(undefined), CHAT_WIDTH_MAX);
  assert.equal(chatWidthUpperBound(NaN), CHAT_WIDTH_MAX);
  assert.equal(clampChatWidth("abc", 1600), CHAT_WIDTH_DEFAULT);
  assert.equal(clampChatWidth(NaN, 0), CHAT_WIDTH_DEFAULT);
});

test("760px 미만이면 세로 쌓기, 그 이상이면 좌우 배치", () => {
  assert.equal(resolveWorkspaceLayout(759), "stacked");
  assert.equal(resolveWorkspaceLayout(760), "side");
  assert.equal(resolveWorkspaceLayout(1400), "side");
  // 아직 못 쟀으면 넓은 화면 배치로 둔다 — 첫 페인트 전 useLayoutEffect가 곧 채운다.
  assert.equal(resolveWorkspaceLayout(0), "side");
  assert.equal(resolveWorkspaceLayout(null), "side");
});

test("localStorage 값은 범위 안일 때만 믿는다", () => {
  assert.equal(readStoredChatWidth("420"), 420);
  assert.equal(readStoredChatWidth("280"), 280);
  assert.equal(readStoredChatWidth("720"), 720);
  assert.equal(readStoredChatWidth("100"), CHAT_WIDTH_DEFAULT);
  assert.equal(readStoredChatWidth("9999"), CHAT_WIDTH_DEFAULT);
  assert.equal(readStoredChatWidth(null), CHAT_WIDTH_DEFAULT);
  assert.equal(readStoredChatWidth("가나다"), CHAT_WIDTH_DEFAULT);
});
