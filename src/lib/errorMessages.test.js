import test from "node:test";
import assert from "node:assert/strict";

import { toUserMessage } from "./errorMessages.js";

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
