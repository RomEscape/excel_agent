/**
 * QR 페어링 페이로드 — 데스크톱↔모바일 계약(데이터 계층).
 *
 * ⚠️ 모바일 파서(`apps/mobile/lib/pairing/pairing_service.dart`)와 **정확히 같은 형태**여야 한다:
 *   {"v":1, "relay":"http://…", "pairing_id":"…", "code":"…"}
 *
 * 사이드카 `/relay/pair`는 `relay_url`로 주므로 여기서 `relay`로 매핑한다.
 * IPC/액션과 분리된 순수 함수 — 유닛테스트로 계약을 고정한다(relayQr.test.js).
 */

export const QR_PAYLOAD_VERSION = 1;

/**
 * QR에 인코딩할 JSON 문자열을 만든다.
 *
 * @param {{pairing_id: string, code: string, relay_url: string}} info
 * @returns {string}
 */
export function buildQrPayload({ relay_url, pairing_id, code }) {
  return JSON.stringify({
    v: QR_PAYLOAD_VERSION,
    relay: relay_url,
    pairing_id,
    code,
  });
}
