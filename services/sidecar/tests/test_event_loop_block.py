"""Excel 작업 중에도 사이드카가 다른 요청에 답해야 한다.

`/excel-live/command`는 `async def`인데 Excel 실행부를 동기로 부르던 때가 있었다.
COM 호출은 초 단위로 걸리므로, 그 동안 이벤트 루프가 붙잡히면 UI의 폴링이 답을 못
받고 사이드카가 죽은 것으로 보인다.

여기서는 Excel 없이 재현한다. 실행부를 `time.sleep`으로 갈아 끼우고, 그 동안 탐침
요청이 끊기는지 본다.

## 이 파일에 테스트가 둘인 이유

통과하는 테스트는 배선이 잘못돼 **아무것도 재지 않을 때도** 통과한다. 실제로 그런
일이 있었다 — 통합문서를 격리하고 나니 느린 액션이 아예 호출되지 않았는데도 첫
테스트는 초록색이었다. 그래서 옛 동작(async 핸들러에서 큐를 동기로 호출)을 일부러
되살린 대조군을 같이 둔다. 대조군이 '막힘'을 잡아내야 첫 테스트의 초록색이 의미를
갖는다.

같은 이유로 두 테스트 모두 "느린 일이 실제로 돌았는가"를 먼저 단언한다.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

import pytest

from office_claw_sidecar.main import app
from office_claw_sidecar.routers import excel_live as router
from tests.excel_e2e import command_battery, event_loop_block

# 짧으면 폴링 간격에 묻히고, 길면 테스트가 느려진다. COM 한 번이 이 정도는 걸린다.
WORK_SECONDS = 1.0


@dataclass
class SlowExcel:
    payload: dict[str, Any]
    calls: list[str] = field(default_factory=list)


@pytest.fixture
def slow_excel(tmp_path, monkeypatch):
    """액션 실행만 '느리지만 성공하는' 동기 작업으로 바꾸고, 나머지는 빠르게 둔다.

    큐 함수 자체가 아니라 **큐가 돌리는 일**을 느리게 만든다. 큐를 갈아끼우면
    재려던 배관을 테스트에서 다시 짜게 되고, 그러면 실제 코드가 아니라 스텁을
    재게 된다. 중요한 건 `time.sleep`이다 — `asyncio.sleep`으로 바꾸면 COM이
    스레드를 붙잡는 상황 자체가 사라진다.

    통합문서는 파일 엔진으로 격리한다. 없는 파일을 기본 엔진으로 찾게 두면 Excel을
    붙잡으려는 시도가 수백 ms씩 걸려서 재려던 신호와 섞인다. 이 테스트가 답해야
    하는 질문은 "느린 Excel 작업이 루프를 막는가" 하나뿐이므로, 그 외에 느린 것이
    있으면 안 된다.
    """
    from openpyxl import Workbook

    monkeypatch.setenv("EXCEL_LIVE_ENGINE", "file")
    command_battery._isolate(monkeypatch, tmp_path)
    workbook = Workbook()
    workbook.active.title = "Sheet1"
    path = tmp_path / "measure.xlsx"
    workbook.save(path)

    slow = SlowExcel(
        payload={
            "message": "A1에 1 입력해줘",
            "workbook_id": str(path),
            "sheet_name": "Sheet1",
            "approve": True,
        }
    )

    def _slow_action(*, action, params, workbook_id, sheet_name):
        slow.calls.append(action)
        time.sleep(WORK_SECONDS)
        return {"ok": True, "written_cells": 1, "target_range": "A1"}

    async def _fake_plan(message, llm_service=None, context=None, **kwargs):
        return {
            "action_plan": [
                {
                    "action": "excel_live.write_range",
                    "params": {"target_range": "A1", "values_2d": [[1]]},
                    "reason": "측정",
                }
            ],
            "intent": "edit",
        }

    monkeypatch.setattr(router, "_execute_action", _slow_action)
    monkeypatch.setattr(router, "parse_excel_live_command", _fake_plan)
    return slow


def _measure(slow: SlowExcel) -> event_loop_block.BlockingMeasurement:
    measurement = asyncio.run(
        event_loop_block.measure(
            app,
            work_seconds=WORK_SECONDS,
            command_path="/excel-live/command",
            command_payload=slow.payload,
        )
    )
    # 여기서 막지 않으면, 느린 일이 한 번도 안 돈 측정을 "안 막혔다"로 읽는다.
    assert slow.calls, "느린 액션이 호출되지 않았다 — 이 측정은 아무것도 재지 않았다"
    assert measurement.command_ms >= WORK_SECONDS * 1000 * 0.8, (
        f"명령이 {measurement.command_ms:.0f}ms 만에 끝났다 — 느린 작업이 실제로 돌지 않았다"
    )
    assert measurement.probe_times, "폴링이 한 번도 돌지 않았다 — 측정 자체가 실패"
    return measurement


def test_health_answers_while_excel_work_runs(slow_excel):
    """Excel 작업이 도는 동안에도 다른 요청이 제때 답한다."""
    measurement = _measure(slow_excel)

    assert not measurement.blocked, (
        f"Excel 작업 {WORK_SECONDS}초 동안 탐침 응답이 "
        f"{measurement.longest_silence_ms:.0f}ms 끊겼다 "
        f"(유휴 시 탐침 {measurement.idle_gap_ms:.1f}ms). 이벤트 루프가 막혀 있다."
    )


def test_the_measurement_catches_a_synchronous_call(slow_excel, monkeypatch):
    """옛 동작을 되살리면 측정이 '막힘'으로 판정하는가."""

    async def _blocking_call(task_name, fn):
        # 고치기 전 코드가 하던 그대로 — await 없이 그 자리에서 돌린다.
        return router._run_in_excel_queue(task_name, fn)

    monkeypatch.setattr(router, "_run_in_excel_queue_async", _blocking_call)
    measurement = _measure(slow_excel)

    assert measurement.blocked, (
        "동기 호출을 되살렸는데도 막힘으로 판정되지 않았다 — 측정이 무뎌졌다. "
        f"최대 침묵 {measurement.longest_silence_ms:.0f}ms"
    )
