"""고정 평가셋 무결성 검사.

평가셋이 학습 데이터와 조금이라도 겹치면 측정값이 부풀려진다. 여기서 막는다.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(_SCRIPTS))

_SPEC = importlib.util.spec_from_file_location(
    "build_planner_eval_set", _SCRIPTS / "build_planner_eval_set.py"
)
builder = importlib.util.module_from_spec(_SPEC)
sys.modules["build_planner_eval_set"] = builder
_SPEC.loader.exec_module(builder)

import planner_eval_cases as cases

from office_claw_sidecar.services.excel_live_plan_validator import SUPPORTED_ACTIONS
from office_claw_sidecar.services.excel_workbook_fixtures import WORKBOOK_FIXTURES

CLARIFY = "excel_live.clarify"


def test_every_supported_action_appears():
    """49개 실행 액션 + clarify 전부 한 번은 나와야 회귀를 잡을 수 있다."""
    seen = {action for case in cases.all_cases() for action in case.expected}
    missing = sorted(set(SUPPORTED_ACTIONS) - seen)
    assert not missing, f"평가셋에 없는 액션: {missing}"


def test_expected_actions_are_known_to_validator():
    unknown = sorted(
        {
            action
            for case in cases.all_cases()
            for action in case.expected
            if action not in SUPPORTED_ACTIONS
        }
    )
    assert not unknown, f"검증기가 모르는 액션: {unknown}"


def test_no_sheet_name_overlap_with_training_fixtures():
    """평가 통합문서가 학습 픽스처와 시트명을 공유하면 외운 걸 재게 된다."""
    training = {fixture.name for fixture in WORKBOOK_FIXTURES}
    evaluation = {
        str(sheet.get("name"))
        for workbook in cases.WORKBOOKS.values()
        for sheet in workbook.get("sheets", [])
    }
    overlap = sorted(training & evaluation)
    assert not overlap, f"학습 픽스처와 겹치는 시트: {overlap}"


def test_no_header_overlap_beyond_generic_terms():
    """머리글도 대부분 달라야 한다. 날짜·금액 같은 일반어 몇 개는 허용."""
    generic = {"날짜", "금액", "이름", "수량", "단가", "비고", "상태", "결제수단"}
    training = {
        column
        for fixture in WORKBOOK_FIXTURES
        for column in fixture.columns
    }
    evaluation = {
        str(column.get("header"))
        for workbook in cases.WORKBOOKS.values()
        for sheet in workbook.get("sheets", [])
        for column in sheet.get("columns", [])
    }
    overlap = sorted((training & evaluation) - generic)
    assert not overlap, f"학습 픽스처와 겹치는 머리글: {overlap}"


def test_clarify_yes_cases_expect_a_question():
    for case in cases.all_cases():
        if case.category == "clarify_yes":
            assert case.expected == (CLARIFY,), f"{case.case_id}: 되묻기 정답이어야 합니다"


def test_clarify_no_cases_never_expect_a_question():
    """되묻기를 가르친 뒤 생기는 과잉 질문을 잡는 자리 — 정답에 clarify가 있으면 안 된다."""
    for case in cases.all_cases():
        if case.category == "clarify_no":
            assert CLARIFY not in case.expected, f"{case.case_id}: 되물으면 오답인 케이스입니다"


def test_case_ids_and_instructions_are_unique():
    all_cases = cases.all_cases()
    ids = [case.case_id for case in all_cases]
    assert len(ids) == len(set(ids)), "case_id가 중복됩니다"
    texts = [case.instruction for case in all_cases]
    assert len(texts) == len(set(texts)), "같은 문장이 두 번 들어갔습니다"


def test_built_rows_carry_a_workbook_digest():
    """다이제스트 없이 평가하면 프로덕션과 다른 조건에서 재게 된다."""
    for row in builder.build_rows():
        digest_text = row["input"]["workbook_digest_text"]
        assert digest_text.startswith("현재 통합문서 상태"), row["record_id"]
        assert row["input"]["context_hints"]["sheet_name"], row["record_id"]


def test_digest_mentions_the_sheet_the_case_targets():
    for row in builder.build_rows():
        sheet = row["input"]["context_hints"]["sheet_name"]
        assert sheet in row["input"]["workbook_digest_text"], row["record_id"]


def test_minimum_size_for_meaningful_comparison():
    """21건짜리 평가셋으로는 1건 차이가 노이즈에 묻힌다."""
    assert len(cases.all_cases()) >= 150


def _training_instructions() -> set[str]:
    """학습 JSONL에서 사용자 문장만 뽑는다. 프롬프트 끝의 '사용자 메시지:' 뒤가 원문이다."""
    root = Path(__file__).resolve().parents[3] / "datasets" / "train"
    found: set[str] = set()
    for path in sorted(root.glob("planner_sft_v5*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            for message in row.get("messages") or []:
                if message.get("role") != "user":
                    continue
                hits = re.findall(r"사용자 메시지:\s*(.+)", str(message.get("content") or ""))
                if hits:
                    found.add(_norm(hits[-1]))
    return found


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", str(text or ""))


def test_no_instruction_leaked_from_training_data():
    """같은 문장이 양쪽에 있으면 암기력을 재게 된다.

    짧고 모호한 표현("걸러줘")은 손으로 써도 학습 템플릿과 자연히 겹친다 —
    그래서 사람 눈이 아니라 이 검사로 막는다.
    """
    training = _training_instructions()
    if not training:
        pytest.skip("학습 데이터셋이 없어 누수 검사를 건너뜁니다.")
    leaked = sorted(
        case.case_id for case in cases.all_cases() if _norm(case.instruction) in training
    )
    assert not leaked, f"학습 데이터와 같은 문장: {leaked}"
