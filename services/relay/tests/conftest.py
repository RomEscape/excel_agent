"""테스트 공용 도구.

TTL·rate-limit 창은 시간이 흘러야 검증되는데 `time.sleep`으로 재현하면 테스트가
느려지고 경계에서 흔들린다. 가짜 단조 시계를 주입해 결정적으로 검증한다.
"""

from __future__ import annotations

import pytest


class FakeClock:
    """수동으로 굴리는 단조 시계 — `clock` 인자에 그대로 넣는다."""

    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()
