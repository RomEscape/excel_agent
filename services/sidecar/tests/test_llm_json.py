"""모델 응답에서 JSON을 꺼내는 파서.

각 케이스마다 **예전 탐욕 정규식이 무엇을 하는지** 함께 단언한다. 그래야 이 파서가
막아 주는 것이 무엇인지가 테스트만 읽어도 드러나고, 나중에 "정규식으로 되돌려도
되지 않나"는 질문에 답이 남는다.
"""

from __future__ import annotations

import json
import re

import pytest

from office_claw_sidecar.services.llm_json import (
    extract_json_object,
    iter_json_objects,
    strip_reasoning,
)

# 교체 전에 두 플래너와 매크로 분해기가 함께 쓰던 정규식.
GREEDY = re.compile(r"\{.*\}", re.DOTALL)


def greedy_parse(raw: str):
    """예전 방식. 실패하면 None."""
    match = GREEDY.search(raw)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except ValueError:
        return None


# ── 예전 방식이 깨지던 응답 ────────────────────────────────────────────────


def test_two_objects_in_one_response():
    """오브젝트가 둘이면 정규식은 둘을 하나로 이어 붙여 깨진다."""
    raw = '{"draft": 1}\n{"action": "excel_live.write_range", "params": {}}'

    assert greedy_parse(raw) is None
    assert extract_json_object(raw, require_keys=("action",))["action"] == (
        "excel_live.write_range"
    )


def test_prose_with_braces_after_the_json():
    """JSON 뒤 설명문에 중괄호가 있으면 정규식은 거기까지 삼킨다."""
    raw = '{"action": "excel_live.sort_range"}\n\n참고: 범위는 {A1:C10} 기준입니다.'

    assert greedy_parse(raw) is None
    assert extract_json_object(raw)["action"] == "excel_live.sort_range"


def test_closed_think_block_with_a_draft_inside():
    """사고 블록 안의 초안 JSON이 진짜 답과 섞이면 안 된다."""
    raw = (
        "<think>\n"
        '먼저 {"action": "excel_live.read_range"} 를 생각했는데 편집 요청이다.\n'
        "</think>\n"
        '{"action": "excel_live.write_range", "params": {"start_cell": "C3"}}'
    )

    assert greedy_parse(raw) is None
    parsed = extract_json_object(raw, require_keys=("action",))
    assert parsed["action"] == "excel_live.write_range"
    assert parsed["params"]["start_cell"] == "C3"


def test_closing_think_tag_without_an_opening_one():
    """스트리밍 중간부터 받으면 여는 태그 없이 닫는 태그만 온다."""
    raw = '정렬이 맞겠다.</think>{"action": "excel_live.sort_range"}'

    assert extract_json_object(raw)["action"] == "excel_live.sort_range"


def test_the_example_from_the_prompt_is_not_mistaken_for_the_answer():
    """모델이 출력 예시를 먼저 따라 쓰고 답을 뒤에 붙이는 경우.

    앞에서부터 무조건 첫 오브젝트를 집으면 예시를 실행하게 된다.
    """
    raw = (
        '출력 형식은 {"reason": "한 줄 한국어"} 입니다.\n'
        '{"action_plan": [{"action": "excel_live.dedupe_rows", "params": {}}], "intent": "edit"}'
    )

    assert greedy_parse(raw) is None
    parsed = extract_json_object(raw, require_keys=("action_plan", "action"))
    assert parsed["action_plan"][0]["action"] == "excel_live.dedupe_rows"


# ── 예전 방식도 되던 응답 — 회귀가 없어야 한다 ────────────────────────────


def test_a_bare_object_still_parses():
    """지금 기본 플래너(`ax7bplanner-*`)가 실제로 뱉는 모양."""
    raw = '{"action_plan": [{"action": "excel_live.write_range", "params": {}}], "intent": "edit"}'

    assert greedy_parse(raw) == extract_json_object(raw)


def test_a_fenced_object_still_parses():
    raw = '```json\n{"action": "excel_live.read_range", "params": {"range_ref": "A1:B2"}}\n```'

    assert extract_json_object(raw)["params"]["range_ref"] == "A1:B2"


@pytest.mark.parametrize(
    "value",
    ["A1 } 참고", '따옴표 " 포함', "역슬래시 \\\\ 포함", "{중괄호로 감싼 값}"],
)
def test_braces_and_quotes_inside_string_values(value):
    """문자열 안의 중괄호를 세면 범위가 엉뚱한 데서 끊긴다."""
    raw = json.dumps({"action": "excel_live.write_range", "reason": value}, ensure_ascii=False)

    assert extract_json_object(raw)["reason"] == value


# ── 꺼낼 것이 없을 때 ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "죄송하지만 요청을 이해하지 못했습니다.",
        '{"action": "excel_live.write_range"',  # 중간에 끊긴 응답
        "[1, 2, 3]",  # 오브젝트가 아니다
    ],
)
def test_nothing_to_extract(raw):
    assert extract_json_object(raw) is None


def test_an_unclosed_think_block_still_yields_a_complete_object():
    """생각을 닫지 않아도 완결된 오브젝트가 있으면 집어낸다."""
    raw = '<think>고민 중\n{"action": "excel_live.list_workbooks", "params": {}}'

    assert extract_json_object(raw)["action"] == "excel_live.list_workbooks"


def test_falls_back_when_no_object_has_the_required_key():
    """조건에 맞는 것이 없으면 아무것도 안 주기보다 파싱된 첫 오브젝트를 준다."""
    raw = '{"reason": "무슨 말인지 모르겠습니다"}'

    assert extract_json_object(raw, require_keys=("action",))["reason"] == (
        "무슨 말인지 모르겠습니다"
    )


# ── 조각 함수 ──────────────────────────────────────────────────────────────


def test_strip_reasoning_keeps_only_what_follows_the_last_think_block():
    raw = "<think>하나</think>중간<think>둘</think>답"

    assert strip_reasoning(raw) == "답"


def test_iter_json_objects_yields_each_top_level_object_in_order():
    raw = '앞 {"a": {"b": 1}} 사이 {"c": 2} 뒤'

    assert list(iter_json_objects(raw)) == ['{"a": {"b": 1}}', '{"c": 2}']
