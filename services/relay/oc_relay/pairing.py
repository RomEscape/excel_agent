"""기기 페어링 — QR 코드 기반 1:1 바인딩(서버 측 최소 구현).

흐름:
  1) 데스크톱이 POST /pair/start → (pairing_id, code) 발급, code를 QR로 표시.
  2) 모바일이 QR 스캔 후 POST /pair/complete{code} → 바인딩 확정, pairing_id 수신.
  3) 이후 양쪽은 이 pairing_id로 /ws/desktop·/ws/mobile 접속.

MVP 한계(비평 반영 — 프로덕션 전 반드시 강화):
  - code에 짧은 TTL(60~120초)과 페어링 시도 rate-limit이 필요.
  - QR에 데스크톱 ephemeral 공개키를 실어, 페어링 후 SAS(숫자/이모지 지문) 육안 대조로
    relay MITM(키 바꿔치기)을 차단해야 한다.
  - 장기 신원키는 데스크톱 OS 키체인(keyring_svc)에 저장, 기기 revoke/재페어 지원.
"""

from __future__ import annotations

import secrets
from typing import Dict, Optional, Set, Tuple


class PairingRegistry:
    def __init__(self) -> None:
        self._pending: Dict[str, str] = {}  # code → pairing_id (일회성)
        self._bound: Set[str] = set()

    def start(self) -> Tuple[str, str]:
        pairing_id = secrets.token_urlsafe(16)
        code = secrets.token_hex(3)  # 6 hex — MVP (프로덕션은 TTL/rate-limit 필요)
        self._pending[code] = pairing_id
        return pairing_id, code

    def complete(self, code: str) -> Optional[str]:
        # 일회성: 성공 시 즉시 소모해 재사용을 막는다.
        pairing_id = self._pending.pop(code, None)
        if pairing_id is None:
            return None
        self._bound.add(pairing_id)
        return pairing_id

    def is_bound(self, pairing_id: str) -> bool:
        return pairing_id in self._bound
