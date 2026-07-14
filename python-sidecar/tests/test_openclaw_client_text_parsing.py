"""OpenClaw chat 이벤트 텍스트 추출 회귀 테스트."""

from __future__ import annotations

from office_claw_sidecar.services.openclaw_client import OpenClawClient


def _client() -> OpenClawClient:
    # 네트워크 연결 없이 private helper만 검증한다.
    return OpenClawClient(port=18789)


def test_extract_text_from_output_text_item():
    client = _client()
    message = {
        "role": "assistant",
        "content": [
            {"type": "output_text", "text": "OK"},
        ],
    }
    assert client._extract_text_from_message(message) == "OK"


def test_extract_text_from_nested_response_message():
    client = _client()
    payload = {
        "state": "final",
        "runId": "run-1",
        "response": {
            "message": {
                "content": [{"type": "text", "text": "안녕하세요"}],
            }
        },
    }
    frame = client._normalize_chat_event("agent:test", payload)
    assert frame is not None
    assert frame.get("content") == "안녕하세요"


def test_extract_text_from_top_level_output_text():
    client = _client()
    payload = {
        "state": "final",
        "runId": "run-2",
        "outputText": "한 단어로 OK",
    }
    frame = client._normalize_chat_event("agent:test", payload)
    assert frame is not None
    assert frame.get("content") == "한 단어로 OK"


def test_extract_text_from_delta_object():
    client = _client()
    payload = {
        "state": "delta",
        "runId": "run-3",
        "delta": {"type": "output_text", "text": "조각 응답"},
    }
    frame = client._normalize_chat_event("agent:test", payload)
    assert frame is not None
    assert frame.get("content") == "조각 응답"


def test_normalize_chat_event_without_text_keeps_empty_content():
    client = _client()
    payload = {
        "state": "final",
        "runId": "run-4",
        "message": {"role": "assistant"},
    }
    frame = client._normalize_chat_event("agent:test", payload)
    assert frame is not None
    assert "content" not in frame
