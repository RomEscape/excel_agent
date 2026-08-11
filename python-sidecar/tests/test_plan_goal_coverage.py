"""사용자가 말한 목표가 계획에서 통째로 빠지는 경우를 막는 규칙들.

이 테스트가 지키는 실패는 "터진 것"이 아니라 **성공했다고 답한 것**이다.
진단 배터리에서 3회 반복 3회 모두 재현됐다(logs/diagnostics/0811-165423-oracle-baseline).

    사용자: "지역별 금액 막대 차트 만들어줘"
    모델:   pivot_table 하나, output_sheet="차트"
    검증기:  피벗의 사후조건만 본다 → 통과
    사용자: "완료했습니다"를 듣고, 시트 이름만 '차트'인 표를 본다

계획이 요청과 다른데 그 계획대로 정확히 실행되면 실행기도 검증기도 잡지 못한다.
둘 다 계획을 기준으로 채점하기 때문이다. 그래서 원문과 계획을 직접 맞대야 한다.
"""

from __future__ import annotations

import pytest

from office_claw_sidecar.routers.excel_live import (
    _chart_kind_from_message,
    _chart_step_from_message,
)
from office_claw_sidecar.services.excel_live_executor import normalize_plan_steps
from office_claw_sidecar.services.excel_live_plan_validator import (
    ValidationContext,
    validate_plan,
)


def _validate(step: dict, *, message: str = "테스트 명령"):
    """플래너가 낸 원시 단계를 실제 경로와 같은 순서로 검증한다."""
    return validate_plan(
        normalize_plan_steps([step]),
        context=ValidationContext(message=message, sheet_name="매출"),
    )


class TestChartCompletion:
    """차트를 요구했는데 계획에 없을 때 채워 넣는 규칙."""

    @pytest.mark.parametrize(
        ("message", "kind"),
        [
            ("지역별 금액 막대 차트 만들어줘", "bar"),
            ("월별 추이 선 그래프로 보여줘", "line"),
            ("비율을 원형 차트로", "pie"),
            ("판매량 bar chart 그려줘", "bar"),
        ],
    )
    def test_reads_the_kind_the_user_named(self, message, kind):
        assert _chart_kind_from_message(message) == kind
        step = _chart_step_from_message(message)
        assert step is not None
        assert step["action"] == "excel_live.create_chart"
        assert step["params"]["chart_type"] == kind

    def test_invents_nothing_when_the_kind_is_unsaid(self):
        """종류를 안 말했으면 만들지 않는다.

        선·막대·원형은 결과물의 성격이 서로 다르다. 기본값으로 밀면 조용한 오답이
        하나 늘 뿐이다. 이 경우는 라우터가 되묻는 쪽으로 넘긴다.
        """
        assert _chart_step_from_message("차트 만들어줘") is None
        assert _chart_step_from_message("그래프로 보여줘") is None

    def test_ignores_sentences_that_are_not_about_charts(self):
        assert _chart_step_from_message("금액 열 기준 내림차순으로 정렬해줘") is None

    def test_the_filled_step_survives_validation(self):
        """채워 넣은 단계가 검증기를 통과해야 실제로 실행된다."""
        message = "지역별 금액 막대 차트 만들어줘"
        plan = _validate(_chart_step_from_message(message), message=message)
        assert [s.action for s in plan] == ["excel_live.create_chart"]
        assert plan[0].params["chart_type"] == "bar"


class TestHighlightThreshold:
    """기준값을 지어내지 않는다."""

    def test_refuses_to_highlight_without_a_threshold(self):
        """기준값이 없으면 계획을 반려한다.

        예전에는 0을 채워 넣었다. 그러면 ">= 0"이 되어 전 행이 칠해지는데,
        실행기는 "칠한 셀 1개 이상"이라 성공을 보고하고 사후조건 검증도 통과한다.
        사용자만 통째로 노랗게 칠해진 시트를 본다.
        """
        step = {
            "action": "excel_live.highlight_by_condition",
            "params": {"target_range": "C2:C9"},
            "reason": "기준값 없음",
        }
        with pytest.raises(ValueError, match="기준값"):
            _validate(step, message="금액 큰 건 노란색으로 표시해줘")

    @pytest.mark.parametrize(
        ("params", "expected"),
        [
            ({"target_range": "C2:C9", "threshold": 300}, 300.0),
            ({"target_range": "C2:C9", "value": 150}, 150.0),
            ({"target_range": "C2:C9", "operator": ">=1100"}, 1100.0),
            ({"target_range": "C2:C9", "operator": {"op": ">=", "value": 42}}, 42.0),
        ],
    )
    def test_still_recovers_a_threshold_that_arrived_somewhere_else(self, params, expected):
        """플래머가 기준값을 다른 자리에 넣는 경우는 계속 회수한다.

        반려는 '어디에도 없을 때'만이어야 한다. 회수 경로까지 같이 막으면
        멀쩡한 요청이 되묻기로 떨어진다.
        """
        step = {
            "action": "excel_live.highlight_by_condition",
            "params": params,
            "reason": "기준값 회수",
        }
        plan = _validate(step, message="기준 넘는 건 표시해줘")
        assert plan[0].params["threshold"] == expected


class TestSortLabelBoundary:
    """정렬 두 액션이 서로 구분되는 설명을 갖고 있는가.

    v5r 모델이 승격 게이트에서 떨어진 직접 원인이 이 둘의 라벨 충돌이었다.
    학습셋에서 37건 대 36건으로 갈라져 있는데 문장꼴이 거의 같아, 모델에게는
    구분 근거가 없었다. 설명과 예시가 다시 겹치면 같은 일이 반복된다.
    """

    def test_the_two_sort_tools_do_not_share_example_triggers(self):
        from office_claw_sidecar.services.tool_registry import get_tool

        by_range = get_tool("excel_live.sort_range")
        by_header = get_tool("excel_live.sort_rows")
        shared = set(by_range.example_triggers) & set(by_header.example_triggers)
        assert not shared, f"두 정렬 액션이 같은 예시 문구를 쓴다: {shared}"

    def test_display_names_have_no_duplicate_keys(self):
        """중복 키는 뒤의 값이 조용히 이긴다. 표시명이 바뀌어도 아무도 모른다."""
        from pathlib import Path

        import office_claw_sidecar.services.tool_registry as registry

        source = Path(registry.__file__).read_text(encoding="utf-8")
        block = source.split("TOOL_DISPLAY_NAMES: dict[str, str] = {", 1)[1].split("\n}", 1)[0]
        keys = [
            line.split(":", 1)[0].strip().strip('"')
            for line in block.splitlines()
            if line.strip().startswith('"')
        ]
        duplicates = {key for key in keys if keys.count(key) > 1}
        assert not duplicates, f"TOOL_DISPLAY_NAMES에 중복 키: {duplicates}"
