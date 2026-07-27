/**
 * 릴레이 연동 액션 — 페어링 개시 / 상태 폴링 / 연결 해제.
 *
 * 상태는 `store/relayStore.js`가 소유하고, UI는 이 매니저의 액션만 호출한다.
 * 모든 IPC는 `lib/api.js`를 경유한다(단일 진입점 규칙).
 */
import { relayDisconnect, relayPair, relayStatus } from "./api";
import useRelayStore from "../store/relayStore";

// QR 페이로드 계약은 순수 모듈(relayQr)이 소유 — 유닛테스트로 고정된다.
// 매니저는 파사드로 재수출해 UI가 한 곳만 import하게 한다.
export { buildQrPayload, QR_PAYLOAD_VERSION } from "./relayQr";

/**
 * 페어링 개시 → QR 정보 확보(phase=waiting).
 *
 * @param {string} [relayUrl] 지정 시 relay 주소를 이 값으로 갱신하고 사용
 * @returns {Promise<{pairing_id: string, code: string, relay_url: string}>}
 */
export async function startPairing(relayUrl) {
  useRelayStore.getState().setPhase("pairing");
  try {
    const info = await relayPair(relayUrl);
    useRelayStore.getState().setPairing(info);
    useRelayStore.getState().setPhase("waiting");
    return info;
  } catch (e) {
    useRelayStore.getState().setError(String(e?.message || e));
    throw e;
  }
}

/** 상태 1회 갱신. connected면 phase를 connected로 올린다. */
export async function refreshStatus() {
  try {
    const st = await relayStatus();
    useRelayStore.getState().setStatus({
      enabled: !!st.enabled,
      connected: !!st.connected,
      relayUrl: st.relay_url ?? null,
    });
    if (st.connected) useRelayStore.getState().setPhase("connected");
    return st;
  } catch (e) {
    useRelayStore.getState().setError(String(e?.message || e));
    return null;
  }
}

/**
 * 모바일이 붙을 때까지 주기적으로 상태를 폴링한다.
 *
 * @param {number} [intervalMs]
 * @returns {() => void} 폴링 정지 함수
 */
export function pollUntilConnected(intervalMs = 2000) {
  let stopped = false;
  let timer = null;
  const tick = async () => {
    if (stopped) return;
    const st = await refreshStatus();
    if (stopped || st?.connected) return;
    timer = setTimeout(tick, intervalMs);
  };
  tick();
  return () => {
    stopped = true;
    if (timer) clearTimeout(timer);
  };
}

/** 연동 해제 — 사이드카 클라이언트 정리 + 상태 리셋. */
export async function disconnect() {
  try {
    await relayDisconnect();
    useRelayStore.getState().reset();
    useRelayStore
      .getState()
      .setStatus({ enabled: false, connected: false, relayUrl: null });
  } catch (e) {
    useRelayStore.getState().setError(String(e?.message || e));
  }
}
