"""타임아웃 계층 순서 (Task 2).

라우터는 파싱을 `asyncio.wait_for(10초)`로 감싸는데 httpx 클라이언트는 120초였다.
바깥에서만 끊으면 HTTP 요청은 백그라운드에 그대로 살아 있어 Ollama에 부하가
쌓인다. 소켓 쪽이 먼저 끊겨야 요청이 실제로 끝난다.

**값은 안 올렸다.** 감사는 기본 10초를 45초로 올리라고 했지만
`0811-182610-armA-off` 15턴 전부 `timeout_count=0`이다 — 이 구성에서는 타임아웃이
아예 안 난다. 근거 없이 올리면 실패하는 턴만 4.5배 느려진다.
"""

from __future__ import annotations

import httpx
import pytest

from office_claw_sidecar.services.excel_live_agent import (
    HTTP_TIMEOUT_MARGIN_SECONDS,
    http_budget_for,
)
from office_claw_sidecar.services.ollama_service import (
    DEFAULT_HTTP_TIMEOUT_SECONDS,
    OllamaService,
)


class TestHttpBudget:
    def test_it_stays_under_the_parser_budget(self):
        assert http_budget_for(10.0) == 10.0 - HTTP_TIMEOUT_MARGIN_SECONDS

    @pytest.mark.parametrize("budget", [10.0, 14.0, 45.0, 90.0])
    def test_the_socket_always_gives_up_first(self, budget):
        assert http_budget_for(budget) < budget

    def test_a_tiny_budget_still_leaves_a_usable_timeout(self):
        assert http_budget_for(1.0) == 1.0

    @pytest.mark.parametrize("bad", [None, "", "abc", 0, -5])
    def test_an_unknown_budget_falls_back_to_the_default(self, bad):
        assert http_budget_for(bad) is None


class TestTheTimeoutReachesHttpx:
    @pytest.mark.anyio
    async def test_the_given_timeout_is_used(self, monkeypatch):
        seen: dict = {}

        class FakeClient:
            def __init__(self, timeout=None):
                seen["timeout"] = timeout

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return False

            async def post(self, url, json=None):
                return httpx.Response(
                    200,
                    json={"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}]},
                    request=httpx.Request("POST", url),
                )

        monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
        await OllamaService().chat_messages([{"role": "user", "content": "x"}], timeout=8.0)
        assert seen["timeout"] == 8.0

    @pytest.mark.anyio
    async def test_without_one_the_default_applies(self, monkeypatch):
        seen: dict = {}

        class FakeClient:
            def __init__(self, timeout=None):
                seen["timeout"] = timeout

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return False

            async def post(self, url, json=None):
                return httpx.Response(
                    200,
                    json={"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}]},
                    request=httpx.Request("POST", url),
                )

        monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
        await OllamaService().chat_messages([{"role": "user", "content": "x"}])
        assert seen["timeout"] == DEFAULT_HTTP_TIMEOUT_SECONDS


@pytest.fixture
def anyio_backend():
    return "asyncio"
