"""oc_protocol — kimdaeri 와이어 프로토콜의 단일 진실(SSOT).

데스크톱(sidecar) ↔ 중계 서버(relay) ↔ 모바일(Flutter) 3자가 공유하는 계약.
- Envelope: relay가 라우팅에 쓰는 '평문' 봉투 (payload는 해석하지 않음 → content-blind)
- Frame: Envelope.payload에 담기는 도메인 프레임 (discriminated union)

파이썬(sidecar/relay)은 이 패키지를 직접 import하고,
Flutter는 export된 JSON Schema(packages/protocol/scripts/export_schema.py)에서 Dart로 codegen한다.
"""

from .envelope import PROTOCOL_VERSION, Direction, Envelope, RoutingHeader
from .frames import (
    Ack,
    AgentState,
    AgentStatus,
    ApprovalRequest,
    ApprovalResponse,
    ChatUserMsg,
    ErrorFrame,
    Frame,
    Ping,
    Pong,
    StreamEnd,
    TokenDelta,
)

__all__ = [
    "PROTOCOL_VERSION",
    "Direction",
    "Envelope",
    "RoutingHeader",
    "Frame",
    "AgentState",
    "ChatUserMsg",
    "TokenDelta",
    "StreamEnd",
    "AgentStatus",
    "ApprovalRequest",
    "ApprovalResponse",
    "Ack",
    "Ping",
    "Pong",
    "ErrorFrame",
]
