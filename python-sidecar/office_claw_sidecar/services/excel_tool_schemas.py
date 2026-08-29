"""
Excel 도구 스키마 — Pydantic 모델 + docstring에서 OpenAI 호환 `tools`를 자동 생성.

함수 1개 = Pydantic 파라미터 모델 1개.
  - 모델 docstring  → function.description (LLM의 함수 선택 근거)
  - Field(description=...) → parameters.properties.*.description
  - 필수/선택       → 기본값 유무로 결정 (기본값 없는 필드 = required)

model_json_schema() 원본은 소형 로컬 모델에 불필요한 노이즈(title, anyOf 래핑,
default)가 많아 `_clean_schema`로 정리해 전달한다.

권한(SAFE/CONFIRM/DENIED)은 여기서 정의하지 않는다 — 보안 정책은
`tool_registry.py`가 단일 소유자이며, 함수 이름 ↔ 레지스트리 액션 이름
매핑(`tool_name_to_action`)으로 연결된다.

LLM이 생성한 인자는 실행 전에 `validate_tool_params()`로 같은 모델에 대해
검증한다 — 검증 실패 메시지는 tool 결과로 재주입되어 LLM이 스스로 교정한다.
"""

from __future__ import annotations

import inspect
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

# 셀에 들어갈 수 있는 값 타입 (문자열/숫자/불리언/빈 셀)
CellValue = str | float | bool | None

_SHEET_DESC = "대상 시트 이름. 생략하면 현재 활성 시트를 사용합니다."


# ── 함수별 파라미터 모델 ──────────────────────────────────────────────────────


class ListWorkbooksParams(BaseModel):
    """실행 중인 Excel에서 현재 열려 있는 통합문서(워크북) 목록을 조회합니다. 어떤 파일이 열려 있는지 확인할 때 사용합니다."""


class SelectWorkbookParams(BaseModel):
    """작업 대상 통합문서를 선택합니다. 이후의 읽기/쓰기 작업이 이 통합문서를 대상으로 실행됩니다."""

    workbook_id: str = Field(
        ..., description="선택할 통합문서의 파일 이름(예: 'sales.xlsx') 또는 전체 경로"
    )


class ReadRangeParams(BaseModel):
    """엑셀 시트에서 지정된 범위의 셀 값을 읽어 2차원 배열로 반환합니다. 예: 'A1:C10', 'B9', 'D:D'(열 전체). 데이터 확인·분석·질문 답변에 필요한 값을 조회할 때 사용합니다."""

    range_ref: str | None = Field(
        None,
        description="읽을 범위의 A1 표기 주소 (예: 'A1:C10', 'B9', 'D:D'). 생략하면 사용자가 현재 선택한 범위를 읽습니다.",
    )
    sheet_name: str | None = Field(None, description=_SHEET_DESC)


class WriteRangeParams(BaseModel):
    """엑셀 시트의 지정 시작 셀부터 2차원 배열 값을 기록합니다. 예: start_cell 'B2'에 [['이름','수량'],['사과',10]]을 쓰면 B2:C3에 채워집니다. 셀 값 입력·수정에 사용합니다."""

    values_2d: list[list[CellValue]] = Field(
        ...,
        description="기록할 값의 2차원 배열. 한 행이 하나의 내부 배열입니다. 단일 셀이라도 [[값]] 형태로 전달합니다.",
    )
    start_cell: str | None = Field(
        None,
        description="기록을 시작할 셀 주소 (예: 'C3'). 생략하면 사용자가 현재 선택한 셀에 씁니다.",
    )
    sheet_name: str | None = Field(None, description=_SHEET_DESC)


class HighlightByConditionParams(BaseModel):
    """지정 범위에서 숫자 조건에 맞는 셀만 배경색을 칠합니다. 예: A열에서 50 이상인 셀만 노란색. 조건부 강조·색칠 요청에 사용합니다."""

    target_range: str = Field(..., description="대상 범위 (예: 'A:A'(열 전체), 'C1:C20')")
    operator: Literal[">", ">=", "<", "<=", "==", "!="] = Field(
        ..., description="비교 연산자 (예: '50 이상' → '>=')"
    )
    threshold: float = Field(..., description="비교 기준값 (예: '50 이상' → 50)")
    fill_color: str | None = Field(
        None,
        description="배경색 hex 코드. 노랑 '#FFFF00', 빨강 '#FF0000', 초록 '#00B050', 파랑 '#0070C0'. 생략 시 노랑.",
    )
    sheet_name: str | None = Field(None, description=_SHEET_DESC)


