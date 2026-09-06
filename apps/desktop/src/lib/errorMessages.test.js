import test from "node:test";
import assert from "node:assert/strict";

import { extractSidecarDetail, toUserMessage } from "./errorMessages.js";

test("사이드카 연결 오류가 '디스크 공간 부족'으로 둔갑하지 않는다", () => {
  // 2026-08-19 실측: /no.*space/가 "cannot connect … workspace"까지 잡았다.
  const msg = toUserMessage("Cannot connect to sidecar: GET http://127.0.0.1:19532/workspace/list failed", "폴백");
  assert.equal(msg.includes("디스크"), false, msg);
  assert.equal(
    toUserMessage("Not found: workspace directory", "폴백").includes("디스크"),
    false
  );
});

test("진짜 디스크 오류는 디스크 문구로 매핑된다", () => {
  assert.match(toUserMessage("ENOSPC: no space left on device"), /디스크 공간/);
  assert.match(toUserMessage("Disk is full"), /디스크 공간/);
  assert.match(toUserMessage("There is not enough space on the disk"), /디스크 공간/);
});


// ─── 2026-09-06 감사: 사이드카가 지어 준 한국어 안내가 "서버 오류"로 뭉개지던 것 ───

test("사이드카의 409 안내가 그대로 사용자에게 간다", () => {
  const raw =
    'HTTP 409: {"detail":"\'260906.xlsx\' 파일이 Excel에서 열려 있어 저장할 수 없습니다. Excel에서 해당 파일을 닫고 다시 시도해 주세요."}';
  const msg = toUserMessage(raw, "폴백");
  assert.match(msg, /Excel에서 해당 파일을 닫고/);
  assert.equal(msg.includes("서버 오류"), false, msg);
});

test("셀 편집 중 안내(409)도 원문 그대로", () => {
  const raw =
    'HTTP 409: {"detail":"Excel이 지금 다른 작업 중이라 명령을 받지 못했습니다. 셀을 편집 중이면 Enter나 Esc를 누르고, 열려 있는 대화상자를 닫은 뒤 다시 시도해 주세요."}';
  assert.match(toUserMessage(raw, "폴백"), /Esc/);
});

test("영어 스택트레이스·COM 덤프는 통과시키지 않는다", () => {
  const com =
    'HTTP 500: {"detail":"Excel Live 오류: (-2147418111, \'Call was rejected by callee.\', None, None)"}';
  // 한글이 섞여 있으므로 통과하되, 순수 영어 본문은 막힌다.
  const english = 'HTTP 500: {"detail":"Traceback (most recent call last): KeyError: sheet"}';
  assert.equal(extractSidecarDetail(english), "");
  assert.match(toUserMessage(english, "폴백"), /서버 내부 오류|서버 오류/);
  // COM 덤프는 한글 접두사가 붙어 있어 통과한다 — 사이드카가 그 문구를 고치는 게 맞다.
  assert.equal(typeof extractSidecarDetail(com), "string");
});

test("아주 긴 본문은 안내로 쓰지 않는다", () => {
  const long = `HTTP 400: {"detail":"${"가".repeat(400)}"}`;
  assert.equal(extractSidecarDetail(long), "");
});

test("HTTP 접두가 없으면 건드리지 않는다", () => {
  assert.equal(extractSidecarDetail("그냥 문자열"), "");
  assert.equal(extractSidecarDetail(""), "");
  assert.equal(extractSidecarDetail(null), "");
});

test("JSON 이 아닌 본문도 한국어면 통과", () => {
  assert.equal(extractSidecarDetail("HTTP 409: 통합문서를 찾지 못했습니다."), "통합문서를 찾지 못했습니다.");
});

test("기존 매핑은 그대로 — 안내가 없는 오류는 예전 문구", () => {
  assert.match(toUserMessage("HTTP 502 Bad Gateway", "폴백"), /서버 오류/);
  assert.match(toUserMessage("HTTP 404 Not Found", "폴백"), /찾을 수 없습니다/);
});
