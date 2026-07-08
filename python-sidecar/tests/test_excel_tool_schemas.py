"""Excel 도구 JSON Schema(OpenAI tools 형식) 정합성 테스트."""

from office_claw_sidecar.services.excel_tool_schemas import (
    EXCEL_TOOL_SCHEMAS,
    action_to_tool_name,
    get_excel_tools,
    tool_name_to_action,
)
from office_claw_sidecar.services.tool_registry import PermissionLevel, get_tool


def test_every_schema_follows_openai_function_format():
    assert len(EXCEL_TOOL_SCHEMAS) > 0
    for entry in EXCEL_TOOL_SCHEMAS:
        assert entry["type"] == "function"
        fn = entry["function"]
        assert isinstance(fn["name"], str) and fn["name"]
        # OpenAI 함수 이름 규격: 영문/숫자/언더스코어/하이픈
        assert all(ch.isalnum() or ch in "_-" for ch in fn["name"])
        assert isinstance(fn["description"], str) and fn["description"]
        params = fn["parameters"]
        assert params["type"] == "object"
        assert isinstance(params["properties"], dict)
        required = params.get("required", [])
        assert set(required) <= set(params["properties"].keys())


def test_every_tool_maps_to_registered_action():
    for entry in EXCEL_TOOL_SCHEMAS:
        name = entry["function"]["name"]
        action = tool_name_to_action(name)
        assert action is not None, f"매핑 없는 함수: {name}"
        tool_def = get_tool(action)
        assert tool_def is not None, f"레지스트리에 없는 액션: {action}"
        assert tool_def.permission in {PermissionLevel.SAFE, PermissionLevel.CONFIRM}


def test_tool_name_action_roundtrip():
    for entry in EXCEL_TOOL_SCHEMAS:
        name = entry["function"]["name"]
        action = tool_name_to_action(name)
        assert action_to_tool_name(action) == name
    assert tool_name_to_action("unknown_function") is None
    assert action_to_tool_name("excel_live.unknown") is None


def test_get_excel_tools_returns_full_schema_list():
    tools = get_excel_tools()
    assert tools is EXCEL_TOOL_SCHEMAS
    names = {t["function"]["name"] for t in tools}
    # 사용자 시나리오 핵심 함수 포함 확인
    assert {"read_range", "write_range", "set_formula", "calculate_column_stat"} <= names


def test_write_range_schema_requires_values_2d():
    schema = next(
        t for t in EXCEL_TOOL_SCHEMAS if t["function"]["name"] == "write_range"
    )
    params = schema["function"]["parameters"]
    assert params["required"] == ["values_2d"]
    assert params["properties"]["values_2d"]["type"] == "array"
    assert params["properties"]["values_2d"]["items"]["type"] == "array"


def test_calculate_column_stat_schema_has_stat_enum():
    schema = next(
        t for t in EXCEL_TOOL_SCHEMAS if t["function"]["name"] == "calculate_column_stat"
    )
    params = schema["function"]["parameters"]
    assert set(params["required"]) == {"column", "stat"}
    assert params["properties"]["stat"]["enum"] == ["sum", "average", "min", "max", "count"]


def test_highlight_schema_operator_enum_matches_service_support():
    schema = next(
        t for t in EXCEL_TOOL_SCHEMAS if t["function"]["name"] == "highlight_by_condition"
    )
    operators = schema["function"]["parameters"]["properties"]["operator"]["enum"]
    assert operators == [">", ">=", "<", "<=", "==", "!="]


# ── Pydantic 자동 생성 + 인자 검증 ────────────────────────────────────────────


def test_filter_rows_schema_generated_from_pydantic():
    schema = next(
        t for t in EXCEL_TOOL_SCHEMAS if t["function"]["name"] == "filter_rows"
    )
    params = schema["function"]["parameters"]
    assert params["required"] == ["column", "operator", "value"]
    assert params["properties"]["operator"]["enum"] == [
        ">", ">=", "<", "<=", "==", "!=", "contains",
    ]
    # float|str 유니온이 type 배열로 축약되는지 (소형 모델 친화)
    assert params["properties"]["value"]["type"] == ["number", "string"]
    # title/default 노이즈가 제거되는지
    assert "title" not in params
    assert all("title" not in p for p in params["properties"].values())


def test_transform_tools_are_registered():
    names = {t["function"]["name"] for t in EXCEL_TOOL_SCHEMAS}
    assert {
        "filter_rows", "sort_rows", "dedupe_rows",
        "drop_column", "rename_column", "add_column", "group_by_aggregate",
    } <= names


def test_validate_tool_params_accepts_valid_args():
    from office_claw_sidecar.services.excel_tool_schemas import validate_tool_params

    validated = validate_tool_params(
        "excel_live.filter_rows",
        {"column": "매출", "operator": ">=", "value": 5000000},
    )
    assert validated == {"column": "매출", "operator": ">=", "value": 5000000.0}

    # None 값 필드는 제거된다
    validated = validate_tool_params(
        "excel_live.read_range", {"range_ref": "A1:C3", "sheet_name": None}
    )
    assert validated == {"range_ref": "A1:C3"}


def test_validate_tool_params_rejects_missing_required():
    from office_claw_sidecar.services.excel_tool_schemas import validate_tool_params

    try:
        validate_tool_params("excel_live.write_range", {"start_cell": "C3"})
        assert False, "ValueError expected"
    except ValueError as exc:
        assert "values_2d" in str(exc)


def test_validate_tool_params_rejects_bad_enum():
    from office_claw_sidecar.services.excel_tool_schemas import validate_tool_params

    try:
        validate_tool_params(
            "excel_live.calculate_column_stat",
            {"column": "매출", "stat": "median"},
        )
        assert False, "ValueError expected"
    except ValueError as exc:
        assert "stat" in str(exc)