class ApplyBorderParams(BaseModel):
    """지정 범위에 경계선(테두리)을 적용합니다. 외곽과 내부 선이 모두 그려집니다."""

    target_range: str | None = Field(
        None,
        description="대상 범위 (예: 'B2:D5'). 생략하면 사용자가 현재 선택한 범위에 적용합니다.",
    )
    weight: Literal["thin", "medium", "thick"] | None = Field(
        None, description="선 굵기. 생략 시 medium."
    )
    color: str | None = Field(None, description="선 색상 hex 코드. 생략 시 검정 '#000000'.")
    sheet_name: str | None = Field(None, description=_SHEET_DESC)


class SetFormulaParams(BaseModel):
    """지정 범위의 모든 셀에 동일한 A1 스타일 수식을 설정합니다. 예: C1에 '=SUM(B2:B20)', I1:I10에 '=A1*2'(상대 참조는 행별로 자동 조정). 합계·평균·조건식 등 수식 요청에 사용합니다."""

    range_ref: str = Field(..., description="수식을 넣을 범위 (예: 'C1', 'I1:I10')")
    formula_a1: str = Field(
        ...,
        description="'='로 시작하는 A1 스타일 수식 (예: '=SUM(A1:A10)', '=IF(A2>0,\"Y\",\"N\")')",
    )
    sheet_name: str | None = Field(None, description=_SHEET_DESC)


class SaveWorkbookParams(BaseModel):
    """현재 통합문서를 디스크에 저장합니다."""

    workbook_id: str | None = Field(
        None,
        description="저장할 통합문서 이름 또는 경로. 생략하면 현재 선택된 통합문서를 저장합니다.",
    )


class CalculateColumnStatParams(BaseModel):
    """엑셀 시트에서 지정된 열(Column)의 숫자 통계를 계산합니다. 열은 머리글 이름(예: '매출', '나이') 또는 열 문자(예: 'B')로 지정합니다. '매출 열 다 더해줘' → column='매출', stat='sum'."""

    column: str = Field(
        ..., description="대상 열의 머리글 이름(예: '매출') 또는 열 문자(예: 'B')"
    )
    stat: Literal["sum", "average", "min", "max", "count"] = Field(
        ...,
        description="계산할 통계: sum(합계), average(평균), min(최소), max(최대), count(숫자 셀 개수)",
    )
    sheet_name: str | None = Field(None, description=_SHEET_DESC)


class FilterRowsParams(BaseModel):
    """조건에 맞는 데이터 행만 남기고 나머지 행을 시트에서 제거합니다. 예: '매출 열에서 500만 이상인 행만 남겨줘' → column='매출', operator='>=', value=5000000. 첫 행은 머리글로 유지됩니다."""

    column: str = Field(
        ..., description="조건을 검사할 열의 머리글 이름(예: '매출') 또는 열 문자(예: 'B')"
    )
    operator: Literal[">", ">=", "<", "<=", "==", "!=", "contains"] = Field(
        ...,
        description="비교 연산자. 숫자 비교는 >, >=, <, <=, 값 일치는 ==/!=, 문자열 포함은 contains",
    )
    value: float | str = Field(
        ..., description="비교 기준값 (예: 5000000 또는 '서울')"
    )
    sheet_name: str | None = Field(None, description=_SHEET_DESC)


class SortRowsParams(BaseModel):
    """지정 열 기준으로 데이터 행을 정렬합니다 (머리글 행 제외). 숫자는 크기순, 문자는 사전순으로 정렬됩니다."""

    column: str = Field(
        ..., description="정렬 기준 열의 머리글 이름 또는 열 문자"
    )
    order: Literal["asc", "desc"] | None = Field(
        None, description="정렬 방향: asc(오름차순, 기본), desc(내림차순)"
    )
    sheet_name: str | None = Field(None, description=_SHEET_DESC)


