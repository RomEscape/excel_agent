"""플래너 프롬프트의 액션 목록이 실행 계층과 어긋나지 않는지 검증한다.

프롬프트는 모델이 고를 수 있는 후보의 전부다. 검증기가 실행할 수 있는 액션이
프롬프트에 없으면 그 기능은 구현돼 있어도 영영 선택되지 않고, 반대로 프롬프트에만
있는 액션은 고르는 순간 실행에 실패한다.

실제로 clear_range를 포함한 7종이 프롬프트에서 누락된 채로 배포됐고,
그 상태에서 만든 SFT 데이터는 "허용되지 않는다고 적힌 액션"을 정답으로 가르쳤다.
"""

from __future__ import annotations

import re

from office_claw_sidecar.services.excel_live_plan_validator import (
    EDIT_ACTIONS,
    SUPPORTED_ACTIONS,
)
from office_claw_sidecar.services.excel_planner_prompt import build_planner_prompt


def prompt_listed_actions() -> set[str]:
    """프롬프트에서 '- excel_live.xxx' 항목 줄로 나열된 액션만 뽑는다."""
    prompt = build_planner_prompt("샘플 요청", context={}, planner_model="test")
    return set(re.findall(r"^\s*-\s+(excel_live\.[a-z_]+)", prompt, re.MULTILINE))


def test_prompt_lists_every_supported_action():
    missing = SUPPORTED_ACTIONS - prompt_listed_actions()
    assert not missing, f"검증기는 실행할 수 있는데 프롬프트에 없는 액션: {sorted(missing)}"


def test_prompt_lists_no_unsupported_action():
    extra = prompt_listed_actions() - SUPPORTED_ACTIONS
    assert not extra, f"프롬프트에만 있고 실행할 수 없는 액션: {sorted(extra)}"


def test_edit_action_hint_matches_validator():
    prompt = build_planner_prompt("샘플 요청", context={}, planner_model="test")
    for action in EDIT_ACTIONS:
        short = action.removeprefix("excel_live.")
        assert short in prompt, f"편집 액션 힌트에 빠진 액션: {action}"
