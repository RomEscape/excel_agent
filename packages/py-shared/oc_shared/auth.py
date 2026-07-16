"""페어링 토큰 서명/검증 (HMAC-SHA256).

범위: relay에 접속할 때 "이 pairing_id에 이 role(desktop/mobile)로 붙을 자격이 있는가"를
확인하는 대칭키 토큰. relay가 페어링 확정 시 발급하고, 이후 WS 접속마다 검증한다.

주의: 이것은 접속 인증(transport auth)이지 종단간 기밀성(E2E)이 아니다. 실제 콘텐츠 보호는
QR 페어링 시점의 X25519 키교환 + Noise/double-ratchet으로 별도 계층에서 처리한다(추후).
"""

from __future__ import annotations

import hashlib
import hmac


def sign_token(secret: bytes, pairing_id: str, role: str) -> str:
    """pairing_id+role에 대한 HMAC 토큰(hex)."""
    msg = f"{pairing_id}:{role}".encode()
    return hmac.new(secret, msg, hashlib.sha256).hexdigest()


def verify_token(secret: bytes, pairing_id: str, role: str, token: str) -> bool:
    """상수시간 비교로 토큰 검증(타이밍 공격 방지)."""
    expected = sign_token(secret, pairing_id, role)
    return hmac.compare_digest(expected, token)
