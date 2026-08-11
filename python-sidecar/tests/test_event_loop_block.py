"""Excel 작업 중에도 사이드카가 다른 요청에 답해야 한다.

`/excel-live/command`는 `async def`인데 Excel 실행부를 동기로 부른다. COM 호출은
초 단위로 걸리므로, 그 동안 이벤트 루프가 붙잡히면 `/health` 폴링이 답을 못 받고
UI는 사이드카가 죽은 것으로 본다.

여기서는 Excel 없이 재현한다. 실행부를 `time.sleep`으로 갈아 끼우고, 작업 시간을
늘렸을 때 `/health` 지연이 따라 늘어나는지를 본다. 따라 늘면 루프가 막힌 것이다.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from office_claw_sidecar.main import app
from office_claw_sidecar.routers import excel_live as router
from tests.excel_e2e import event_loop_block

# 짧으면 폴링 간격에 묻히고, 길면 테스트가 느려진다. COM 한 번이 이 정도는 걸린다.
WORK_SECONDS = 1.0

COMMAND_PAYLOAD = {
    "message": "A1에 1 입력해줘",
    "workbook_id": "measure.xlsx",
    "sheet_name": "Sheet1",
    "approve": True,
}


@pytest.fixture
def slow_excel(monkeypatch):
    """액션 실행을 '느리지만 성공하는' 동기 작업으로 바꾼다.

    큐 함수 자체가 아니라 **큐가 돌리는 일**을 느리게 만든다. 큐를 갈아끼우면
    재려던 배관을 테스트에서 다시 짜게 되고, 그러면 실제 코드가 아니라 스텁을
    재게 된다. 중요한 건 `time.sleep`이다 — `asyncio.sleep`으로 바꾸면 COM이
    스레드를 붙잡는 상황 자체가 사라진다.
    """
    calls: list[str] = []

    def _slow_action(*, action, params, workbook_id, sheet_name):
        calls.append(action)
        time.sleep(WORK_SECONDS)
        return {"ok": True, "written_cells": 1, "target_range": "A1"}

    monkeypatch.setattr(router, "_execute_action", _slow_action)
    return calls


def test_health_answers_while_excel_work_runs(slow_excel, monkeypatch):
    """Excel 작업이 도는 동안에도 `/health`가 제때 답한다."""

    async def _fake_plan(message, llm_service=None, context=None, **kwargs):
        return {
            "action_plan": [
                {
                    "action": "excel_live.write_range",
                    "params": {"target_range": "A1", "values": [[1]]},
                    "reason": "측정",
                }
            ],
            "intent": "edit",
        }

    monkeypatch.setattr(router, "parse_excel_live_command", _fake_plan)

    measurement = asyncio.run(
        event_loop_block.measure(
            app,
            work_seconds=WORK_SECONDS,
            command_path="/excel-live/command",
            command_payload=COMMAND_PAYLOAD,
        )
    )

    assert measurement.health_latencies_ms, "폴링이 한 번도 돌지 않았다 — 측정 자체가 실패"
    assert not measurement.blocked, (
        f"Excel 작업 {WORK_SECONDS}초 동안 /health 최대 지연 "
        f"{measurement.worst_health_ms:.0f}ms, 유휴 기준 {measurement.idle_health_ms:.0f}ms "
        f"— 초과 {measurement.excess_ms:.0f}ms. 이벤트 루프가 막혀 있다."
    )


def test_the_measurement_catches_a_synchronous_call(slow_excel, monkeypatch):
    """위 테스트가 회귀를 실제로 잡을 수 있는지 확인한다.

    통과하는 테스트는 배선이 잘못돼 아무것도 재지 않을 때도 통과한다. 그래서 옛
    동작(async 핸들러에서 큐를 동기로 호출)을 일부러 되살려 놓고, 측정이 그걸
    '막힘'으로 판정하는지를 본다. 여기가 통과해야 위 테스트가 의미를 갖는다.
    """

    async def _blocking_call(task_name, fn):
        # 고치기 전 코드가 하던 그대로 — await 없이 그 자리에서 돌린다.
        return router._run_in_excel_queue(task_name, fn)

    async def _fake_plan(message, llm_service=None, context=None, **kwargs):
        return {
            "action_plan": [
                {
                    "action": "excel_live.write_range",
                    "params": {"target_range": "A1", "values": [[1]]},
                    "reason": "측정",
                }
            ],
            "intent": "edit",
        }

    monkeypatch.setattr(router, "parse_excel_live_command", _fake_plan)
    monkeypatch.setattr(router, "_run_in_excel_queue_async", _blocking_call)

    measurement = asyncio.run(
        event_loop_block.measure(
            app,
            work_seconds=WORK_SECONDS,
            command_path="/excel-live/command",
            command_payload=COMMAND_PAYLOAD,
        )
    )

    assert measurement.blocked, (
        "동기 호출을 되살렸는데도 막힘으로 판정되지 않았다 — 측정이 무뎌졌다. "
        f"초과 {measurement.excess_ms:.0f}ms"
    )
