"""학습용 통합문서 합성기 테스트.

여기서 지키려는 계약은 하나다: **정답 계획이 가리키는 시트·머리글은 반드시
다이제스트 안에 있어야 한다.** 이게 깨지면 "없는 걸 쓰라"고 가르치는 셈이라
지금 겪고 있는 환각을 데이터로 굳히게 된다.
"""

from office_claw_sidecar.services.excel_workbook_digest import render_workbook_digest
from office_claw_sidecar.services.excel_workbook_fixtures import (
    collect_plan_references,
    digest_headers,
    synthesize_digest,
)


def _sort_plan(sheet: str = "Sales_Data", column: str = "금액") -> list[dict]:
    return [
        {
            "action": "excel_live.sort_rows",
            "params": {"sheet_name": sheet, "column": column, "order": "desc"},
            "reason": "정렬",
        }
    ]


def test_referenced_column_always_exists_in_digest():
    digest, plan = synthesize_digest(_sort_plan(column="금액"), instruction="금액 큰 순으로", seed="a")
    assert "금액" in digest_headers(digest, plan[0]["params"]["sheet_name"])


def test_referenced_sheet_always_exists_in_digest():
    digest, plan = synthesize_digest(_sort_plan(), instruction="정렬해줘", seed="b")
    names = {sheet["name"] for sheet in digest["sheets"]}
    assert plan[0]["params"]["sheet_name"] in names


def test_sheet_names_vary_across_records():
    """같은 계획이라도 레코드마다 시트가 달라야 이름을 외우지 못한다."""
    seen = set()
    for seed in [str(i) for i in range(20)]:
        _, plan = synthesize_digest(_sort_plan(), instruction="정렬해줘", seed=seed)
        seen.add(plan[0]["params"]["sheet_name"])
    assert len(seen) >= 5


def test_sheet_named_in_the_instruction_is_kept():
    """원문이 시트를 지목했으면 그 이름을 바꾸면 안 된다 — 정답이 원문과 어긋난다."""
    digest, plan = synthesize_digest(
        _sort_plan(sheet="매출"), instruction="매출 시트를 금액 순으로 정렬해줘", seed="c"
    )
    assert plan[0]["params"]["sheet_name"] == "매출"
    assert "매출" in {sheet["name"] for sheet in digest["sheets"]}


def test_new_sheet_targets_are_not_rewritten():
    """pivot 결과 시트는 아직 없는 시트다. 기존 시트로 바꾸면 원본을 덮어쓰게 된다."""
    plan = [
        {
            "action": "excel_live.pivot_table",
            "params": {
                "source_sheet": "Sales_Data",
                "output_sheet": "지역별집계",
                "row_field": "지역",
                "value_field": "금액",
            },
            "reason": "집계",
        }
    ]
    digest, rewritten = synthesize_digest(plan, instruction="지역별 매출 집계해줘", seed="d")
    assert rewritten[0]["params"]["output_sheet"] == "지역별집계"
    assert "지역별집계" not in {sheet["name"] for sheet in digest["sheets"]}


def test_rename_target_name_is_not_forced_into_digest():
    """바꿀 새 이름까지 다이제스트에 넣으면 '이미 있는 열로 이름을 바꿔라'가 된다."""
    plan = [
        {
            "action": "excel_live.rename_column",
            "params": {"column": "Amount", "new_name": "매출액"},
            "reason": "이름 변경",
        }
    ]
    digest, _ = synthesize_digest(plan, instruction="Amount를 매출액으로 바꿔줘", seed="e")
    headers = digest_headers(digest)
    assert "Amount" in headers
    assert "매출액" not in headers


def test_positional_column_selectors_are_not_treated_as_headers():
    sheets, columns = collect_plan_references(
        [
            {
                "action": "excel_live.sort_range",
                "params": {"target_range": "A1:E9", "key_column": 3},
                "reason": "",
            }
        ]
    )
    assert sheets == []
    assert columns == []


def test_digest_renders_with_the_production_renderer():
    """학습 프롬프트와 추론 프롬프트가 같은 렌더러를 통과해야 형식이 어긋나지 않는다."""
    digest, _ = synthesize_digest(_sort_plan(), instruction="정렬해줘", seed="f")
    text = render_workbook_digest(digest)
    assert text.startswith("현재 통합문서 상태(실제 파일에서 읽음):")
    assert "(활성)" in text
    assert "열: A=" in text


def test_digest_has_more_than_one_sheet_to_choose_from():
    digest, _ = synthesize_digest(_sort_plan(), instruction="정렬해줘", seed="g")
    assert len(digest["sheets"]) >= 2
