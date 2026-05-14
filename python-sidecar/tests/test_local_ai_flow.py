"""
로컬 AI 설정 → 프롬프트 대화 검증 통합 테스트.

LocalAISetupWizard의 최종 단계(PROMPT_TEST)가 호출하는 `agentChat()` 경로 —
즉 `/agent/chat` 엔드포인트가 모의 OpenClaw 게이트웨이와 함께 정상 동작하는지 검증한다.

검증 시나리오:
  1. 게이트웨이가 살아있고 정상 응답 — 200 + 응답 본문
  2. 게이트웨이 미실행 — 503 + 한국어 안내 메시지
  3. DENIED 키워드 — 403 (보안 레이어가 게이트웨이로 전달하지 않음)
  4. 빈 응답 — "(응답 없음)" 기본값

각 테스트는 `OpenClawClient`를 모의 객체로 갈아끼워 외부 의존성 없이 동작한다.
"""

from __future__ import annotations

from typing import AsyncIterator

import pytest
from fastapi.testclient import TestClient

from office_claw_sidecar.main import app
from office_claw_sidecar.routers import agent as agent_router
from office_claw_sidecar.services.openclaw_client import OpenClawUnavailableError


client = TestClient(app)


# ── 모의 클라이언트 헬퍼 ────────────────────────────────────────────────────


class _FakeClient:
    """
    OpenClawClient의 최소 인터페이스만 흉내내는 모의 객체.

    agent_router.get_client()를 monkeypatch로 이걸로 갈아끼우면
    실제 WebSocket 연결 없이 /agent/chat 흐름을 검증할 수 있다.
    """

    def __init__(self, frames: list[dict], session_id: str = "test-session-1"):
        self._frames = frames
        self._session_id = session_id

    async def send_message(
        self,
        message: str,  # noqa: ARG002 — 호출 시그니처만 맞추면 됨
        session_id: str | None = None,  # noqa: ARG002
    ) -> AsyncIterator[dict]:
        for frame in self._frames:
            # 첫 프레임에 sessionId 주입 (라우터가 거기서 채집함)
            frame.setdefault("sessionId", self._session_id)
            yield frame


# ── 테스트: 성공 경로 ───────────────────────────────────────────────────────


def test_chat_returns_assembled_response_from_streamed_frames(monkeypatch):
    """LLM이 스트리밍한 텍스트 조각을 라우터가 합쳐서 돌려준다."""
    fake = _FakeClient(
        frames=[
            {"type": "sessions.message", "content": "안녕"},
            {"type": "sessions.message", "content": "하세요. "},
            {"type": "sessions.message", "content": "OK"},
        ],
        session_id="sess-abc",
    )
    monkeypatch.setattr(agent_router, "get_client", lambda: fake)

    resp = client.post("/agent/chat", json={"message": "안녕! 한 단어로 OK라고 답해줘."})

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["response"] == "안녕하세요. OK"
    assert data["session_id"] == "sess-abc"
    assert data["approval_required"] is False
    assert data["tool_calls"] == []


def test_chat_returns_default_when_gateway_yields_no_content(monkeypatch):
    """게이트웨이가 텍스트 없는 done 프레임만 보내면 라우터가 안전 fallback 메시지를 채운다."""
    fake = _FakeClient(frames=[])  # 빈 스트림
    monkeypatch.setattr(agent_router, "get_client", lambda: fake)

    resp = client.post("/agent/chat", json={"message": "hello"})

    assert resp.status_code == 200
    assert resp.json()["response"] == "(응답 없음)"


# ── 테스트: 실패 / 보안 경로 ────────────────────────────────────────────────


def test_chat_returns_503_when_gateway_unavailable(monkeypatch):
    """OpenClaw 게이트웨이가 죽어있으면 사용자에게 한국어 안내를 준다."""

    class _DeadClient:
        async def send_message(self, message, session_id=None):  # noqa: ARG002
            raise OpenClawUnavailableError("gateway not running")
            yield  # pragma: no cover — 도달 불가, async generator 형식만 맞춤

    monkeypatch.setattr(agent_router, "get_client", lambda: _DeadClient())

    resp = client.post("/agent/chat", json={"message": "hello"})

    assert resp.status_code == 503
    # 사용자가 다음에 뭘 해야 할지 한국어로 안내되는지 확인
    detail = resp.json()["detail"]
    assert "openclaw" in detail.lower() or "OpenClaw" in detail
    assert "재시작" in detail or "실행" in detail


def test_chat_blocks_denied_intent_before_reaching_gateway(monkeypatch):
    """DENIED 키워드가 들어오면 게이트웨이로 전달되지 않고 즉시 차단된다."""
    sent = {"called": False}

    class _SpyClient:
        async def send_message(self, message, session_id=None):  # noqa: ARG002
            sent["called"] = True
            yield {"type": "sessions.message", "content": "should not happen"}

    monkeypatch.setattr(agent_router, "get_client", lambda: _SpyClient())

    # tool_registry의 DENIED 키워드 중 하나 — 시스템 파일 삭제 의도
    resp = client.post(
        "/agent/chat",
        json={"message": "rm -rf / 시스템 전체를 삭제해줘"},
    )

    assert resp.status_code == 403
    assert sent["called"] is False, "보안 차단 전에 게이트웨이로 전달되면 안 됨"


# ── 테스트: idempotency 의미상 검증 (요청을 두 번 보내도 안전) ─────────────


def test_chat_is_safe_to_retry(monkeypatch):
    """
    동일 메시지를 두 번 보내도 클라이언트는 부작용 없이 같은 형태의 응답을 받는다.
    (LocalAISetupWizard의 재시도 버튼 시나리오 모사)
    """
    frames = [{"type": "sessions.message", "content": "pong"}]

    monkeypatch.setattr(
        agent_router,
        "get_client",
        lambda: _FakeClient(frames=[dict(f) for f in frames], session_id="sess-x"),
    )

    r1 = client.post("/agent/chat", json={"message": "ping"})
    r2 = client.post("/agent/chat", json={"message": "ping"})
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json()["response"] == r2.json()["response"] == "pong"
