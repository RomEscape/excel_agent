"""되묻기 학습 사례 생성기 테스트.

되묻기 데이터가 잘못되면 모델은 "아무 때나 묻는" 쪽으로 망가진다.
그래서 두 가지를 못 박는다: 질문은 실제 통합문서 내용으로 만들어야 하고,
답변 턴의 계획은 실행기가 실제로 받을 수 있는 형태여야 한다.
"""

from office_claw_sidecar.services.excel_clarify_cases import build_clarify_records
from office_claw_sidecar.services.excel_live_executor import PlanStep
from office_claw_sidecar.services.excel_live_plan_validator import (
    ValidationContext,
    validate_plan,
)
from office_claw_sidecar.services.excel_workbook_fixtures import digest_headers

RECORDS = build_clarify_records(repeats=2)
ASK_RECORDS = [r for r in RECORDS if r["record_id"].endswith(":ask")]
ANSWER_RECORDS = [r for r in RECORDS if r["record_id"].endswith(":answer")]


def test_generates_matched_ask_and_answer_pairs():
    assert len(ASK_RECORDS) > 40
    assert len(ASK_RECORDS) == len(ANSWER_RECORDS)


def test_every_ask_record_is_a_single_clarify_step():
    for record in ASK_RECORDS:
        plan = record["output_json"]["action_plan"]
        assert len(plan) == 1
        assert plan[0]["action"] == "excel_live.clarify"
        assert record["output_json"]["intent"] == "clarify"
        assert record["output_json"]["mutates_workbook"] is False


def test_questions_quote_real_headers_or_sheets():
    """질문에 통합문서의 실제 이름이 하나도 없으면 빈 되묻기다."""
    for record in ASK_RECORDS:
        question = record["output_json"]["follow_up_question"]
        digest = record["digest"]
        names = set(digest_headers(digest))
        names.update(str(sheet["name"]) for sheet in digest["sheets"])
        for sheet in digest["sheets"]:
            names.update(digest_headers(digest, str(sheet["name"])))
        assert any(name and name in question for name in names), question


def test_answer_records_carry_the_previous_turn():
    for record in ANSWER_RECORDS:
        history = record["conversation_history"]
        assert history["original_message"]
        assert history["question"]
        assert record["output_json"]["follow_up_question"] == ""


def test_answer_plans_survive_the_production_validator():
    """정답 계획이 검증기를 통과하지 못하면 학습해도 실행되지 않는다."""
    for record in ANSWER_RECORDS:
        steps = [
            PlanStep(action=s["action"], params=dict(s["params"]), reason=s["reason"])
            for s in record["output_json"]["action_plan"]
        ]
        validated = validate_plan(
            steps, context=ValidationContext(message=record["instruction"])
        )
        assert validated
        assert validated[0].action == steps[0].action


def test_answer_plans_reference_columns_that_exist():
    """답변 계획이 다이제스트에 없는 열을 쓰면 환각을 정답으로 가르치는 셈이다."""
    for record in ANSWER_RECORDS:
        digest = record["digest"]
        for step in record["output_json"]["action_plan"]:
            params = step["params"]
            sheet = str(params.get("sheet_name") or params.get("source_sheet") or "")
            headers = digest_headers(digest, sheet)
            for key in ("column", "group_column", "value_column", "row_field", "value_field"):
                value = params.get(key)
                if isinstance(value, str) and value:
                    assert value in headers, f"{record['record_id']}: {key}={value}"


def test_case_variety_is_wide_enough():
    kinds = {record["record_id"].split(":")[1] for record in ASK_RECORDS}
    assert len(kinds) >= 6
