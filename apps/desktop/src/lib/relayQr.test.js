import { strict as assert } from "node:assert";
import { test } from "node:test";

import { buildQrPayload, QR_PAYLOAD_VERSION } from "./relayQr.js";

test("QR 페이로드가 모바일 파서 계약과 일치한다", () => {
  const raw = buildQrPayload({
    relay_url: "http://127.0.0.1:8787",
    pairing_id: "p1",
    code: "abc123",
  });
  const p = JSON.parse(raw);

  assert.equal(p.v, QR_PAYLOAD_VERSION);
  // 사이드카의 relay_url → 모바일이 기대하는 relay 키로 매핑돼야 한다
  assert.equal(p.relay, "http://127.0.0.1:8787");
  assert.equal(p.pairing_id, "p1");
  assert.equal(p.code, "abc123");
});

test("모바일이 요구하는 키가 전부 있고 그 외는 없다", () => {
  const raw = buildQrPayload({
    relay_url: "https://relay.example.com",
    pairing_id: "abc",
    code: "xyz",
  });
  assert.deepEqual(Object.keys(JSON.parse(raw)).sort(), [
    "code",
    "pairing_id",
    "relay",
    "v",
  ]);
});

test("relay_url이 그대로 실려야 한다(스킴 유지)", () => {
  const raw = buildQrPayload({
    relay_url: "https://relay.example.com:8443",
    pairing_id: "p",
    code: "c",
  });
  assert.equal(JSON.parse(raw).relay, "https://relay.example.com:8443");
});
