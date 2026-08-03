"""기기 페어링 — QR 코드 기반 1:1 바인딩(서버 측 최소 구현).

흐름:
  1) 데스크톱이 POST /pair/start → (pairing_id, code) 발급, code를 QR로 표시.
  2) 모바일이 QR 스캔 후 POST /pair/complete{code} → 바인딩 확정, pairing_id 수신.
  3) 이후 양쪽은 이 pairing_id로 /ws/desktop·/ws/mobile 접속.

보안 설계:
  - **code TTL(기본 120초)**. TTL이 없으면 미사용 code가 계속 쌓이고, 공격자는 '특정'
    code가 아니라 '아무' code나 맞히면 되므로 성공 확률이 대기 개수만큼 배가된다.
    QR을 띄워둔 채 방치해도 창이 닫혀야 한다.
  - **code 엔트로피 8 hex(2^32)**. 6 hex(2^24)는 TTL·rate-limit이 있어도 초당 1만 회
    공격에 한 창(120초)당 약 7% 확률로 뚫린다. 8 hex면 같은 조건에서 0.03%.
  - 시도 횟수 제한은 이 모듈이 아니라 `rate_limit.RateLimiter`가 소유한다(app.py에서 결합).
    TTL과 rate-limit은 서로를 대체하지 못한다 — TTL은 창을 좁히고, rate-limit은 창
    안에서의 시도 속도를 깎는다.

남은 MVP 한계(프로덕션 전 강화):
  - QR에 데스크톱 ephemeral 공개키를 실어, 페어링 후 SAS(숫자/이모지 지문) 육안 대조로
    relay MITM(키 바꿔치기)을 차단해야 한다.
  - 장기 신원키는 데스크톱 OS 키체인(keyring_svc)에 저장, 기기 revoke/재페어 지원.
    현재 `_bound`는 폐기 수단이 없어 pairing_id가 한 번 새면 영구 유효하다.
"""

from __future__ import annotations

import secrets
import time
from typing import Callable, Dict, Optional, Set, Tuple

# QR을 스캔해 /pair/complete까지 가기엔 넉넉하고, 방치된 창을 오래 열어두진 않는 값.
DEFAULT_TTL_SECONDS = 120.0

# 페어링 code 바이트 수(hex 문자 수는 2배). 4바이트 = 8 hex = 2^32.
_CODE_BYTES = 4


class PairingRegistry:
    def __init__(
        self,
        *,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        # code → (pairing_id, expires_at). 일회성 + 만료.
        self._pending: Dict[str, Tuple[str, float]] = {}
        self._bound: Set[str] = set()
        self._ttl = ttl_seconds
        # monotonic 기본값: NTP 보정 등 시스템 시계 변경에 영향받지 않는다.
        # 테스트는 가짜 clock을 주입해 sleep 없이 만료를 검증한다.
        self._clock = clock

    @property
    def ttl_seconds(self) -> float:
        """발급되는 code의 유효 시간 — 클라이언트에 알려 카운트다운/재발급에 쓴다."""
        return self._ttl

    def start(self) -> Tuple[str, str]:
        # 만료분 청소는 발급 시점에 한다 — /pair/start는 인증이 없어 누구나 부를 수 있으므로
        # 여기서 치워야 _pending이 무한정 자라지 않는다.
        self._purge_expired()
        pairing_id = secrets.token_urlsafe(16)
        code = secrets.token_hex(_CODE_BYTES)
        self._pending[code] = (pairing_id, self._clock() + self._ttl)
        return pairing_id, code

    def complete(self, code: str) -> Optional[str]:
        # 일회성: 만료 여부와 무관하게 pop해서 재사용을 막는다.
        entry = self._pending.pop(code, None)
        if entry is None:
            return None
        pairing_id, expires_at = entry
        if self._clock() >= expires_at:
            return None  # 만료 — 소모만 하고 바인딩하지 않는다
        self._bound.add(pairing_id)
        return pairing_id

    def is_bound(self, pairing_id: str) -> bool:
        return pairing_id in self._bound

    def pending_count(self) -> int:
        """대기 중(미만료) code 개수 — 관측·테스트용."""
        now = self._clock()
        return sum(1 for _, expires_at in self._pending.values() if now < expires_at)

    def _purge_expired(self) -> None:
        now = self._clock()
        expired = [
            code for code, (_, expires_at) in self._pending.items() if now >= expires_at
        ]
        for code in expired:
            del self._pending[code]
