"""관측 모드 스위치의 계약.

가장 중요한 건 `test_off_leaves_the_prompt_byte_identical`이다. `build_planner_prompt`는
SFT 데이터 생성과 **같은 함수**라, 기본 경로의 프롬프트가 한 글자라도 달라지면 이미
학습된 모델이 본 적 없는 형식을 받는다. 실험용 스위치를 넣으면서 그걸 건드리면
실험 결과 자체를 믿을 수 없게 된다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar

from office_claw_sidecar.services import excel_observation as obs
from office_claw_sidecar.services.excel_planner_prompt import build_planner_prompt


@dataclass
class _Step:
    action: str
    result: dict[str, Any] = field(default_factory=dict)
    error: str = ""


@dataclass
class _Execution:
    last: _Step | None


def _read(values: list[list[Any]], address: str = "A1:D3") -> _Execution:
    return _Execution(
        _Step("excel_live.read_range", {"values": values, "address": address})
    )


class TestMode:
    def test_default_is_off(self, monkeypatch):
        monkeypatch.delenv(obs.ENV_VAR, raising=False)
        assert obs.current_mode() is obs.ObservationMode.OFF

    def test_unknown_value_falls_back_to_off(self, monkeypatch):
        # 오타 난 환경변수 때문에 조용히 실험 팔이 바뀌면 측정이 무의미해진다.
        monkeypatch.setenv(obs.ENV_VAR, "loopy")
        assert obs.current_mode() is obs.ObservationMode.OFF

    def test_read_first_allows_observation_but_does_not_feed_back(self, monkeypatch):
        monkeypatch.setenv(obs.ENV_VAR, "read_first")
        assert obs.allows_read_first() is True
        assert obs.feeds_result_back() is False

    def test_loop_does_both(self, monkeypatch):
        monkeypatch.setenv(obs.ENV_VAR, "loop")
        assert obs.allows_read_first() is True
        assert obs.feeds_result_back() is True


class TestPromptStability:
    def test_off_leaves_the_prompt_byte_identical(self):
        context = {
            "workbook_id": "book.xlsx",
            "sheet_name": "매출",
            "workbook_digest_text": "현재 통합문서 상태:\n- 시트 매출\n",
        }
        without_key = build_planner_prompt("정렬해줘", context=context)
        with_empty_key = build_planner_prompt(
            "정렬해줘", context={**context, "observation_text": ""}
        )
        assert without_key == with_empty_key

    def test_observation_text_lands_in_the_prompt(self):
        prompt = build_planner_prompt(
            "빈 칸 지워줘",
            context={"observation_text": "관측 결과 (excel_live.read_range A1:B2):\n  1: 1 | (빈칸)\n"},
        )
        assert "관측 결과 (excel_live.read_range A1:B2)" in prompt
        assert "(빈칸)" in prompt


class TestTruncate:
    plan: ClassVar[list[dict[str, Any]]] = [
        {"action": "excel_live.read_range", "params": {}},
        {"action": "excel_live.clear_range", "params": {}},
    ]

    def test_off_and_read_first_keep_the_whole_plan(self, monkeypatch):
        for mode in ("off", "read_first"):
            monkeypatch.setenv(obs.ENV_VAR, mode)
            assert obs.truncate_at_observation(self.plan) == self.plan

    def test_loop_cuts_after_the_observation(self, monkeypatch):
        # 끊지 않으면 편집 단계가 **읽기 전에 정해진 인자로** 실행돼, 재계획할
        # 기회가 오기 전에 파일이 이미 바뀐다.
        monkeypatch.setenv(obs.ENV_VAR, "loop")
        assert obs.truncate_at_observation(self.plan) == self.plan[:1]

    def test_loop_leaves_a_plan_without_observation_alone(self, monkeypatch):
        monkeypatch.setenv(obs.ENV_VAR, "loop")
        edits = [{"action": "excel_live.clear_range", "params": {}}]
        assert obs.truncate_at_observation(edits) == edits


class TestReplanTrigger:
    def test_only_loop_triggers(self, monkeypatch):
        execution = _read([[1, 2]])
        for mode in ("off", "read_first"):
            monkeypatch.setenv(obs.ENV_VAR, mode)
            assert obs.should_replan_after_observation(execution) is False
        monkeypatch.setenv(obs.ENV_VAR, "loop")
        assert obs.should_replan_after_observation(execution) is True

    def test_an_edit_does_not_trigger(self, monkeypatch):
        monkeypatch.setenv(obs.ENV_VAR, "loop")
        execution = _Execution(_Step("excel_live.clear_range", {"cleared_cells": 3}))
        assert obs.should_replan_after_observation(execution) is False

    def test_a_broken_observation_does_not_trigger(self, monkeypatch):
        # 읽다가 터진 것은 실패 재계획이 맡는다. 여기서 또 잡으면 두 번 돈다.
        monkeypatch.setenv(obs.ENV_VAR, "loop")
        execution = _Execution(_Step("excel_live.read_range", {}, error="범위를 찾지 못함"))
        assert obs.should_replan_after_observation(execution) is False

    def test_budget_stops_repeated_observation(self, monkeypatch):
        monkeypatch.setenv(obs.ENV_VAR, "loop")
        execution = _read([[1]])
        assert obs.should_replan_after_observation(execution, observed=0) is True
        assert obs.should_replan_after_observation(execution, observed=1) is False


class TestRender:
    def test_it_separates_a_number_from_a_string_that_looks_like_one(self, monkeypatch):
        # 이 구분이 이 블록의 존재 이유다. "22"와 22가 같아 보이면 읽어도 소용없다.
        text = obs.render_observation(_read([[22, "22"]]))
        assert '"22"' in text
        assert " 22 |" in text or "22 | " in text

    def test_it_marks_blanks(self):
        text = obs.render_observation(_read([["서울", None], ["", "부산"]]))
        # 범례 줄에도 같은 표시가 있으므로 데이터 줄만 센다.
        data_lines = [line for line in text.splitlines() if line.strip().startswith(("1:", "2:"))]
        assert sum(line.count("(빈칸)") for line in data_lines) == 2

    def test_it_says_when_it_clipped(self):
        text = obs.render_observation(_read([[i] for i in range(200)]), max_rows=10)
        assert "행 190개 더 있음" in text

    def test_validate_data_issues_are_rendered(self):
        execution = _Execution(
            _Step(
                "excel_live.validate_data",
                {"address": "D2:D50", "issues": [{"type": "empty", "count": 3}]},
            )
        )
        text = obs.render_observation(execution)
        assert "검사 결과" in text
        assert "'count': 3" in text or '"count": 3' in text

    def test_nothing_to_render_is_empty(self):
        assert obs.render_observation(_Execution(None)) == ""
        assert obs.render_observation(_Execution(_Step("excel_live.read_range", {}))) == ""
