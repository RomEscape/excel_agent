import test from "node:test";
import assert from "node:assert/strict";

import {
  DEFAULT_THEME_PREFERENCE,
  THEME_PREFERENCES,
  normalizePreference,
  resolveTheme,
  nextPreference,
} from "./theme.js";

test("normalizePreference: 알 수 없는 값은 기본값으로 접힌다", () => {
  // localStorage에 구버전 값이나 손으로 넣은 값이 남아 있어도 앱이 깨지면 안 된다.
  assert.equal(normalizePreference("dark"), "dark");
  assert.equal(normalizePreference("light"), "light");
  assert.equal(normalizePreference("system"), "system");
  assert.equal(normalizePreference("sepia"), DEFAULT_THEME_PREFERENCE);
  assert.equal(normalizePreference(undefined), DEFAULT_THEME_PREFERENCE);
  assert.equal(normalizePreference(null), DEFAULT_THEME_PREFERENCE);
});

test("resolveTheme: 명시 선택은 OS 선호를 이긴다", () => {
  assert.equal(resolveTheme("light", true), "light");
  assert.equal(resolveTheme("dark", false), "dark");
});

test("resolveTheme: system이면 OS 선호를 따른다", () => {
  assert.equal(resolveTheme("system", true), "dark");
  assert.equal(resolveTheme("system", false), "light");
});

test("nextPreference: system에서 눌러도 화면이 실제로 바뀐다", () => {
  // system + OS 다크에서 "light"가 아니라 그냥 반대로 가야 한다.
  // 여기서 light를 돌려주면 resolved가 그대로라 아무 일도 안 일어난 것처럼 보인다.
  assert.equal(nextPreference("system", true), "light");
  assert.equal(nextPreference("system", false), "dark");
  assert.equal(nextPreference("dark", false), "light");
  assert.equal(nextPreference("light", false), "dark");
});

test("nextPreference는 항상 명시값을 돌려준다 — system으로 되돌아가지 않는다", () => {
  for (const pref of THEME_PREFERENCES) {
    for (const systemDark of [true, false]) {
      const next = nextPreference(pref, systemDark);
      assert.ok(next === "light" || next === "dark", `${pref}/${systemDark} → ${next}`);
    }
  }
});
