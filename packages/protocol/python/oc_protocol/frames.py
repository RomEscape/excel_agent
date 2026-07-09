"""프레임 정의 — Envelope.payload에 담기는 도메인 메시지.

relay는 이 프레임의 '내용'을 절대 해석하지 않는다(Envelope의 라우팅 헤더만 본다).
따라서 여기 정의는 데스크톱(sidecar)과 모바일(Flutter) 두 endpoint 사이의 계약이며,
E2E 암호화 도입 시에도 relay는 그대로 불투명 payload로 전달한다.

3대 최우선 기능에 맞춘 프레임:
  1) 스트리밍 채팅  : ChatUserMsg / TokenDelta / StreamEnd
  2) 에이전트 상태  : AgentStatus (+ AgentState)
  3) HITL 승인      : ApprovalRequest / ApprovalResponse
공통 제어         : Ack(재연결 재개) / Ping·Pong(liveness) / ErrorFrame
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field


class AgentState(str, Enum):
    """데스크톱 에이전트의 현재 상태 (모바일 상단 인디케이터용)."""

    idle = "idle"
    thinking = "thinking"  # 추론(LLM 생성) 중
    remote_controlling = "remote_controlling"  # 원격 제어(액션 실행) 중
    offline = "offline"  # 데스크톱 연결 끊김 — relay presence가 대체 통지


class ChatUserMsg(BaseModel):
    """모바일 → 데스크톱: 사용자 입력."""

    type: Literal["chat_user_msg"] = "chat_user_msg"
    client_msg_id: str = Field(..., description="모바일이 생성한 멱등 키(재전송 중복 제거)")
    text: str


class TokenDelta(BaseModel):
    """데스크톱 → 모바일: LLM 토큰 스트리밍 조각."""

    type: Literal["token_delta"] = "token_delta"
    stream_id: str = Field(..., description="하나의 assistant 응답을 식별")
    index: int = Field(..., description="스트림 내 조각 순번(0-base) — 순서 복원/중복 제거")
    text: str


class StreamEnd(BaseModel):
    """데스크톱 → 모바일: 스트림 종료/중단.

    잘린 스트림이 '손상된 최종 메시지'로 남지 않도록 명시적 종료 시맨틱을 준다.
    """

    type: Literal["stream_end"] = "stream_end"
    stream_id: str
    reason: Literal["complete", "aborted", "error"] = "complete"
    error: str | None = None


class AgentStatus(BaseModel):
    """데스크톱 → 모바일: 에이전트 상태 동기화."""

    type: Literal["agent_status"] = "agent_status"
    state: AgentState


class ApprovalRequest(BaseModel):
    """데스크톱 → 모바일: HITL 승인 요청 (CONFIRM 권한 동작)."""

    type: Literal["approval_request"] = "approval_request"
    request_id: str
    command: str = Field(..., description="승인 대상 동작 요약")
    reason: str = Field(..., description="왜 승인이 필요한지")


class ApprovalResponse(BaseModel):
    """모바일 → 데스크톱: 승인 응답.

    보안 주의: E2E 도입 시 이 프레임은 반드시 인증(MAC)되어야 한다.
    그래야 relay가 승인을 위조하거나 재전송(replay)해 CONFIRM 게이트를 우회하지 못한다.
    """

    type: Literal["approval_response"] = "approval_response"
    request_id: str
    approved: bool


class Ack(BaseModel):
    """양방향: 수신 확인 — 상대편 replay 버퍼의 절단점.

    재연결 시 마지막 ack_seq 이후만 재전송하면 되므로 토큰 유실·중복을 막는다.
    """

    type: Literal["ack"] = "ack"
    ack_seq: int = Field(..., description="여기까지 연속 수신 완료한 최대 seq")


class Ping(BaseModel):
    """양방향: 앱 레벨 liveness — half-open 소켓 탐지(NAT idle timeout 대응)."""

    type: Literal["ping"] = "ping"
    nonce: str


class Pong(BaseModel):
    type: Literal["pong"] = "pong"
    nonce: str


class ErrorFrame(BaseModel):
    """relay 또는 endpoint → : 오류 통지(예: 세션 없음, 버전 불일치)."""

    type: Literal["error"] = "error"
    code: str
    message: str


# 모든 프레임의 discriminated union. `type` 필드로 역직렬화 분기.
Frame = Annotated[
    Union[
        ChatUserMsg,
        TokenDelta,
        StreamEnd,
        AgentStatus,
        ApprovalRequest,
        ApprovalResponse,
        Ack,
        Ping,
        Pong,
        ErrorFrame,
    ],
    Field(discriminator="type"),
]
