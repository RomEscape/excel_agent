"""실행기 결함 셋 (Task 8).

8-1 `ExecutionResult.ok`가 `bool(self.steps)`였다 — 전 단계가 실패해도 참이다.
8-2 재시도가 **같은 파라미터로** 다시 던졌다. 결정적 실패는 두 번 똑같이 실패하고
    지연만 두 배가 된다. 실측: `logs/diagnostics/*.jsonl` 410단계 중 재시도 3회,
    성공 0회. 그래서 못 고치는 실패는 아예 재시도하지 않는다.
8-3 실패 사유가 응답까지 도달하지 않았다.
"""

from __future__ import annotations

import pytest

from office_claw_sidecar.routers.excel_live import _step_failure_line
from office_claw_sidecar.services.excel_live_executor import (
    ExecutionResult,
    PlanStep,
    StepExecutionResult,
    execute_plan,
)
from office_claw_sidecar.services.excel_step_repair import (
    RepairContext,
    normalize_range_text,
    repair_step,
)


def _step(**kwargs) -> StepExecutionResult:
    base = {
        "index": 1,
        "action": "excel_live.fill_range",
        "params": {},
        "reason": "",
        "result": {},
        "retried": False,
        "verified": True,
        "error": None,
    }
    base.update(kwargs)
    return StepExecutionResult(**base)


class TestOkProperty:
    def test_no_steps_is_not_ok(self):
        assert ExecutionResult(steps=[]).ok is False

    def test_all_verified_is_ok(self):
        assert ExecutionResult(steps=[_step(), _step(index=2)]).ok is True

    def test_a_failed_step_makes_the_whole_thing_not_ok(self):
        # 예전에는 steps가 비어 있지만 않으면 참이었다.
        assert ExecutionResult(steps=[_step(), _step(index=2, verified=False)]).ok is False

    def test_an_errored_step_makes_the_whole_thing_not_ok(self):
        assert ExecutionResult(steps=[_step(error="COM 오류")]).ok is False


class TestRetryUsesRepairedParams:
    def test_the_second_attempt_runs_with_the_repaired_params(self):
        seen: list[dict] = []

        def execute(action, params):
            seen.append(dict(params))
            if params.get("target_range") != "A1:C10":
                raise RuntimeError("잘못된 범위")
            return {"changed_cells": 30}

        out = execute_plan(
            steps=[PlanStep(action="excel_live.fill_range", params={"target_range": " a1 : c10 "})],
            execute_action=execute,
            verify_step=lambda *_: True,
            repair=lambda step, _f: repair_step(step, _f, RepairContext()),
        )

        assert [s["target_range"] for s in seen] == [" a1 : c10 ", "A1:C10"]
        assert out.ok is True
        assert out.steps[0].retried is True
        # 실제로 실행된 파라미터가 남고, 원본은 따로 보존된다.
        assert out.steps[0].params["target_range"] == "A1:C10"
        assert out.steps[0].original_params["target_range"] == " a1 : c10 "

    def test_nothing_to_repair_means_no_second_attempt(self):
        calls = []

        def execute(action, params):
            calls.append(params)
            raise RuntimeError("결정적 실패")

        out = execute_plan(
            steps=[PlanStep(action="excel_live.fill_range", params={"target_range": "A1:C10"})],
            execute_action=execute,
            verify_step=lambda *_: True,
            repair=lambda step, _f: repair_step(step, _f, RepairContext()),
        )

        assert len(calls) == 1, "고칠 게 없는데 같은 파라미터로 또 던졌다"
        assert out.ok is False
        assert out.steps[0].retried is False

    def test_without_a_repair_callback_the_old_retry_survives(self):
        # COM의 일시적 실패는 그대로 다시 시도하는 게 맞다. 이 경로를 없애지 않았다.
        calls = []

        def execute(action, params):
            calls.append(params)
            if len(calls) == 1:
                raise RuntimeError("일시적 실패")
            return {"changed_cells": 1}

        out = execute_plan(
            steps=[PlanStep(action="excel_live.fill_range", params={"target_range": "A1"})],
            execute_action=execute,
            verify_step=lambda *_: True,
        )
        assert len(calls) == 2
        assert out.ok is True

    def test_a_step_that_recovers_is_not_reported_as_errored(self):
        # 1회차 예외 뒤 2회차가 성공하면 error가 남아 있으면 안 된다.
        calls = []

        def execute(action, params):
            calls.append(params)
            if len(calls) == 1:
                raise RuntimeError("일시적 실패")
            return {"changed_cells": 1}

        out = execute_plan(
            steps=[PlanStep(action="excel_live.fill_range", params={"target_range": "A1"})],
            execute_action=execute,
            verify_step=lambda *_: True,
        )
        assert out.steps[0].error is None
        assert out.steps[0].verified is True

    def test_repair_returning_none_stops_a_verify_failure_too(self):
        attempts = []

        out = execute_plan(
            steps=[PlanStep(action="excel_live.fill_range", params={"target_range": "A1"})],
            execute_action=lambda a, p: attempts.append(p) or {"changed_cells": 0},
            verify_step=lambda *_: (False, "no_cells_changed:칠한 셀이 없습니다"),
            repair=lambda *_: None,
        )
        assert len(attempts) == 1
        assert out.steps[0].verify_detail == "no_cells_changed:칠한 셀이 없습니다"


