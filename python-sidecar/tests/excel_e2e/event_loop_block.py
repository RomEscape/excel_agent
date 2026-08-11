"""Excel 작업이 도는 동안 사이드카가 다른 요청에 답하는지 잰다.

증상은 "명령을 실행하면 UI가 사이드카가 죽은 것처럼 본다"였다. 원인 후보는
`_run_in_excel_queue()`가 `async def` 핸들러 안에서 **동기로** 호출된다는 것이다.
그렇다면 COM 호출이 도는 내내 이벤트 루프가 통째로 붙잡히고, UI의 폴링이 답을
못 받는다.

여기서는 Excel 없이 재현한다. 실행부를 `time.sleep`으로 갈아 끼운다. COM이 느린 것
자체는 문제가 아니다 — **그 동안 다른 요청이 막히는 것**이 문제다.

## 무엇을 재는가 — 지연이 아니라 '끊긴 구간'

처음에는 탐침 요청의 최대 지연을 쟀다. 틀린 지표였다. 이벤트 루프가 막히면 이미
날아간 요청만 늦게 돌아오는 게 아니라, **폴러 자체가 실행되지 못해 요청이 출발조차
못 한다.** 그래서 1초를 통째로 막아 놓아도 남는 샘플은 전부 1ms짜리였고, 측정은
"안 막혔다"고 답했다. 막힌 코드를 일부러 되살려 놓은 대조군이 통과해 버려서
드러났다.

지금은 탐침 응답이 **연속으로 끊긴 최대 구간**을 잰다. 루프가 살아 있으면 폴링
간격마다 응답이 돌아오므로 간격은 50ms 근처에 머문다. 1초 막히면 그 1초 동안 아무
응답도 없으므로 간격이 그대로 1초가 된다. 신호와 잡음이 스무 배 벌어진다.

## 탐침으로 무엇을 두드리는가

`/health`는 안에서 Ollama에 모델 목록을 물어본다. 이 왕복이 이 기계에서 470ms라
탐침 비용이 재려는 신호와 자릿수가 같아졌다. 탐침은 "이벤트 루프가 돌고 있는가"만
답하면 되므로, 라우트가 없는 경로를 두드린다. 404도 루프가 살아 있어야 돌아오고
바깥 의존성이 없다.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from itertools import pairwise
from typing import Any

import httpx


@dataclass
class BlockingMeasurement:
    """작업 하나를 돌리는 동안 관측한 탐침 응답 기록."""

    work_seconds: float
    # 명령이 도는 동안 탐침 응답이 돌아온 시각(측정 시작 기준 초).
    probe_times: list[float] = field(default_factory=list)
    probe_latencies_ms: list[float] = field(default_factory=list)
    idle_latencies_ms: list[float] = field(default_factory=list)
    idle_errors: list[str] = field(default_factory=list)
    command_started: float = 0.0
    command_ended: float = 0.0
    command_ms: float = 0.0
    answered: int = 0
    unanswered: int = 0

    @property
    def idle_gap_ms(self) -> float:
        """아무 작업도 없을 때 탐침 하나가 걸리는 시간."""
        return max(self.idle_latencies_ms, default=0.0)

    @property
    def longest_silence_ms(self) -> float:
        """명령이 도는 동안 탐침 응답이 하나도 없던 최대 구간.

        명령의 시작과 끝을 경계로 넣는다. 그래야 "그 사이에 응답이 한 번도 없었다"는
        경우도 침묵 구간으로 잡힌다 — 완전히 막히면 정확히 그렇게 된다.
        """
        if self.command_ended <= self.command_started:
            return 0.0
        marks = [self.command_started]
        marks += [t for t in self.probe_times if self.command_started <= t <= self.command_ended]
        marks.append(self.command_ended)
        return max((b - a) * 1000 for a, b in pairwise(marks))

    @property
    def blocked(self) -> bool:
        """침묵 구간이 작업 시간의 절반을 넘으면 막힌 것으로 본다.

        완전히 막히면 침묵이 작업 시간에 거의 같아지고, 안 막히면 폴링 간격 근처에
        머문다. 그 사이가 넓어서 경계값이 예민하지 않다.
        """
        return self.longest_silence_ms >= self.work_seconds * 1000 * 0.5


async def measure(
    app: Any,
    *,
    work_seconds: float,
    command_path: str,
    command_payload: dict,
    poll_interval: float = 0.05,
    idle_samples: int = 3,
    probe_path: str = "/__event_loop_probe__",
) -> BlockingMeasurement:
    """명령을 한 번 던지고, 그 동안 탐침 경로를 계속 두드린다."""
    result = BlockingMeasurement(work_seconds=work_seconds)
    transport = httpx.ASGITransport(app=app)
    origin = time.perf_counter()

    async with httpx.AsyncClient(transport=transport, base_url="http://sidecar") as client:
        # 먼저 유휴 상태에서 몇 번 재 둔다. 탐침 자체 비용을 알아야 침묵 구간이
        # 의미 있는 크기인지 판단할 수 있다.
        for _ in range(idle_samples):
            started = time.perf_counter()
            try:
                await client.get(probe_path, timeout=10)
            except Exception as exc:  # noqa: BLE001 - 기준선 수집이라 실패해도 계속
                result.idle_errors.append(f"{type(exc).__name__}: {exc}")
            result.idle_latencies_ms.append((time.perf_counter() - started) * 1000)

        stop = asyncio.Event()

        async def poll() -> None:
            # 응답 내용은 보지 않는다 — 돌아왔다는 사실만이 루프가 살아 있다는 증거다.
            while not stop.is_set():
                started = time.perf_counter()
                try:
                    await client.get(probe_path, timeout=work_seconds * 3 + 5)
                    result.answered += 1
                except Exception:  # noqa: BLE001 - 실패도 관측 대상이다
                    result.unanswered += 1
                now = time.perf_counter()
                result.probe_times.append(now - origin)
                result.probe_latencies_ms.append((now - started) * 1000)
                try:
                    await asyncio.wait_for(stop.wait(), timeout=poll_interval)
                except TimeoutError:
                    pass

        poller = asyncio.create_task(poll())
        # 폴러가 먼저 한 바퀴 돌아 붙게 둔다. 안 그러면 명령이 루프를 잡은 뒤에
        # 폴러가 시작돼 첫 구간을 놓친다.
        await asyncio.sleep(poll_interval)

        started = time.perf_counter()
        result.command_started = started - origin
        try:
            await client.post(command_path, json=command_payload, timeout=work_seconds * 3 + 10)
        finally:
            ended = time.perf_counter()
            result.command_ended = ended - origin
            result.command_ms = (ended - started) * 1000
            stop.set()
            await poller

    return result
