import { test } from "node:test";
import assert from "node:assert/strict";
import { describeSidecarHealth, STALE_MESSAGE } from "./sidecarHealth.js";

test("최신 사이드카는 연결됨", () => {
  const got = describeSidecarHealth({
    status: "ok",
    sidecar: { code_stale: false, workspace_dir: "C:/repo/엑셀 작업 폴더" },
  });
  assert.equal(got.state, "ok");
  assert.equal(got.message, "연결됨");
  assert.equal(got.workspaceDir, "C:/repo/엑셀 작업 폴더");
});

test("낡은 사이드카는 stale + 재시작 안내", () => {
  const got = describeSidecarHealth({
    status: "ok",
    sidecar: { code_stale: true, workspace_dir: "C:/AppData/office_claw/Workspace" },
  });
  assert.equal(got.state, "stale");
  assert.equal(got.message, STALE_MESSAGE);
  // 어느 폴더를 보고 있었는지 사람이 눈으로 확인할 수 있어야 한다.
  assert.equal(got.workspaceDir, "C:/AppData/office_claw/Workspace");
});

test("sidecar 블록이 없으면 그 자체가 낡음의 증거", () => {
  // 이 필드를 내려주지 않던 시절의 사이드카가 답한 것이다.
  const got = describeSidecarHealth({ status: "ok", ollama_status: "connected" });
  assert.equal(got.state, "stale");
});

test("배포본(frozen)은 소스 트리가 없어 낡음 판정 대상이 아니다", () => {
  const got = describeSidecarHealth({
    status: "ok",
    sidecar: { code_stale: false, frozen: true, workspace_dir: "" },
  });
  assert.equal(got.state, "ok");
});

test("응답이 비어도 터지지 않는다", () => {
  assert.equal(describeSidecarHealth(null).state, "stale");
  assert.equal(describeSidecarHealth(undefined).state, "stale");
});