class TestRangeNormalization:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("a1:c10", "A1:C10"),
            (" A1 : C10 ", "A1:C10"),
            ("A1：C10", "A1:C10"),  # 한글 IME의 전각 콜론
            ("$A$1:$C$10", "A1:C10"),
            ("d:d", "D:D"),
            ("A1", "A1"),
        ],
    )
    def test_it_normalizes(self, raw, expected):
        assert normalize_range_text(raw) == expected

    def test_it_leaves_non_a1_text_alone(self):
        # 이름 있는 범위나 표 이름을 망가뜨리면 안 된다.
        assert normalize_range_text("매출표") == "매출표"


class TestSheetRepair:
    def test_a_case_mismatch_is_snapped_to_the_real_name(self):
        step = PlanStep(action="excel_live.fill_range", params={"sheet_name": "sheet1"})
        out = repair_step(step, "없는 시트", RepairContext(sheet_names=("Sheet1", "요약")))
        assert out is not None
        assert out.params["sheet_name"] == "Sheet1"

    def test_a_missing_sheet_falls_back_to_the_active_one(self):
        step = PlanStep(action="excel_live.fill_range", params={"sheet_name": "없는시트"})
        out = repair_step(
            step, "없는 시트", RepairContext(sheet_names=("Sheet1",), active_sheet="Sheet1")
        )
        assert out is not None
        assert out.params["sheet_name"] == "Sheet1"

    def test_an_output_sheet_may_legitimately_not_exist_yet(self):
        # 새로 만들 출력 시트까지 활성 시트로 바꾸면 결과를 엉뚱한 데 쓴다.
        step = PlanStep(action="excel_live.pivot_table", params={"output_sheet": "피벗결과"})
        assert repair_step(step, "x", RepairContext(sheet_names=("Sheet1",), active_sheet="Sheet1")) is None


class TestActiveSelection:
    def test_it_falls_back_to_the_context_range(self):
        step = PlanStep(
            action="excel_live.fill_range", params={"target_range": "__ACTIVE_SELECTION__"}
        )
        out = repair_step(step, "x", RepairContext(context_range="B2:D5"))
        assert out is not None
        assert out.params["target_range"] == "B2:D5"

    def test_then_the_last_touched_range(self):
        step = PlanStep(
            action="excel_live.fill_range", params={"target_range": "__ACTIVE_SELECTION__"}
        )
        out = repair_step(step, "x", RepairContext(active_cell="F7"))
        assert out is not None
        assert out.params["target_range"] == "F7"

    def test_with_nothing_to_resolve_to_there_is_no_repair(self):
        step = PlanStep(
            action="excel_live.fill_range", params={"target_range": "__ACTIVE_SELECTION__"}
        )
        assert repair_step(step, "x", RepairContext()) is None

    def test_table_scoped_actions_keep_the_sentinel(self):
        """표 전체가 대상인 액션은 직전 턴이 남긴 좁은 범위를 물려받으면 안 된다.

        2026-08-16 실측: 차트를 G1:G4로 만든 다음 "지역별 매출을 집계해서 새 시트로
        만들어줘"를 하면, 보정기가 `__ACTIVE_SELECTION__`을 그 G1:G4로 바꿔 놓아
        피벗이 Product 열 4칸을 sum하려다 죽었다. 센티널을 남겨 둬야 라우터가
        시트의 사용 영역으로 편다.
        """
        step = PlanStep(
            action="excel_live.pivot_table",
            params={
                "source_range": "__ACTIVE_SELECTION__",
                "row_field": "Region",
                "value_field": "Sales",
            },
        )
        out = repair_step(step, "x", RepairContext(active_cell="G1:G4"))
        assert out is None, "피벗의 센티널을 직전 범위로 바꿔치기했다"

    def test_a_chart_keeps_the_sentinel_too(self):
        step = PlanStep(
            action="excel_live.create_chart", params={"source_range": "__ACTIVE_SELECTION__"}
        )
        assert repair_step(step, "x", RepairContext(context_range="B2:B5")) is None

    def test_cell_scoped_actions_still_resolve_the_sentinel(self):
        """칸 단위 서식은 직전 범위를 물려받는 게 맞다 — 그 경로를 없애지 않았다."""
        step = PlanStep(
            action="excel_live.fill_range", params={"target_range": "__ACTIVE_SELECTION__"}
        )
        out = repair_step(step, "x", RepairContext(context_range="B2:D5"))
        assert out is not None
        assert out.params["target_range"] == "B2:D5"


class TestFailureLineReachesTheUser:
    def test_it_names_the_action_the_range_and_the_cause(self):
        line = _step_failure_line(
            _step(
                action="excel_live.fill_range",
                params={"target_range": "B2:D5"},
                verified=False,
                verify_detail="no_cells_changed:서식이 적용된 셀이 없습니다",
            )
        )
        assert "배경색" in line
        assert "B2:D5" in line
        assert "서식이 적용된 셀이 없습니다" in line

    def test_an_exception_message_survives(self):
        line = _step_failure_line(_step(verified=False, error="시트를 찾을 수 없습니다"))
        assert "시트를 찾을 수 없습니다" in line

    def test_it_never_produces_a_bare_failure_message(self):
        line = _step_failure_line(_step(action="excel_live.sort_range", verified=False))
        assert line != "작업에 실패했습니다."
        assert "정렬" in line
