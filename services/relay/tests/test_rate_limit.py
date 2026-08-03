"""RateLimiter 단위 테스트 — 예산 소진·창 리셋·키 분리·키 청소."""

from __future__ import annotations

from oc_relay.rate_limit import RateLimiter

from .conftest import FakeClock


def test_allows_up_to_max_then_blocks(clock: FakeClock) -> None:
    limiter = RateLimiter(max_attempts=3, window_seconds=60, clock=clock)

    assert [limiter.allow("ip") for _ in range(3)] == [True, True, True]
    assert limiter.allow("ip") is False


def test_window_resets_after_expiry(clock: FakeClock) -> None:
    limiter = RateLimiter(max_attempts=2, window_seconds=60, clock=clock)

    limiter.allow("ip")
    limiter.allow("ip")
    assert limiter.allow("ip") is False

    clock.advance(60)
    assert limiter.allow("ip") is True  # 새 창


def test_keys_are_independent(clock: FakeClock) -> None:
    """한 IP가 예산을 태워도 다른 IP는 멀쩡해야 한다(전역 잠금은 그 자체가 DoS 수단)."""
    limiter = RateLimiter(max_attempts=1, window_seconds=60, clock=clock)

    assert limiter.allow("a") is True
    assert limiter.allow("a") is False
    assert limiter.allow("b") is True


def test_reset_clears_counter(clock: FakeClock) -> None:
    limiter = RateLimiter(max_attempts=2, window_seconds=60, clock=clock)

    limiter.allow("ip")
    limiter.allow("ip")
    assert limiter.allow("ip") is False

    limiter.reset("ip")  # 페어링 성공 시
    assert limiter.allow("ip") is True


def test_retry_after_counts_down_within_window(clock: FakeClock) -> None:
    limiter = RateLimiter(max_attempts=1, window_seconds=60, clock=clock)

    assert limiter.retry_after("ip") == 0  # 기록 없음
    limiter.allow("ip")
    assert limiter.retry_after("ip") == 60

    clock.advance(30)
    assert limiter.retry_after("ip") == 30

    clock.advance(30)
    assert limiter.retry_after("ip") == 0


def test_stale_keys_are_purged(clock: FakeClock) -> None:
    """IP 키가 무한정 쌓이지 않아야 한다(메모리 고갈 방지)."""
    limiter = RateLimiter(max_attempts=5, window_seconds=60, clock=clock)

    for i in range(100):
        limiter.allow(f"ip-{i}")
    assert len(limiter._windows) == 100

    clock.advance(61)
    limiter.allow("new")  # 창당 1회로 상각된 청소
    assert len(limiter._windows) == 1
