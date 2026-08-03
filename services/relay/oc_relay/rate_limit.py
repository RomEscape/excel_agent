"""시도 횟수 제한 — 고정 창(fixed window) 카운터.

`/pair/complete` 무차별 대입 방어용. relay는 content-blind라 사용자 신원이 없으므로
제한 키는 클라이언트 IP다(`app.client_key` 참조).

설계 판단:
  - **전역 잠금은 넣지 않는다.** "전체 실패 N회 → 엔드포인트 차단"은 공격자가 정상
    사용자의 페어링을 막는 DoS 수단이 된다. 제한은 항상 키 단위다.
  - **분산 공격(IP 로테이션)은 이 계층으로 못 막는다.** 그 몫은 code 엔트로피(2^32)와
    TTL이 맡는다 — 세 방어가 곱해져야 의미 있는 난이도가 나온다.
  - 인메모리·단일 프로세스 전제. 멀티 인스턴스로 가면 세션 레지스트리와 함께 Redis
    백플레인으로 옮긴다(인터페이스는 유지).
"""

from __future__ import annotations

import math
import time
from typing import Callable, Dict, Tuple

DEFAULT_MAX_ATTEMPTS = 10
DEFAULT_WINDOW_SECONDS = 60.0


class RateLimiter:
    def __init__(
        self,
        *,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        window_seconds: float = DEFAULT_WINDOW_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        # key → (창 시작 시각, 그 창에서의 시도 횟수)
        self._windows: Dict[str, Tuple[float, int]] = {}
        self._max = max_attempts
        self._window = window_seconds
        self._clock = clock
        self._last_purge = clock()

    def allow(self, key: str) -> bool:
        """시도 1회를 기록하고 허용 여부를 반환. 창을 넘긴 키는 자동으로 리셋된다."""
        now = self._clock()
        self._maybe_purge(now)
        started, count = self._windows.get(key, (now, 0))
        if now - started >= self._window:
            started, count = now, 0
        count += 1
        self._windows[key] = (started, count)
        return count <= self._max

    def retry_after(self, key: str) -> int:
        """현재 창이 닫히기까지 남은 초 — 429의 Retry-After 헤더용."""
        entry = self._windows.get(key)
        if entry is None:
            return 0
        started, _ = entry
        remaining = self._window - (self._clock() - started)
        return max(1, math.ceil(remaining)) if remaining > 0 else 0

    def reset(self, key: str) -> None:
        """성공한 키의 카운터를 비운다 — 정상 사용자가 다음 페어링에서 손해보지 않도록."""
        self._windows.pop(key, None)

    def _maybe_purge(self, now: float) -> None:
        """창이 지난 키를 정리 — IP 키가 무한정 쌓이지 않게.

        매 호출마다 전수 검사하면 키가 많을 때 요청당 O(n)이 되어 그 자체로 공격
        표면이 된다. 창당 최대 1회로 상각한다.
        """
        if now - self._last_purge < self._window:
            return
        self._last_purge = now
        stale = [
            key
            for key, (started, _) in self._windows.items()
            if now - started >= self._window
        ]
        for key in stale:
            del self._windows[key]