class DedupeRowsParams(BaseModel):
    """중복된 데이터 행을 제거합니다 (첫 번째 등장 행만 유지). columns를 지정하면 해당 열들의 값이 같을 때 중복으로 판정합니다."""

    columns: list[str] | None = Field(
        None,
        description="중복 판정 기준 열 목록 (머리글 이름 또는 열 문자). 생략하면 모든 열이 같아야 중복으로 봅니다.",
    )
    sheet_name: str | None = Field(None, description=_SHEET_DESC)


class DropColumnParams(BaseModel):
    """지정 열을 테이블에서 삭제합니다. 오른쪽 열들이 왼쪽으로 당겨집니다."""

    column: str = Field(
        ..., description="삭제할 열의 머리글 이름 또는 열 문자"
    )
    sheet_name: str | None = Field(None, description=_SHEET_DESC)


class RenameColumnParams(BaseModel):
    """지정 열의 머리글(1행) 이름을 변경합니다. 데이터는 변경되지 않습니다."""

    column: str = Field(
        ..., description="이름을 바꿀 열의 현재 머리글 이름 또는 열 문자"
    )
    new_name: str = Field(..., description="새 머리글 이름")
    sheet_name: str | None = Field(None, description=_SHEET_DESC)


class AddColumnParams(BaseModel):
    """테이블 오른쪽 끝에 새 열을 추가합니다. formula_a1을 주면 데이터 행 전체에 수식이 채워집니다 (상대 참조는 행별 자동 조정)."""

    name: str = Field(..., description="새 열의 머리글 이름")
    formula_a1: str | None = Field(
        None,
        description="데이터 행에 채울 '=' 시작 수식 (예: '=B2*C2'). 생략하면 머리글만 추가합니다.",
    )
    sheet_name: str | None = Field(None, description=_SHEET_DESC)


class GroupByAggregateParams(BaseModel):
    """그룹별 집계를 계산해 결과만 알려줍니다 (시트는 수정하지 않는 읽기 전용 분석). 예: '지역별 매출 합계 알려줘' → group_column='지역', value_column='매출', agg='sum'."""

    group_column: str = Field(
        ..., description="그룹 기준 열의 머리글 이름 또는 열 문자 (예: '지역')"
    )
    agg: Literal["sum", "average", "min", "max", "count"] = Field(
        ..., description="집계 방법: sum(합계), average(평균), min(최소), max(최대), count(행 개수)"
    )
    value_column: str | None = Field(
        None,
        description="집계 대상 값 열의 머리글 이름 또는 열 문자 (예: '매출'). agg='count'면 생략 가능.",
    )
    sheet_name: str | None = Field(None, description=_SHEET_DESC)


# ── 함수 이름 ↔ 레지스트리 액션 ↔ 파라미터 모델 매핑 (단일 소스) ─────────────

_TOOL_TABLE: list[tuple[str, str, type[BaseModel]]] = [
    ("list_workbooks", "excel_live.list_workbooks", ListWorkbooksParams),
    ("select_workbook", "excel_live.select_workbook", SelectWorkbookParams),
    ("read_range", "excel_live.read_range", ReadRangeParams),
    ("write_range", "excel_live.write_range", WriteRangeParams),
    ("highlight_by_condition", "excel_live.highlight_by_condition", HighlightByConditionParams),
    ("apply_border", "excel_live.apply_border", ApplyBorderParams),
    ("set_formula", "excel_live.set_formula", SetFormulaParams),
    ("save_workbook", "excel_live.save_workbook", SaveWorkbookParams),
    ("calculate_column_stat", "excel_live.calculate_column_stat", CalculateColumnStatParams),
    ("filter_rows", "excel_live.filter_rows", FilterRowsParams),
    ("sort_rows", "excel_live.sort_rows", SortRowsParams),
    ("dedupe_rows", "excel_live.dedupe_rows", DedupeRowsParams),
    ("drop_column", "excel_live.drop_column", DropColumnParams),
    ("rename_column", "excel_live.rename_column", RenameColumnParams),
    ("add_column", "excel_live.add_column", AddColumnParams),
    ("group_by_aggregate", "excel_live.group_by_aggregate", GroupByAggregateParams),
]

