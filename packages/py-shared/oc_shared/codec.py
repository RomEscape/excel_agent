"""프레임 코덱 — WS 텍스트 프레임 ↔ Envelope.

relay와 endpoint(sidecar/모바일)가 같은 직렬화 규약을 쓰도록 한 곳에 모은다.
"""

from __future__ import annotations

from oc_protocol import Envelope, RoutingHeader


def encode_envelope(env: Envelope) -> str:
    """Envelope → WS로 보낼 JSON 텍스트."""
    return env.model_dump_json()


def decode_envelope(raw: str) -> Envelope:
    """WS 텍스트 → Envelope (payload까지 전체 검증). endpoint에서 사용."""
    return Envelope.model_validate_json(raw)


def parse_routing(raw: str) -> RoutingHeader:
    """WS 텍스트 → 라우팅 헤더만 파싱(payload 무시). relay에서 사용 — content-blind."""
    return RoutingHeader.model_validate_json(raw)
