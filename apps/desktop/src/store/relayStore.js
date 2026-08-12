/**
 * 모바일 릴레이(중계 서버) 연동 상태 저장소.
 *
 * 도메인 상태는 이 store가 소유하고, 액션은 `lib/relayManager.js`가 가진다.
 * UI(페어링 다이얼로그)는 구독만 한다.
 *
 * ## Phase
 *   idle      — 미연동 (초기)
 *   pairing   — /relay/pair 요청 중
 *   waiting   — QR 표시됨, 모바일 스캔 대기 (상태 폴링 중)
 *   connected — 모바일 연결됨
 *   error     — 실패 (lastError 참조)
 */
import { create } from "zustand";

/** @typedef {'idle'|'pairing'|'waiting'|'connected'|'error'} RelayPhase */

const useRelayStore = create((set) => ({
  /** @type {RelayPhase} */
  phase: "idle",
  /** @type {{pairing_id: string, code: string, relay_url: string, expires_in?: number}|null} QR에 담을 페어링 정보 */
  pairing: null,
  /**
   * 페어링 code 만료 시각 (epoch ms). null이면 만료 개념 없음(구버전 relay).
   * 계산은 lib/pairingCountdown.js가 하고, 여기는 값만 갖는다.
   */
  pairingExpiresAt: null,
  enabled: false,
  connected: false,
  /** @type {string|null} */
  relayUrl: null,
  /** @type {string|null} */
  lastError: null,

  setPhase: (phase) => set({ phase }),
  setPairing: (pairing, pairingExpiresAt = null) => set({ pairing, pairingExpiresAt }),
  setStatus: ({ enabled, connected, relayUrl }) =>
    set({ enabled, connected, relayUrl }),
  setError: (lastError) => set({ lastError, phase: "error" }),
  reset: () =>
    set({ phase: "idle", pairing: null, pairingExpiresAt: null, lastError: null }),
}));

export default useRelayStore;
