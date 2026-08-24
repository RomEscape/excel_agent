import test from "node:test";
import assert from "node:assert/strict";

import {
  DEFAULT_FONT_SCALE,
  FONT_SCALES,
  FONT_SCALE_LABELS,
  normalizeFontScale,
  rootFontSize,
} from "./fontScale.js";

test("FONT_SCALES: 와이어프레임의 2단계뿐", () => {
  assert.deepEqual([...FONT_SCALES], ["default", "large"]);
  assert.equal(DEFAULT_FONT_SCALE, "default");
});

test("모든 값에 라벨과 px가 있다", () => {
  for (const s of FONT_SCALES) {
    assert.equal(typeof FONT_SCALE_LABELS[s], "string");
    assert.ok(FONT_SCALE_LABELS[s].length > 0);
    assert.equal(typeof rootFontSize(s), "number");
  }
});

test("normalizeFontScale: 모르는 값·빈 값은 기본으로 떨어진다", () => {
  assert.equal(normalizeFontScale("large"), "large");
  assert.equal(normalizeFontScale("default"), "default");
  // 저장된 설정이 깨졌거나 구버전 값이어도 앱이 뜨지 않으면 안 된다.
  assert.equal(normalizeFontScale("huge"), "default");
  assert.equal(normalizeFontScale(null), "default");
  assert.equal(normalizeFontScale(undefined), "default");
  assert.equal(normalizeFontScale(42), "default");
});

test("rootFontSize: 큰 사이즈가 기본보다 크고, 깨진 값도 숫자를 돌려준다", () => {
  assert.ok(rootFontSize("large") > rootFontSize("default"));
  assert.equal(rootFontSize("nonsense"), rootFontSize("default"));
});
