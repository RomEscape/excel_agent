from __future__ import annotations

import asyncio

from office_claw_sidecar.services import llm_service


def test_ollama_provider_uses_saved_model_config(monkeypatch, tmp_path):
    monkeypatch.setattr(llm_service, "get_data_dir", lambda: tmp_path)
    llm_service.save_llm_config({"provider": "ollama", "model": "ax4-light:latest"})

    captured: dict[str, str | None] = {"model": None}

    async def _fake_chat_messages(self, messages, model=None):  # noqa: ANN001
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

    async def _fake_chat_messages(self, messages, model=None):  # noqa: ANN001
        captured["model"] = model
        return "OK"

    monkeypatch.setattr(llm_service.OllamaService, "chat_messages", _fake_chat_messages)

    provider = llm_service.OllamaProvider()
    result = asyncio.run(provider.chat([{"role": "user", "content": "ping"}], model="ax4-light:latest"))
    assert result == "OK"
    assert captured["model"] == "ax4-light:latest"
