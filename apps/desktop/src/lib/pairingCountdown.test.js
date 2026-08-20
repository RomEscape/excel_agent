import test from "node:test";
import assert from "node:assert/strict";

import {
  formatCountdown,
  isExpired,
  remainingSeconds,
  toExpiryTime,
} from "./pairingCountdown.js";

const NOW = 1_760_000_000_000;

test("toExpiryTime: expires_in을 만료 시각으로 옮긴다", () => {
  assert.equal(toExpiryTime(120, NOW), NOW + 120_000);
});

test("toExpiryTime: 값이 없거나 0이면 만료 개념이 없다", () => {
  // 구버전 relay는 expires_in을 안 준다. 이때 0초 만료로 치면
  // QR을 띄우자마자 "만료됨"이 뜬다.
  for (const bad of [0, -5, undefined, null, "이상한값", Number.NaN]) {
    assert.equal(toExpiryTime(bad, NOW), null, `${bad}`);
  }
});

test("remainingSeconds: 올림해서 센다", () => {
  // 남은 0.4초를 0으로 찍으면 아직 되는 코드가 죽은 것처럼 보인다.
  assert.equal(remainingSeconds(NOW + 400, NOW), 1);
  assert.equal(remainingSeconds(NOW + 120_000, NOW), 120);
  assert.equal(remainingSeconds(NOW + 209_000, NOW), 209);
});

test("remainingSeconds: 지나면 음수가 아니라 0", () => {
  assert.equal(remainingSeconds(NOW - 10_000, NOW), 0);
});

test("remainingSeconds: 만료 개념이 없으면 null", () => {
  assert.equal(remainingSeconds(null, NOW), null);
  assert.equal(remainingSeconds(undefined, NOW), null);
});

test("formatCountdown: 와이어프레임의 `3:29` 형식", () => {
  assert.equal(formatCountdown(209), "3:29");
  assert.equal(formatCountdown(120), "2:00");
  assert.equal(formatCountdown(9), "0:09");
  assert.equal(formatCountdown(0), "0:00");
});

test("formatCountdown: 값이 없으면 빈 문자열", () => {
  assert.equal(formatCountdown(null), "");
  assert.equal(formatCountdown(undefined), "");
  assert.equal(formatCountdown(Number.NaN), "");
});

test("isExpired: 만료 개념이 없으면 만료로 단정하지 않는다", () => {
  // 알 수 없는 걸 만료로 치면 멀쩡한 QR에 재발급 버튼만 남는다.
  assert.equal(isExpired(null, NOW), false);
  assert.equal(isExpired(NOW + 1000, NOW), false);
  assert.equal(isExpired(NOW - 1, NOW), true);
});

test("발급 → 만료까지의 흐름이 일관된다", () => {
  const expiresAt = toExpiryTime(120, NOW);
  assert.equal(formatCountdown(remainingSeconds(expiresAt, NOW)), "2:00");
  assert.equal(formatCountdown(remainingSeconds(expiresAt, NOW + 60_000)), "1:00");
  assert.equal(formatCountdown(remainingSeconds(expiresAt, NOW + 120_000)), "0:00");
  assert.equal(isExpired(expiresAt, NOW + 120_000), true);
});
