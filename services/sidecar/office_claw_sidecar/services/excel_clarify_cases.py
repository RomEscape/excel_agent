"""되묻기 학습 사례 생성기.

플래너 학습 데이터 1000건은 전부 `follow_up_question`이 빈 문자열이었다.
즉 모델은 "무슨 일이 있어도 뭔가를 실행하라"고 배웠다. 그래서 "정리해줘"처럼
대상이 없는 문장에도 열을 하나 골라 실행하고, 사용자는 엉뚱한 결과를 받는다.

이 모듈은 그 반대 신호를 만든다:
- **1턴**: 추측하면 데이터가 잘못 바뀌는 문장 → `excel_live.clarify`로 되묻기
- **2턴**: 사용자의 답변 + 이전 대화 → 완성된 실행 계획

질문 문장은 통합문서 다이제스트의 **실제 머리글·값 후보**로 만든다.
"무엇을 도와드릴까요?" 같은 빈 되묻기는 사용자에게 아무 정보도 주지 않기 때문이다.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from typing import Any

from office_claw_sidecar.services.excel_workbook_fixtures import (
    WORKBOOK_FIXTURES,
    categorical_headers,
    digest_from_fixtures,
    digest_headers,
    numeric_headers,
)


def _clarify_output(question: str, reason: str) -> dict[str, Any]:
    return {
        "intent": "clarify",
        "mutates_workbook": False,
        "action_plan": [
            {
                "action": "excel_live.clarify",
                "params": {"question": question},
                "reason": reason,
            }
        ],
        "slot_fill": {},
        "partial_params": {},
        "follow_up_question": question,
        "reason": reason,
    }


def _plan_output(steps: list[dict[str, Any]], reason: str, *, intent: str = "edit") -> dict[str, Any]:
    return {
        "intent": intent,
        "mutates_workbook": intent == "edit",
        "action_plan": steps,
        "slot_fill": {},
        "partial_params": {},
        "follow_up_question": "",
        "reason": reason,
    }


# 하나의 애매 사례는 (1턴 되묻기, 2턴 실행) 두 레코드를 만든다.
# 반환이 None이면 그 통합문서로는 그 사례를 만들 수 없다는 뜻이다
# (예: 숫자 열이 하나뿐이면 "어느 열 기준" 질문이 성립하지 않는다).
CaseBuilder = Callable[[dict[str, Any], random.Random], tuple[str, str, str, dict[str, Any], str] | None]


def _case_sort(digest: dict[str, Any], rng: random.Random):
    numbers = numeric_headers(digest)
    if len(numbers) < 2:
        return None
    first, second = numbers[0], numbers[1]
    sheet = str(digest.get("active_sheet"))
    ask = rng.choice(["정렬해줘", "이거 순서대로 정리해줘", "보기 좋게 정렬 좀", "줄 세워줘"])
    question = f"'{first}'과 '{second}' 중 어느 열을 기준으로 정렬할까요? 오름차순인지 내림차순인지도 알려주세요."
    answer = rng.choice([f"{second} 기준 내림차순으로", f"{second} 큰 순서대로"])
    plan = _plan_output(
        [
            {
                "action": "excel_live.sort_rows",
                "params": {"sheet_name": sheet, "column": second, "order": "desc"},
                "reason": f"{second} 내림차순 정렬",
            }
        ],
        "답변받은 기준으로 정렬",
    )
    return ask, question, answer, plan, "정렬 기준 열과 방향이 정해지지 않음"


def _case_filter(digest: dict[str, Any], rng: random.Random):
    categories = categorical_headers(digest)
    if not categories:
        return None
    header, values = categories[0]
    sheet = str(digest.get("active_sheet"))
    keep = values[0]
    ask = rng.choice(["필터 걸어줘", "필요한 것만 남겨줘", "걸러줘"])
    question = (
        f"'{header}' 열을 기준으로 거를까요? 값 후보는 {', '.join(values[:4])} 입니다. "
        "어떤 값만 남길지 알려주세요."
    )
    answer = rng.choice([f"{keep}만 남겨줘", f"{keep}인 행만"])
    plan = _plan_output(
        [
            {
                "action": "excel_live.filter_rows",
                "params": {
                    "sheet_name": sheet,
                    "target_range": "__USED_RANGE__",
                    "column": header,
                    "operator": "==",
                    "value": keep,
                    "has_header": True,
                    "mode": "keep",
                },
                "reason": f"{header}={keep} 행만 남김",
            }
        ],
        "답변받은 조건으로 필터",
    )
    return ask, question, answer, plan, "거를 열과 기준값이 정해지지 않음"


def _case_delete(digest: dict[str, Any], rng: random.Random):
    headers = digest_headers(digest)
    if len(headers) < 3:
        return None
    sheet = str(digest.get("active_sheet"))
    target = headers[-1]
    ask = rng.choice(["지워줘", "필요 없는 거 삭제해줘", "정리하면서 좀 지워줘"])
    question = (
        f"시트 '{sheet}'에서 무엇을 지울까요? 열 하나를 통째로 지우는 건지, "
        f"특정 조건의 행을 지우는 건지 알려주세요. 현재 열은 {', '.join(headers[:5])} 입니다."
    )
    answer = rng.choice([f"{target} 열 통째로 지워줘", f"{target} 열 삭제"])
    plan = _plan_output(
        [
            {
                "action": "excel_live.drop_column",
                "params": {"sheet_name": sheet, "column": target},
                "reason": f"{target} 열 삭제",
            }
        ],
        "답변받은 열 삭제",
    )
    return ask, question, answer, plan, "삭제 대상이 열인지 행인지 알 수 없음"


def _case_aggregate(digest: dict[str, Any], rng: random.Random):
    numbers = numeric_headers(digest)
    categories = categorical_headers(digest)
    if not numbers or not categories:
        return None
    group, _values = categories[0]
    value = numbers[-1]
    sheet = str(digest.get("active_sheet"))
    ask = rng.choice(["집계해줘", "합계 좀 내줘", "요약해줘"])
    question = (
        f"'{group}'별로 '{value}' 합계를 내면 될까요? 결과를 새 시트에 표로 만들지, "
        "숫자만 알려드릴지도 함께 알려주세요."
    )
    answer = rng.choice(["응 새 시트에 표로 만들어줘", "그렇게 해서 새 시트에 정리해줘"])
    plan = _plan_output(
        [
            {
                "action": "excel_live.pivot_table",
                "params": {
                    "source_sheet": sheet,
                    "source_range": "__USED_RANGE__",
                    "row_field": group,
                    "value_field": value,
                    "agg": "sum",
                    "output_sheet": f"{group}별집계",
                    "output_start": "A1",
                    "has_header": True,
                },
                "reason": f"{group}별 {value} 합계표",
            }
        ],
        "답변대로 새 시트에 집계표 생성",
    )
    return ask, question, answer, plan, "집계 기준과 출력 형태가 정해지지 않음"


def _case_chart(digest: dict[str, Any], rng: random.Random):
    numbers = numeric_headers(digest)
    categories = categorical_headers(digest)
    if not numbers or not categories:
        return None
    axis, _ = categories[0]
    value = numbers[-1]
    sheet = str(digest.get("active_sheet"))
    ask = rng.choice(["그래프 그려줘", "차트 하나 만들어줘", "시각화해줘"])
    question = (
        f"'{axis}'를 가로축, '{value}'를 값으로 그리면 될까요? "
        "막대·선·원 중 어떤 그래프가 좋을지 알려주세요."
    )
    answer = rng.choice(["막대 그래프로", "막대로 그려줘"])
    plan = _plan_output(
        [
            {
                "action": "excel_live.create_chart",
                "params": {
                    "source_range": "__USED_RANGE__",
                    "chart_type": "bar",
                    "title": f"{axis}별 {value}",
                    "output_sheet": sheet,
                },
                "reason": f"{axis}별 {value} 막대 그래프",
            }
        ],
        "답변받은 종류로 차트 생성",
    )
    return ask, question, answer, plan, "차트 종류와 축이 정해지지 않음"


def _case_dedupe(digest: dict[str, Any], rng: random.Random):
    headers = digest_headers(digest)
    if len(headers) < 2:
        return None
    sheet = str(digest.get("active_sheet"))
    key = headers[0]
    ask = rng.choice(["중복 정리해줘", "중복된 거 빼줘", "겹치는 행 처리해줘"])
    question = (
        f"어떤 열이 같으면 중복으로 볼까요? 예를 들어 '{key}' 기준인지, "
        f"'{headers[1]}'까지 같아야 하는지 알려주세요. 그리고 지울지 표시만 할지도요."
    )
    answer = rng.choice([f"{key} 같으면 중복이야, 지워줘", f"{key} 기준으로 삭제해줘"])
    plan = _plan_output(
        [
            {
                "action": "excel_live.dedupe_rows",
                "params": {
                    "sheet_name": sheet,
                    "target_range": "__USED_RANGE__",
                    "key_columns": [key],
                    "has_header": True,
                },
                "reason": f"{key} 기준 중복 제거",
            }
        ],
        "답변받은 기준으로 중복 제거",
    )
    return ask, question, answer, plan, "중복 판단 기준 열이 정해지지 않음"


def _case_highlight(digest: dict[str, Any], rng: random.Random):
    numbers = numeric_headers(digest)
    if not numbers:
        return None
    target = numbers[-1]
    ask = rng.choice(["색칠해줘", "눈에 띄게 표시해줘", "강조 좀 해줘"])
    question = (
        f"어떤 조건을 강조할까요? 예를 들어 '{target}'이 일정 값 이상인 셀을 칠하는 식으로요. "
        "기준값과 색을 알려주세요."
    )
    answer = rng.choice(["100만 이상을 노란색으로", "100만 넘는 건 노란색"])
    plan = _plan_output(
        [
            {
                "action": "excel_live.highlight_by_condition",
                "params": {
                    "target_range": "__USED_RANGE__",
                    "operator": ">=",
                    "threshold": 1000000,
                    "fill_color": "#FFFF00",
                },
                "reason": f"{target} 100만 이상 노란색",
            }
        ],
        "답변받은 조건으로 강조",
    )
    return ask, question, answer, plan, "강조할 조건과 색이 정해지지 않음"


def _case_sheet_choice(digest: dict[str, Any], rng: random.Random):
    names = [str(sheet.get("name")) for sheet in digest.get("sheets") or []]
    if len(names) < 2:
        return None
    ask = rng.choice(["여기 정리해줘", "이 파일 좀 다듬어줘"])
    question = (
        f"시트가 {', '.join(names)} 이렇게 있습니다. 어느 시트를 정리할까요? "
        "그리고 정렬·중복 제거 중 무엇을 원하시는지도 알려주세요."
    )
    target = names[1]
    headers = digest_headers(digest, target) or digest_headers(digest)
    key = headers[0] if headers else "A"
    answer = f"{target} 시트에서 {key} 기준으로 정렬해줘"
    plan = _plan_output(
        [
            {
                "action": "excel_live.sort_rows",
                "params": {"sheet_name": target, "column": key, "order": "asc"},
                "reason": f"{target} 시트 {key} 오름차순",
            }
        ],
        "답변받은 시트와 기준으로 정렬",
    )
    return ask, question, answer, plan, "대상 시트와 작업 종류가 정해지지 않음"


CASE_BUILDERS: tuple[tuple[str, CaseBuilder], ...] = (
    ("sort", _case_sort),
    ("filter", _case_filter),
    ("delete", _case_delete),
    ("aggregate", _case_aggregate),
    ("chart", _case_chart),
    ("dedupe", _case_dedupe),
    ("highlight", _case_highlight),
    ("sheet_choice", _case_sheet_choice),
)


def build_clarify_records(*, seed: int = 7, repeats: int = 3) -> list[dict[str, Any]]:
    """되묻기 1턴 + 답변 2턴 레코드를 만든다.

    통합문서를 바꿔 가며 같은 사례를 여러 번 만든다. 질문에 들어가는 머리글이
    매번 달라져야 모델이 문장을 통째로 외우지 않고 다이제스트를 보고 만든다.
    """
    rng = random.Random(seed)
    fixture_names = [fixture.name for fixture in WORKBOOK_FIXTURES]
    records: list[dict[str, Any]] = []

    for repeat in range(repeats):
        for primary in fixture_names:
            others = [name for name in fixture_names if name != primary]
            secondary = others[(repeat + fixture_names.index(primary)) % len(others)]
            digest = digest_from_fixtures(
                [primary, secondary], seed=f"clarify-{primary}-{repeat}"
            )
            for case_name, builder in CASE_BUILDERS:
                built = builder(digest, rng)
                if built is None:
                    continue
                ask, question, answer, plan, reason = built
                base_id = f"clarify_v4:{case_name}:{primary}:{repeat}"
                records.append(
                    {
                        "record_id": f"{base_id}:ask",
                        "instruction": ask,
                        "output_json": _clarify_output(question, reason),
                        "digest": digest,
                    }
                )
                records.append(
                    {
                        "record_id": f"{base_id}:answer",
                        "instruction": answer,
                        "output_json": plan,
                        "digest": digest,
                        "conversation_history": {
                            "original_message": ask,
                            "question": question,
                        },
                    }
                )
    return records
