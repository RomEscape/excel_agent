import test from "node:test";
import assert from "node:assert/strict";

import {
  DEFAULT_PANEL_MODE,
  PANEL_MODES,
  PANEL_WIDTH,
  normalizePanelMode,
  nextPanelMode,
  panelToggleLabel,
  reservesLayoutSpace,
} from "./chatPanel.js";

test("normalizePanelMode: 알 수 없는 값은 기본 모드로 접힌다", () => {
  assert.equal(normalizePanelMode("docked"), "docked");
  assert.equal(normalizePanelMode("floating"), "floating");
  assert.equal(normalizePanelMode("fullscreen"), DEFAULT_PANEL_MODE);
  assert.equal(normalizePanelMode(undefined), DEFAULT_PANEL_MODE);
});

test("nextPanelMode: 두 모드를 오간다", () => {
  assert.equal(nextPanelMode("docked"), "floating");
  assert.equal(nextPanelMode("floating"), "docked");
  // 깨진 값에서 눌러도 멈추지 않고 유효한 모드로 빠져나온다.
  assert.equal(nextPanelMode("garbage"), "floating");
});

test("nextPanelMode를 두 번 누르면 제자리로 돌아온다", () => {
  for (const mode of PANEL_MODES) {
    assert.equal(nextPanelMode(nextPanelMode(mode)), mode);
  }
});

test("reservesLayoutSpace: 도킹만 본문 폭을 가져간다", () => {
  // 플로팅이 true가 되면 본문이 줄어든 채로 패널이 그 위에 겹쳐서
  // 오른쪽에 빈 띠가 생긴다 (Frame 169는 본문이 1356px 그대로다).
  assert.equal(reservesLayoutSpace("docked"), true);
  assert.equal(reservesLayoutSpace("floating"), false);
});

test("panelToggleLabel은 현재가 아니라 '누르면 되는 상태'를 말한다", () => {
  assert.match(panelToggleLabel("docked"), /플로팅/);
  assert.match(panelToggleLabel("floating"), /도킹/);
});

test("두 모드는 같은 폭을 쓴다", () => {
  // 폭이 모드마다 다르면 토글이 본문 표의 열 위치까지 흔든다.
  assert.equal(PANEL_WIDTH, 390);
});
