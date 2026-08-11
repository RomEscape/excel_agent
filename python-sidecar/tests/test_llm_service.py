from __future__ import annotations

import asyncio

from office_claw_sidecar.services import llm_service, ollama_service


def test_ollama_provider_uses_saved_model_config(monkeypatch, tmp_path):
    monkeypatch.setattr(llm_service, "get_data_dir", lambda: tmp_path)
    llm_service.save_llm_config({"provider": "ollama", "model": "ax4-light:latest"})

    captured: dict[str, str | None] = {"model": None}

    async def _fake_chat_messages(self, messages, model=None, temperature=None, json_only=False):  # noqa: ANN001
        captured["model"] = model
        return "OK"

    monkeypatch.setattr(llm_service.OllamaService, "chat_messages", _fake_chat_messages)

    provider = llm_service.OllamaProvider()
    result = asyncio.run(provider.chat([{"role": "user", "content": "ping"}]))
    assert result == "OK"
    assert captured["model"] == "ax4-light:latest"


def test_ollama_provider_explicit_model_overrides_saved(monkeypatch, tmp_path):
    monkeypatch.setattr(llm_service, "get_data_dir", lambda: tmp_path)
    llm_service.save_llm_config({"provider": "ollama", "model": "qwen3:4b"})

    captured: dict[str, str | None] = {"model": None}

    async def _fake_chat_messages(self, messages, model=None, temperature=None, json_only=False):  # noqa: ANN001
        captured["model"] = model
        return "OK"

    monkeypatch.setattr(llm_service.OllamaService, "chat_messages", _fake_chat_messages)

    provider = llm_service.OllamaProvider()
    result = asyncio.run(provider.chat([{"role": "user", "content": "ping"}], model="ax4-light:latest"))
    assert result == "OK"
    assert captured["model"] == "ax4-light:latest"


def test_temperature_is_forwarded_to_ollama(monkeypatch):
    """계획 수립은 greedy여야 한다. 온도가 전달되지 않으면 같은 문장이 실행마다 다른 계획이 된다."""
    llm_service.save_llm_config({"provider": "ollama", "model": "ax4-light:latest"})
    captured: dict[str, float | None] = {"temperature": None}

    async def _fake_chat_messages(self, messages, model=None, temperature=None, json_only=False):  # noqa: ANN001
        captured["temperature"] = temperature
        return "OK"

    monkeypatch.setattr(llm_service.OllamaService, "chat_messages", _fake_chat_messages)
    provider = llm_service.OllamaProvider()
    asyncio.run(provider.chat([{"role": "user", "content": "ping"}], temperature=0.0))
    assert captured["temperature"] == 0.0


def test_planner_asks_for_greedy_decoding():
    from office_claw_sidecar.services.excel_live_agent import PLAN_TEMPERATURE

    assert PLAN_TEMPERATURE == 0.0


# ── json_only ─────────────────────────────────────────────────────────────
#
# 응답이 JSON이어야만 하는 호출은 디코딩 자체를 JSON 문법으로 묶는다. 앞뒤에 설명이
# 붙는 것을 파서로 걷어내는 것보다, 애초에 못 붙게 하는 쪽이 확실하다.


def _capture_ollama_payload(monkeypatch) -> dict:
    """OllamaService가 실제로 보내는 HTTP 본문을 붙잡는다.

    provider가 플래그를 삼켜 버려도 테스트가 통과하면 안 되므로, 중간 계층이 아니라
    나가는 요청 본문을 본다.
    """
    captured: dict = {}

    class _FakeResponse:
        status_code = 200

        @staticmethod
        def raise_for_status() -> None:
            return None

        @staticmethod
        def json() -> dict:
            return {"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}]}

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, json=None):
            captured.update(json or {})
            return _FakeResponse()

    monkeypatch.setattr(ollama_service.httpx, "AsyncClient", _FakeClient)
    return captured


def test_json_only_constrains_decoding_to_json(monkeypatch):
    captured = _capture_ollama_payload(monkeypatch)

    asyncio.run(
        ollama_service.OllamaService().chat_messages(
            [{"role": "user", "content": "ping"}], model="ax4-light:latest", json_only=True
        )
    )

    assert captured["response_format"] == {"type": "json_object"}


def test_ordinary_chat_is_left_unconstrained(monkeypatch):
    """대화 응답까지 JSON으로 묶으면 사용자에게 중괄호가 보인다."""
    captured = _capture_ollama_payload(monkeypatch)

    asyncio.run(
        ollama_service.OllamaService().chat_messages(
            [{"role": "user", "content": "ping"}], model="ax4-light:latest"
        )
    )

    assert "response_format" not in captured


def test_the_flag_survives_the_trip_from_service_to_payload(monkeypatch):
    """LLMService → provider → OllamaService 사이에서 플래그가 새지 않아야 한다."""
    llm_service.save_llm_config({"provider": "ollama", "model": "ax4-light:latest"})
    captured = _capture_ollama_payload(monkeypatch)

    service = llm_service.LLMService(llm_service.OllamaProvider())
    asyncio.run(service.chat([{"role": "user", "content": "ping"}], json_only=True))

    assert captured["response_format"] == {"type": "json_object"}


def test_claude_ignores_json_only_instead_of_crashing(monkeypatch):
    """Claude에는 대응 옵션이 없다. 무시하되 호출은 성공해야 한다."""
    seen: dict = {}

    async def _fake_chat_messages(self, messages, model=None):
        seen["called"] = True
        return "OK"

    monkeypatch.setattr(llm_service.ClaudeService, "chat_messages", _fake_chat_messages)

    provider = llm_service.ClaudeProvider()
    result = asyncio.run(provider.chat([{"role": "user", "content": "ping"}], json_only=True))

    assert result == "OK"
    assert seen["called"] is True


def test_the_planner_asks_for_json_only(monkeypatch):
    """플래너가 플래그를 붙이지 않으면 위 배관은 아무 의미가 없다."""
    from office_claw_sidecar.services import excel_live_agent

    seen: dict = {}

    class _RecordingLLM:
        async def chat(self, messages, model=None, temperature=None, json_only=False):
            seen["json_only"] = json_only
            return (
                '{"intent": "read", "mutates_workbook": false, "action_plan": ['
                '{"action": "excel_live.read_range",'
                ' "params": {"target_range": "A1"}, "reason": "읽기"}]}'
            )

    asyncio.run(
        excel_live_agent.parse_command_plan_with_llm(
            "A1 읽어줘", _RecordingLLM(), context={"sheet_name": "Sheet1"}
        )
    )

    assert seen["json_only"] is True
