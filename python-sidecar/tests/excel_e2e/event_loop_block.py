"""Excel 작업이 도는 동안 사이드카가 다른 요청에 답하는지 잰다.

증상은 "명령을 실행하면 UI가 사이드카가 죽은 것처럼 본다"였다. 원인 후보는
`_run_in_excel_queue()`가 `async def` 핸들러 안에서 **동기로** 호출된다는 것이다.
그렇다면 COM 호출이 도는 내내 이벤트 루프가 통째로 붙잡히고, `/health` 폴링이 답을
못 받는다.

여기서는 Excel이 없어도 재현되도록 실행 함수를 `time.sleep`으로 갈아 끼운다. COM이
느린 것 자체는 문제가 아니다 — **그 동안 다른 요청이 막히는 것**이 문제다. 그래서
재는 값은 명령의 소요 시간이 아니라 `/health`의 응답 지연이다.

판정 기준은 하나다. 작업 시간을 늘렸을 때 `/health` 지연이 같이 늘면 이벤트 루프가
막힌 것이고, 늘지 않으면 막히지 않은 것이다.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

import httpx


@dataclass
class BlockingMeasurement:
    """작업 하나를 돌리는 동안 관측한 `/health` 응답 지연."""

    work_seconds: float
    health_latencies_ms: list[float] = field(default_factory=list)
    idle_latencies_ms: list[float] = field(default_factory=list)
    idle_errors: list[str] = field(default_factory=list)
    command_ms: float = 0.0
    health_ok: int = 0
    health_failed: int = 0

    @property
    def worst_health_ms(self) -> float:
        return max(self.health_latencies_ms, default=0.0)

    @property
    def idle_health_ms(self) -> float:
        """아무 작업도 없을 때의 `/health` 지연.

        `/health`는 Ollama에 태그 목록을 물어보므로 원래도 공짜가 아니다. 이 값을
        빼지 않으면 그 왕복 비용을 "막혔다"로 잘못 읽는다.
        """
        return max(self.idle_latencies_ms, default=0.0)

    @property
    def excess_ms(self) -> float:
        """유휴 대비 더 걸린 시간. 이벤트 루프가 붙잡힌 몫이다."""
        return max(0.0, self.worst_health_ms - self.idle_health_ms)

    @property
    def blocked(self) -> bool:
        """유휴 대비 초과분이 작업 시간의 절반을 넘으면 막힌 것으로 본다.

        완전히 막히면 초과분이 작업 시간에 거의 같아지고, 안 막히면 0 근처에
        머문다. 그 사이가 넓어서 경계값이 예민하지 않다.
        """
        return self.excess_ms >= self.work_seconds * 1000 * 0.5


async def measure(
    app: Any,
    *,
    work_seconds: float,
    command_path: str,
    command_payload: dict,
    poll_interval: float = 0.05,
    idle_samples: int = 3,
) -> BlockingMeasurement:
    """명령을 한 번 던지고, 그 동안 `/health`를 계속 두드려 지연을 모은다."""
    result = BlockingMeasurement(work_seconds=work_seconds)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://sidecar") as client:
        # 먼저 유휴 상태에서 몇 번 재 둔다. 비교 기준이 없으면 `/health` 자체
        # 비용과 이벤트 루프가 막힌 시간을 구분할 수 없다.
        for _ in range(idle_samples):
            started = time.perf_counter()
            try:
                await client.get("/health", timeout=10)
            except Exception as exc:  # noqa: BLE001 - 기준선 수집이라 실패해도 계속
                result.idle_errors.append(f"{type(exc).__name__}: {exc}")
            result.idle_latencies_ms.append((time.perf_counter() - started) * 1000)

        stop = asyncio.Event()

        async def poll_health() -> None:
            # 명령이 끝날 때까지 계속 두드린다. 한 번이라도 오래 걸리면 그게 증거다.
            while not stop.is_set():
                started = time.perf_counter()
                try:
                    response = await client.get("/health", timeout=work_seconds * 3 + 5)
                    elapsed_ms = (time.perf_counter() - started) * 1000
                    result.health_latencies_ms.append(elapsed_ms)
                    if response.status_code == 200:
                        result.health_ok += 1
                    else:
                        result.health_failed += 1
                except Exception:  # noqa: BLE001 - 실패도 관측 대상이다
                    result.health_latencies_ms.append((time.perf_counter() - started) * 1000)
                    result.health_failed += 1
                try:
                    await asyncio.wait_for(stop.wait(), timeout=poll_interval)
                except TimeoutError:
                    pass

        poller = asyncio.create_task(poll_health())
        # 폴러가 먼저 한 바퀴 돌아 붙게 둔다. 안 그러면 명령이 루프를 잡은 뒤에
        # 폴러가 시작돼 첫 지연을 놓친다.
        await asyncio.sleep(poll_interval)

        started = time.perf_counter()
        try:
            await client.post(command_path, json=command_payload, timeout=work_seconds * 3 + 10)
        finally:
            result.command_ms = (time.perf_counter() - started) * 1000
            stop.set()
            await poller

    return result
