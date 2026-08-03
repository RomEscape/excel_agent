"""페어링 레지스트리 단위 테스트 — TTL·일회성·엔트로피.

만료는 가짜 clock(conftest.FakeClock) 주입으로 sleep 없이 결정적으로 검증한다.
"""

from __future__ import annotations

from oc_relay.pairing import DEFAULT_TTL_SECONDS, PairingRegistry

from .conftest import FakeClock


def test_code_is_8_hex() -> None:
    """2^32 — 6 hex(2^24)는 TTL·rate-limit이 있어도 고속 공격에 뚫린다."""
    _, code = PairingRegistry().start()
    assert len(code) == 8
    assert all(c in "0123456789abcdef" for c in code)


def test_complete_within_ttl_succeeds(clock: FakeClock) -> None:
    reg = PairingRegistry(ttl_seconds=120, clock=clock)
    pairing_id, code = reg.start()

    clock.advance(119)
    assert reg.complete(code) == pairing_id
    assert reg.is_bound(pairing_id)


def test_complete_after_ttl_fails(clock: FakeClock) -> None:
    reg = PairingRegistry(ttl_seconds=120, clock=clock)
    pairing_id, code = reg.start()

    clock.advance(120)  # 경계: 만료 시각 도달은 만료로 본다
    assert reg.complete(code) is None
    assert not reg.is_bound(pairing_id)


def test_expired_code_is_consumed_not_left_behind(clock: FakeClock) -> None:
    """만료된 code로 시도하면 그 code는 소모된다 — 레지스트리에 남기지 않는다."""
    reg = PairingRegistry(ttl_seconds=120, clock=clock)
    _, code = reg.start()

    clock.advance(200)
    assert reg.complete(code) is None
    assert reg.pending_count() == 0


def test_code_is_single_use() -> None:
    reg = PairingRegistry()
    pairing_id, code = reg.start()
    assert reg.complete(code) == pairing_id
    assert reg.complete(code) is None  # 재사용 불가


def test_start_purges_expired_entries(clock: FakeClock) -> None:
    """미소비 code가 무한정 쌓이지 않아야 한다.

    쌓이면 공격자가 '특정' code가 아니라 '아무' code나 맞히면 되므로,
    대기 개수만큼 무차별 대입 성공 확률이 배가된다.
    """
    reg = PairingRegistry(ttl_seconds=120, clock=clock)
    for _ in range(5):
        reg.start()
    assert reg.pending_count() == 5

    clock.advance(121)
    reg.start()  # 발급 시점에 만료분을 청소한다
    assert reg.pending_count() == 1


def test_default_ttl_is_two_minutes() -> None:
    assert DEFAULT_TTL_SECONDS == 120.0