_TOOL_NAME_TO_ACTION: dict[str, str] = {name: action for name, action, _ in _TOOL_TABLE}
_ACTION_TO_TOOL_NAME: dict[str, str] = {action: name for name, action, _ in _TOOL_TABLE}
_ACTION_TO_MODEL: dict[str, type[BaseModel]] = {action: model for _, action, model in _TOOL_TABLE}


# ── JSON Schema 생성/정리 ─────────────────────────────────────────────────────


def _clean_schema(node: Any) -> Any:
    """
    model_json_schema() 결과를 소형 모델 친화적으로 정리한다.

    - title/default 제거 (LLM에 노이즈)
    - Optional 래핑(anyOf [X, null]) → X로 축약 (선택 여부는 required가 표현)
    - 단순 타입 유니온(anyOf 전부 {"type": ...}) → {"type": [...]} 배열로 축약
    """
    if isinstance(node, list):
        return [_clean_schema(item) for item in node]
    if not isinstance(node, dict):
        return node

    cleaned = {
        key: _clean_schema(value)
        for key, value in node.items()
        if key not in {"title", "default"}
    }

    any_of = cleaned.pop("anyOf", None)
    if any_of:
        non_null = [s for s in any_of if s.get("type") != "null"]
        if len(non_null) == 1:
            cleaned = {**non_null[0], **cleaned}
        elif non_null and all(set(s.keys()) == {"type"} for s in non_null):
            cleaned["type"] = [s["type"] for s in non_null]
        else:
            cleaned["anyOf"] = any_of
    return cleaned


def _build_tool(name: str, model: type[BaseModel]) -> dict:
    """Pydantic 모델 1개를 OpenAI tools 형식의 function 항목으로 변환한다."""
    description = inspect.getdoc(model) or ""
    parameters = _clean_schema(model.model_json_schema())
    # 모델 docstring이 parameters.description으로 중복 노출되는 것 방지
    parameters.pop("description", None)
    parameters.setdefault("type", "object")
    parameters.setdefault("properties", {})
    parameters.setdefault("required", [])
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": parameters,
        },
    }


EXCEL_TOOL_SCHEMAS: list[dict] = [
    _build_tool(name, model) for name, _, model in _TOOL_TABLE
]


# ── 공개 API ──────────────────────────────────────────────────────────────────


def get_excel_tools() -> list[dict]:
    """LLM에 전달할 OpenAI 호환 tools 배열을 반환한다."""
    return EXCEL_TOOL_SCHEMAS


def tool_name_to_action(tool_name: str) -> str | None:
    """LLM 함수 이름을 tool_registry 액션 이름으로 변환한다 (미등록 시 None)."""
    return _TOOL_NAME_TO_ACTION.get(tool_name)


def action_to_tool_name(action: str) -> str | None:
    """tool_registry 액션 이름을 LLM 함수 이름으로 변환한다 (미등록 시 None)."""
    return _ACTION_TO_TOOL_NAME.get(action)


def validate_tool_params(action: str, params: dict) -> dict:
    """
    LLM이 생성한 인자를 해당 액션의 Pydantic 모델로 검증·정규화한다.

    반환: 검증 통과한 파라미터 dict (None 값은 제거)
    실패: ValueError — 메시지는 LLM에 tool 결과로 재주입 가능하도록 한국어로 요약
    """
    model = _ACTION_TO_MODEL.get(action)
    if model is None:
        raise ValueError(f"알 수 없는 액션입니다: {action}")
    try:
        validated = model(**params)
    except ValidationError as exc:
        problems = []
        for err in exc.errors():
            field = ".".join(str(loc) for loc in err.get("loc", ())) or "(인자)"
            problems.append(f"{field}: {err.get('msg', '유효하지 않음')}")
        raise ValueError(f"인자 검증 실패 — {'; '.join(problems)}") from exc
    return validated.model_dump(exclude_none=True)
