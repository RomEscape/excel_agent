"""Envelope — relay가 라우팅에 사용하는 '평문' 봉투.

relay는 Envelope의 라우팅 헤더(pairing_id/direction/seq)만 읽고 payload는 해석하지 않는다.
E2E 도입 시 payload는 typed Frame 대신 암호문 blob으로 대체될 수 있으며, Envelope 구조와
relay의 라우팅 로직은 그대로 유지된다(content-blind).

- Endpoint(sidecar/모바일)는 payload까지 검증하는 `Envelope`를 사용.
- relay는 payload를 무시하고 라우팅 필드만 파싱하는 `RoutingHeader`를 사용한다.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from .frames import Frame

PROTOCOL_VERSION = 1


class Direction(str, Enum):
    """프레임의 논리적 진행 방향(라우팅 교차검증용)."""

    to_desktop = "to_desktop"  # 모바일 → (relay) → 데스크톱
    to_mobile = "to_mobile"  # 데스크톱 → (relay) → 모바일


class Envelope(BaseModel):
    """endpoint용 전체 봉투 — payload(Frame)까지 검증."""

    v: int = Field(default=PROTOCOL_VERSION, description="프로토콜 버전")
    pairing_id: str = Field(..., description="relay 라우팅 키 (1:1 바인딩된 세션)")
    direction: Direction
    seq: int = Field(..., description="(pairing_id, direction)별 단조 증가 순번 — 재연결 재개")
    payload: Frame


class RoutingHeader(BaseModel):
    """relay 전용 — payload를 해석하지 않고 라우팅 필드만 파싱(content-blind).

    `extra="ignore"`로 payload 등 나머지 필드를 버린다. relay는 원본 raw 텍스트를
    그대로 전달하므로 payload는 손실 없이(그리고 불투명하게) 상대편으로 넘어간다.
    """

    model_config = ConfigDict(extra="ignore")

    v: int = PROTOCOL_VERSION
    pairing_id: str
    direction: Direction
    seq: int
