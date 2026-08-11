"""Ollama OpenAI 호환 엔드포인트가 `response_format`을 실제로 따르는지 확인한다.

`format: "json"`을 켤지 말지는 문서가 아니라 이 서버·이 모델의 실제 응답으로 정해야
한다. 켰을 때 (1) 요청이 거부되지 않는지 (2) 출력이 실제로 JSON만 남는지 (3) 지연이
얼마나 늘어나는지를 본다.

    uv run python scripts/probe_json_format.py [모델명 ...]
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from typing import Any

import httpx

BASE_URL = "http://localhost:11434"

# 앞뒤로 말을 붙이기 쉬운 지시. JSON만 뱉으라고 강하게 못 박지 않는다 —
# response_format이 없을 때 무엇이 새는지를 보려는 것이므로.
PROMPT = (
    "사용자 요청: 매출 높은 순으로 정렬해줘\n"
    "다음 형식의 JSON으로 답하세요: "
    '{"action": "<액션>", "params": {}, "reason": "<한 줄 이유>"}'
)


async def call(model: str, *, json_mode: bool) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": PROMPT}],
        "stream": False,
        "temperature": 0,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    started = time.perf_counter()
    async with httpx.AsyncClient(timeout=180.0) as client:
        resp = await client.post(f"{BASE_URL}/v1/chat/completions", json=payload)
        elapsed = round(time.perf_counter() - started, 2)
        if resp.status_code != 200:
            return {"ok": False, "status": resp.status_code, "body": resp.text[:400], "s": elapsed}
        data = resp.json()

    content = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    try:
        json.loads(content)
        pure = True
    except ValueError:
        pure = False
    return {
        "ok": True,
        "s": elapsed,
        # 응답 전체가 그대로 JSON인가. 앞뒤에 한 글자라도 붙으면 False.
        "pure_json": pure,
        "has_think": "<think" in content.lower(),
        "chars": len(content),
        "head": content[:220].replace("\n", "\\n"),
    }


async def main() -> None:
    models = sys.argv[1:] or ["ax7bplanner-v5r:latest", "qwen3:4b"]
    for model in models:
        print(f"\n=== {model} ===")
        for json_mode in (False, True):
            label = "response_format=json_object" if json_mode else "(없음)"
            try:
                result = await call(model, json_mode=json_mode)
            except Exception as exc:  # noqa: BLE001 - 프로브라 원인만 찍고 계속
                print(f"  {label:28} 예외: {type(exc).__name__}: {exc}")
                continue
            print(f"  {label:28} {result}")


if __name__ == "__main__":
    asyncio.run(main())
