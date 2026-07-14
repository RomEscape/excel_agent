"""
Excel Live Agent — 자연어 명령을 Excel Live 작업 액션으로 변환.

우선순위:
  1) 빠른 정규식 규칙 기반 파싱 (대표 시나리오)
  2) LLM JSON 분류 기반 폴백
"""

from __future__ import annotations

import json
import re
from typing import Any

from office_claw_sidecar.services.excel_live_table_presets import match_table_preset


SUPPORTED_ACTIONS = {
    "excel_live.list_workbooks",
    "excel_live.select_workbook",
    "excel_live.list_sheets",
    "excel_live.select_sheet",
    "excel_live.create_sheet",
    "excel_live.read_range",
    "excel_live.write_range",
    "excel_live.create_table",
    "excel_live.highlight_by_condition",
    "excel_live.fill_range",
    "excel_live.apply_border",
    "excel_live.set_formula",
    "excel_live.verify_formula_result",
    "excel_live.sort_range",
    "excel_live.filter_rows",
    "excel_live.dedupe_rows",
    "excel_live.pivot_table",
    "excel_live.create_chart",
    "excel_live.validate_data",
    "excel_live.save_workbook",
}


def _has_likely_edit_intent(message: str) -> bool:
    lowered = str(message or "").lower()
    token_hit = any(
        token in lowered
        for token in [
            "경계선",
            "테두리",
            "border",
            "입력",
            "작성",
            "적용",
            "수식",
            "함수",
            "계산",
            "검증",
            "강조",
            "칠해",
            "배경",
            "배경색",
            "색",
            "노란색",
            "빨간색",
            "파란색",
            "초록색",
            "표",
            "테이블",
            "table",
            "저장",
            "만들어",
            "만들",
            "생성",
            "채워",
            "바꿔",
            "정렬",
            "필터",
            "중복",
            "피벗",
            "차트",
            "그래프",
            "검증",
        ]
    )
    if token_hit:
        return True

    # "A열에서 10 이상인 셀" 같은 조건부 문장을 편집 의도로 인식한다.
    has_numeric_condition = bool(
        re.search(r"(-?\d+(?:\.\d+)?)\s*(이상|초과|이하|미만|보다\s*크|보다\s*작|>=|<=|>|<|==|!=)", lowered)
    )
    has_cell_target = bool(re.search(r"([a-z]\s*열|[a-z]+\d+:[a-z]+\d+|[a-z]+\d+)", lowered, re.IGNORECASE))
    return has_numeric_condition and has_cell_target


def _extract_range_ref(text: str) -> str | None:
    match = re.search(r"\b([a-z]+\d+:[a-z]+\d+|[a-z]:[a-z]|[a-z]+\d+)\b", text, re.IGNORECASE)
    if not match:
        # 한국어 조사(에/을/를/은/는/으로/에서)가 붙은 경우도 범위를 인식한다.
        match = re.search(
            r"([a-z]+\d+:[a-z]+\d+|[a-z]:[a-z]|[a-z]+\d+)\s*(?:에|을|를|은|는|으로|에서)",
            text,
            re.IGNORECASE,
        )
    if not match:
        return None
    return match.group(1).upper()


def _extract_target_range_from_text(text: str) -> str | None:
    explicit = _extract_range_ref(text)
    if explicit:
        return explicit
    col_match = re.search(r"\b([a-z])\s*열\b", text, re.IGNORECASE)
    if col_match:
        col = col_match.group(1).upper()
        return f"{col}:{col}"
    return None


def _normalize_color(word: str) -> str:
    normalized = word.strip().lower()
    if normalized in {"노란색", "노랑", "노란", "yellow"}:
        return "#FFFF00"
    if normalized in {"빨간색", "빨강", "빨간", "red"}:
        return "#FF0000"
    if normalized in {"초록색", "초록", "초록색으로", "green"}:
        return "#00FF00"
    if normalized in {"파란색", "파랑", "blue"}:
        return "#0000FF"
    return "#FFFF00"


def _parse_operator_threshold(text: str) -> tuple[str, float] | None:
    op_map = {
        "이상": ">=",
        "초과": ">",
        "이하": "<=",
        "미만": "<",
        "같음": "==",
        "같지 않음": "!=",
        "크거나 같": ">=",
        "작거나 같": "<=",
        "보다 크": ">",
        "보다 작": "<",
        "greater than or equal": ">=",
        "less than or equal": "<=",
        "greater than": ">",
        "less than": "<",
    }
    # 50 이상 / 50 초과 / ...
    m = re.search(r"(-?\d+(?:\.\d+)?)\s*(이상|초과|이하|미만|같음|같지 않음)", text)
    if m:
        return op_map[m.group(2)], float(m.group(1))
    # 50보다 크다 / 50보다 작다
    m = re.search(r"(-?\d+(?:\.\d+)?)\s*보다\s*(크거나\s*같|작거나\s*같|큰|작은|크|작)", text)
    if m:
        key = m.group(2).replace(" ", "")
        key_map = {
            "크": ">",
            "작": "<",
            "큰": ">",
            "작은": "<",
            "크거나같": ">=",
            "작거나같": "<=",
        }
        operator = key_map.get(key)
        if operator:
            return operator, float(m.group(1))
    # > 50 / >= 50 / <= 10 / != 0
    m = re.search(r"(>=|<=|>|<|==|!=)\s*(-?\d+(?:\.\d+)?)", text)
    if m:
        return m.group(1), float(m.group(2))
    return None


def _parse_literal_value(raw: str) -> Any:
    text = raw.strip().strip("\"'")
    lowered = text.lower()
    if lowered in {"true", "yes"}:
        return True
    if lowered in {"false", "no"}:
        return False
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    if re.fullmatch(r"-?\d+\.\d+", text):
        return float(text)
    return text


def _build_formula_from_function(func_word: str, source_range: str) -> str:
    normalized = func_word.strip().lower()
    func_map = {
        "합계": "SUM",
        "합": "SUM",
        "sum": "SUM",
        "평균": "AVERAGE",
        "average": "AVERAGE",
        "avg": "AVERAGE",
        "최대": "MAX",
        "max": "MAX",
        "최소": "MIN",
        "min": "MIN",
        "개수": "COUNT",
        "카운트": "COUNT",
        "count": "COUNT",
    }
    fn = func_map.get(normalized, "SUM")
    return f"={fn}({source_range})"


def _range_to_empty_payload(range_ref: str) -> tuple[str, list[list[Any]]]:
    """
    A1 또는 A1:C3 범위를 write_range 입력 형태(start_cell + values_2d)로 변환.
    지우기 명령은 None 매트릭스를 써서 기존 값을 clear한다.
    """
    normalized = range_ref.strip().upper()
    if ":" not in normalized:
        return normalized, [[None]]

    left, right = normalized.split(":")
    left_col_m = re.match(r"([A-Z]+)(\d+)", left)
    right_col_m = re.match(r"([A-Z]+)(\d+)", right)
    if not left_col_m or not right_col_m:
        return left, [[None]]

    start_col, start_row = left_col_m.group(1), int(left_col_m.group(2))
    end_col, end_row = right_col_m.group(1), int(right_col_m.group(2))
    row_count = max(1, end_row - start_row + 1)
    col_count = _column_span(start_col, end_col)
    values_2d = [[None for _ in range(col_count)] for _ in range(row_count)]
    return left, values_2d


def parse_command_rule_based(message: str, *, context_range: str | None = None) -> dict[str, Any] | None:
    text = message.strip()
    lowered = text.lower()
    write_verbs = r"(입력(?:해(?:줘)?)?|써(?:줘)?|작성(?:해(?:줘)?)?|적어(?:줘)?|넣어(?:줘)?|write|set|input|fill)"
    border_words = r"(경계선|테두리|border|보더)"
    border_apply_words = r"(적용|넣|추가|만들|그려|draw|apply|set)"

    if any(
        token in lowered
        for token in [
            "열린 엑셀",
            "워크북 목록",
            "열린 파일 목록",
            "열린 통합문서",
            "열려 있는 엑셀 파일",
            "workbook list",
            "list workbooks",
        ]
    ):
        return {
            "action": "excel_live.list_workbooks",
            "params": {},
            "reason": "열린 통합문서 목록 조회 요청",
        }

    if any(
        token in lowered
        for token in [
            "시트 목록",
            "탭 목록",
            "현재 시트",
            "시트들",
            "sheet list",
            "list sheets",
            "worksheet list",
        ]
    ):
        return {
            "action": "excel_live.list_sheets",
            "params": {},
            "reason": "시트 목록 조회 요청",
        }

    create_sheet_match = re.search(
        r"([^\s,]+)\s*(?:시트|sheet)\s*(?:만들|생성|추가|create|add)",
        text,
        re.IGNORECASE,
    )
    if create_sheet_match:
        sheet_name = str(create_sheet_match.group(1)).strip().strip("\"'")
        if sheet_name:
            return {
                "action": "excel_live.create_sheet",
                "params": {"sheet_name": sheet_name, "make_active": True},
                "reason": "새 시트 생성 요청",
            }

    select_sheet_match = re.search(
        r"([^\s,]+)\s*(?:시트|sheet)\s*(?:로|으로)?\s*(?:이동|전환|선택|활성화|switch|go)",
        text,
        re.IGNORECASE,
    )
    if select_sheet_match:
        sheet_name = str(select_sheet_match.group(1)).strip().strip("\"'")
        if sheet_name:
            return {
                "action": "excel_live.select_sheet",
                "params": {"sheet_name": sheet_name},
                "reason": "작업 시트 전환 요청",
            }

    # 예: "워크북 text_1.xlsx 선택", "select workbook text_1.xlsx"
    select_match = re.search(
        r"(?:워크북|통합문서|파일|workbook)\s+([^\s]+\.xlsx|[^\s]+)\s*(?:선택|전환|열어|열기|select|switch)",
        text,
        re.IGNORECASE,
    )
    if select_match:
        target = select_match.group(1).strip().strip("\"'")
        return {
            "action": "excel_live.select_workbook",
            "params": {"workbook_id": target},
            "reason": "대상 워크북 선택 요청",
        }

    # 예: "C3 셀 내용 지워줘", "B2:D3 비워줘", "clear A1"
    clear_match = re.search(
        r"\b([a-z]+\d+:[a-z]+\d+|[a-z]+\d+)\b[^\n]*(지워|지우|삭제|비워|clear|erase|reset)",
        text,
        re.IGNORECASE,
    )
    if clear_match:
        target_range = clear_match.group(1).upper()
        start_cell, values_2d = _range_to_empty_payload(target_range)
        return {
            "action": "excel_live.write_range",
            "params": {"start_cell": start_cell, "values_2d": values_2d},
            "reason": "셀 값 삭제 요청",
        }

    # 예: "5 * 5 표 만들어줘", "3x4 테이블 생성"
    table_match = re.search(
        r"(\d{1,3})\s*(?:\*|x|×)\s*(\d{1,3})\s*(?:표|테이블|table)",
        lowered,
        re.IGNORECASE,
    )
    if not table_match:
        table_match = re.search(
            r"(\d{1,3})\s*행\s*(\d{1,3})\s*열\s*(?:표|테이블|table)",
            lowered,
            re.IGNORECASE,
        )
    if table_match and re.search(r"(만들|생성|작성|create|make)", lowered):
        rows = max(1, min(100, int(table_match.group(1))))
        cols = max(1, min(50, int(table_match.group(2))))
        return {
            "action": "excel_live.create_table",
            "params": {
                "start_cell": "__ACTIVE_CELL__",
                "rows": rows,
                "cols": cols,
                "with_border": True,
            },
            "reason": "선택 셀 기준 표 생성 요청",
        }

    # "테두리 넣어줘"가 값 쓰기(write_range)로 잘못 분류되지 않도록
    # 경계선 의도를 읽기/쓰기보다 우선 판별한다.
    if re.search(border_words, lowered) and re.search(border_apply_words, lowered):
        target_range = _extract_target_range_from_text(lowered) or context_range or "__ACTIVE_SELECTION__"
        # 사용자 체감상 "적용이 안 됨"을 줄이기 위해 기본을 눈에 띄는 medium으로 둔다.
        weight = "medium"
        if re.search(r"(얇게|얇은|thin)", lowered):
            weight = "thin"
        elif re.search(r"(굵게|두껍|thick|굵은)", lowered):
            weight = "medium"
        # 기본 색상은 검정으로 사용해 Excel 기본 격자선보다 확실히 보이게 한다.
        color = "#000000"
        if re.search(r"(검정|검은|black)", lowered):
            color = "#000000"
        return {
            "action": "excel_live.apply_border",
            "params": {
                "target_range": target_range,
                "line_style": "continuous",
                "weight": weight,
                "color": color,
            },
            "reason": "선택 범위 경계선 적용 요청",
        }

    # 예: "A1:C10 읽어줘", "B열 보여줘"
    read_verbs = r"(읽어|읽기|보여|조회|확인|read|show|display)"
    if re.search(read_verbs, lowered) and not re.search(write_verbs, lowered):
        range_ref = _extract_target_range_from_text(lowered)
        if not range_ref:
            range_ref = context_range or "__ACTIVE_SELECTION__"
        return {
            "action": "excel_live.read_range",
            "params": {"range_ref": range_ref},
            "reason": "범위 읽기 요청",
        }

    # 예: "C1에 A1:A10 합계 수식 넣어줘", "set sum formula in C1 from A1:A10"
    formula_template = re.search(
        r"([a-z]+\d+)\s*에\s*([a-z]+\d+:[a-z]+\d+)\s*(합계|합|평균|average|avg|sum|최대|max|최소|min|개수|카운트|count).*(수식|formula|넣|적용|set)",
        lowered,
    )
    if formula_template:
        target_cell = formula_template.group(1).upper()
        source_range = formula_template.group(2).upper()
        func_word = formula_template.group(3)
        return {
            "action": "excel_live.set_formula",
            "params": {
                "range_ref": target_cell,
                "formula_a1": _build_formula_from_function(func_word, source_range),
            },
            "reason": "집계 함수 수식 생성 요청",
        }

    # 예: "A열 50보다 큰 값 노란색으로 칠해줘", "A1:A20 >= 100 highlight"
    if re.search(r"(칠해|강조|표시|highlight|색|채워|배경|바꿔)", lowered):
        op_threshold = _parse_operator_threshold(lowered)
        color_match = re.search(
            r"(노란색|노랑|yellow|빨간색|빨강|red|초록색|초록|green|파란색|파랑|blue)",
            lowered,
        )
        # 예: "표 색을 전반적으로 노랗게 칠해줘"처럼 조건 없는 전체 채우기
        if op_threshold is None:
            broad_intent = bool(
                re.search(r"(전반|전체|전체적|모든|표\s*색|배경색|통으로)", lowered)
            )
            target_range = _extract_target_range_from_text(lowered) or (
                context_range or "__ACTIVE_SELECTION__" if broad_intent else "A:Z"
            )
            return {
                "action": "excel_live.fill_range",
                "params": {
                    "target_range": target_range,
                    "fill_color": _normalize_color(color_match.group(1)) if color_match else "#FFFF00",
                },
                "reason": "범위 전체 배경색 적용 요청",
            }

        # 범위를 특정하지 않은 강조 명령은 A:A로 좁게 잡으면 체감상 "안 된다"가 되기 쉽다.
        # 기본값을 A:Z로 넓혀 일반 표(좌측 영역)에서 자연어 명령이 더 잘 동작하게 한다.
        target_range = _extract_target_range_from_text(lowered) or "A:Z"
        if op_threshold:
            operator, threshold = op_threshold
            color = _normalize_color(color_match.group(1)) if color_match else "#FFFF00"
            return {
                "action": "excel_live.highlight_by_condition",
                "params": {
                    "target_range": target_range,
                    "operator": operator,
                    "threshold": threshold,
                    "fill_color": color,
                },
                "reason": "조건부 강조 요청",
            }

    # 예: "B2:D2에 헤더 써줘"
    header_match = re.search(
        r"([a-z]+\d+:[a-z]+\d+)\s*에?.*(헤더|header).*(써|작성|입력|write|set|fill)",
        lowered,
    )
    if not header_match:
        range_match = re.search(r"([a-z]+\d+:[a-z]+\d+)", lowered)
        has_header = bool(re.search(r"(헤더|header)", lowered))
        has_write_verb = bool(re.search(write_verbs, lowered))
        if range_match and has_header and has_write_verb:
            range_ref = range_match.group(1).upper()
            left, right = range_ref.split(":")
            left_col = re.match(r"([A-Z]+)\d+", left)
            right_col = re.match(r"([A-Z]+)\d+", right)
            row_match = re.match(r"[A-Z]+(\d+)", left)
            if left_col and right_col and row_match:
                start_col = left_col.group(1)
                end_col = right_col.group(1)
                row_no = row_match.group(1)
                col_count = _column_span(start_col, end_col)
                values = [[f"헤더{i+1}" for i in range(col_count)]]
                return {
                    "action": "excel_live.write_range",
                    "params": {
                        "start_cell": f"{start_col}{row_no}",
                        "values_2d": values,
                    },
                    "reason": "헤더 행 입력 요청",
                }
    if header_match:
        range_ref = header_match.group(1).upper()
        left, right = range_ref.split(":")
        left_col = re.match(r"([A-Z]+)\d+", left)
        right_col = re.match(r"([A-Z]+)\d+", right)
        row_match = re.match(r"[A-Z]+(\d+)", left)
        if left_col and right_col and row_match:
            start_col = left_col.group(1)
            end_col = right_col.group(1)
            row_no = row_match.group(1)
            col_count = _column_span(start_col, end_col)
            values = [[f"헤더{i+1}" for i in range(col_count)]]
            return {
                "action": "excel_live.write_range",
                "params": {
                    "start_cell": f"{start_col}{row_no}",
                    "values_2d": values,
                },
                "reason": "헤더 행 입력 요청",
            }

    # 예: "B2:D2에 이름,수량,금액 입력"
    row_write = re.search(
        r"\b([a-z]+\d+:[a-z]+\d+)\s*에\s*([^\n]+?)\s*(입력(?:해)?|써|작성|적어|넣어|write|set)\b",
        text,
        re.IGNORECASE,
    )
    if row_write and "헤더" not in lowered and "header" not in lowered:
        range_ref = row_write.group(1).upper()
        left, right = range_ref.split(":")
        left_col = re.match(r"([A-Z]+)\d+", left)
        right_col = re.match(r"([A-Z]+)\d+", right)
        row_match = re.match(r"[A-Z]+(\d+)", left)
        if left_col and right_col and row_match:
            raw_values = row_write.group(2).strip()
            # 수식 명령은 write_range가 아니라 set_formula 경로로 내려가야 한다.
            if (
                "수식" in raw_values
                or "formula" in raw_values.lower()
                or "=" in raw_values
            ):
                pass
            else:
                tokens = [t.strip() for t in re.split(r"[,/|]", raw_values) if t.strip()]
                col_count = _column_span(left_col.group(1), right_col.group(1))
                if tokens:
                    row_values = [_parse_literal_value(t) for t in tokens[:col_count]]
                    if len(row_values) < col_count:
                        row_values.extend([""] * (col_count - len(row_values)))
                    return {
                        "action": "excel_live.write_range",
                        "params": {"start_cell": left, "values_2d": [row_values]},
                        "reason": "행 범위 값 입력 요청",
                    }

    # 예: "A1에 120 입력", "C3 셀에 777 입력해줘", "C3 값을 777로 입력", "C3 777 입력"
    single_write_patterns = [
        r"([a-z]+\d+)\s*(?:셀)?\s*에\s*(?:값\s*)?['\"]?([^'\"]+?)['\"]?\s*(?:을|를)?\s*(?:로)?\s*(입력(?:해(?:줘)?)?|써(?:줘)?|작성(?:해(?:줘)?)?|적어(?:줘)?|넣어(?:줘)?|write|set|input)",
        r"([a-z]+\d+)\s*(?:셀)?\s*값(?:을|를)?\s*['\"]?([^'\"]+?)['\"]?\s*(?:로)?\s*(입력(?:해(?:줘)?)?|써(?:줘)?|작성(?:해(?:줘)?)?|적어(?:줘)?|넣어(?:줘)?|write|set|input)",
        r"\b([a-z]+\d+)\s+['\"]?([^'\"]+?)['\"]?\s*(입력(?:해(?:줘)?)?|써(?:줘)?|작성(?:해(?:줘)?)?|적어(?:줘)?|넣어(?:줘)?|write|set|input)\b",
    ]
    for pattern in single_write_patterns:
        single_write = re.search(pattern, text, re.IGNORECASE)
        if not single_write:
            continue
        cell = single_write.group(1).upper()
        raw_value = re.sub(r"\s*(?:값|value)\s*$", "", single_write.group(2).strip(), flags=re.IGNORECASE)
        if "수식" in raw_value or "formula" in raw_value.lower() or "=" in raw_value:
            continue
        value = _parse_literal_value(raw_value)
        return {
            "action": "excel_live.write_range",
            "params": {"start_cell": cell, "values_2d": [[value]]},
            "reason": "단일 셀 값 입력 요청",
        }

    # 예: "777 입력해줘" (셀 미지정) -> 현재 선택 셀에 기록
    implicit_single_write = re.search(
        r"^\s*['\"]?([^'\"]+?)['\"]?\s*(입력(?:해(?:줘)?)?|써(?:줘)?|작성(?:해(?:줘)?)?|적어(?:줘)?|넣어(?:줘)?|write|set|input)\s*$",
        text,
        re.IGNORECASE,
    )
    if (
        implicit_single_write
        and not re.search(r"(수식|formula|헤더|header|색|highlight|강조|표시|열|column|row)", lowered)
    ):
        raw_value = implicit_single_write.group(1).strip()
        if "수식" not in raw_value and "formula" not in raw_value.lower() and "=" not in raw_value:
            value = _parse_literal_value(raw_value)
            return {
                "action": "excel_live.write_range",
                "params": {"start_cell": "__ACTIVE_CELL__", "values_2d": [[value]]},
                "reason": "선택 셀 값 입력 요청",
            }

    # 예: "C7 777 set", "B2 done write"
    single_write_en = re.search(
        r"\b([a-z]+\d+)\s+['\"]?([^'\"]+?)['\"]?\s+(?:write|set|input)\b",
        text,
        re.IGNORECASE,
    )
    if single_write_en:
        cell = single_write_en.group(1).upper()
        raw_value = single_write_en.group(2).strip()
        if "formula" not in raw_value.lower() and "=" not in raw_value:
            value = _parse_literal_value(raw_value)
            return {
                "action": "excel_live.write_range",
                "params": {"start_cell": cell, "values_2d": [[value]]},
                "reason": "영문 단일 셀 값 입력 요청",
            }

    # 예: "A1:B10에 수식 =SUM(C1:C10) 적용"
    formula_match = re.search(
        r"([a-z]+\d+:[a-z]+\d+|[a-z]+\d+).*(수식|formula).*(=[^\n]+?)\s*(?:적용|넣|써|set|$)",
        text,
        re.IGNORECASE,
    )
    if formula_match:
        formula_a1 = re.sub(
            r"\s*(?:적용(?:해줘)?|넣(?:어줘)?|써(?:줘)?|set)\s*$",
            "",
            formula_match.group(3).strip(),
            flags=re.IGNORECASE,
        )
        return {
            "action": "excel_live.set_formula",
            "params": {
                "range_ref": formula_match.group(1).upper(),
                "formula_a1": formula_a1,
            },
            "reason": "범위 수식 적용 요청",
        }

    # 예: "수식 =SUM(A1:A10) 적용" (범위 미지정) -> 현재 선택 범위에 적용
    implicit_formula = re.search(
        r"(수식|formula).*(=[^\n]+?)\s*(?:적용(?:해줘)?|넣(?:어줘)?|써(?:줘)?|set|$)",
        text,
        re.IGNORECASE,
    )
    if implicit_formula:
        formula_a1 = re.sub(
            r"\s*(?:적용(?:해줘)?|넣(?:어줘)?|써(?:줘)?|set)\s*$",
            "",
            implicit_formula.group(2).strip(),
            flags=re.IGNORECASE,
        )
        return {
            "action": "excel_live.set_formula",
            "params": {"range_ref": "__ACTIVE_SELECTION__", "formula_a1": formula_a1},
            "reason": "선택 범위 수식 적용 요청",
        }

    # 예: "엑셀 저장해줘", "save workbook"
    if re.search(r"(저장(?:해(?:줘)?)?|save)", lowered):
        return {
            "action": "excel_live.save_workbook",
            "params": {},
            "reason": "통합문서 저장 요청",
        }

    return None


async def parse_command_with_llm(message: str, llm_service) -> dict[str, Any]:
    prompt = (
        "너는 Excel Live 작업 분류기다. 사용자 메시지를 아래 JSON으로만 반환해라.\n"
        "허용 action:\n"
        "- excel_live.list_workbooks\n"
        "- excel_live.select_workbook\n"
        "- excel_live.list_sheets\n"
        "- excel_live.select_sheet\n"
        "- excel_live.create_sheet\n"
        "- excel_live.read_range\n"
        "- excel_live.write_range\n"
        "- excel_live.create_table\n"
        "- excel_live.highlight_by_condition\n"
        "- excel_live.fill_range\n"
        "- excel_live.save_workbook\n\n"
        "- excel_live.apply_border\n"
        "- excel_live.set_formula\n\n"
        "- excel_live.verify_formula_result\n\n"
        "- excel_live.sort_range\n"
        "- excel_live.filter_rows\n"
        "- excel_live.dedupe_rows\n"
        "- excel_live.pivot_table\n"
        "- excel_live.create_chart\n"
        "- excel_live.validate_data\n\n"
        "규칙:\n"
        "1) JSON 외 텍스트 금지\n"
        "2) action은 허용 목록 중 하나\n"
        "3) params에는 action에 필요한 값만 넣는다\n"
        "4) 모호하면 excel_live.list_workbooks를 반환\n\n"
        "출력 형식:\n"
        '{"action":"excel_live.read_range","params":{"range_ref":"A1:B10"},"reason":"한 줄 한국어"}\n\n'
        f"사용자 메시지: {message}"
    )
    raw = await llm_service.chat([{"role": "user", "content": prompt}])
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        raise ValueError("LLM JSON 파싱 실패")
    parsed = json.loads(match.group(0))
    action = str(parsed.get("action", "")).strip()
    if action not in SUPPORTED_ACTIONS:
        raise ValueError(f"지원하지 않는 action: {action}")
    params = parsed.get("params", {})
    if not isinstance(params, dict):
        params = {}
    return {"action": action, "params": params, "reason": parsed.get("reason", "")}


def _ensure_action_step(step: dict[str, Any]) -> dict[str, Any]:
    action = str(step.get("action", "")).strip()
    if action not in SUPPORTED_ACTIONS:
        raise ValueError(f"지원하지 않는 action: {action}")
    params = step.get("params", {})
    if not isinstance(params, dict):
        params = {}
    reason = str(step.get("reason", "")).strip()
    return {"action": action, "params": params, "reason": reason}


async def parse_command_plan_with_llm(
    message: str,
    llm_service,
    *,
    context: dict[str, Any] | None = None,
    forbid_list_action: bool = False,
    require_edit_action: bool = False,
) -> dict[str, Any]:
    """
    LLM 기반 계획형 파서.

    출력은 action_plan 배열을 우선으로 사용한다.
    - action_plan: [{"action": "...", "params": {...}, "reason": "..."}]
    """
    context = context or {}
    context_range = str(context.get("context_range", "") or "").strip().upper()
    workbook_id = str(context.get("workbook_id", "") or "").strip()
    sheet_name = str(context.get("sheet_name", "") or "").strip()
    reasoning_mode = str(context.get("reasoning_mode", "fast") or "").strip().lower()
    if reasoning_mode not in {"fast", "deep", "reflect"}:
        reasoning_mode = "fast"
    complexity_score = context.get("complexity_score", 0)
    try:
        complexity_score_int = int(complexity_score)
    except Exception:
        complexity_score_int = 0
    reflection_note = str(context.get("reflection_note", "") or "").strip()
    previous_first_action = str(context.get("previous_first_action", "") or "").strip()
    context_line = (
        f"최근 컨텍스트: workbook_id={workbook_id or 'auto'}, sheet={sheet_name or 'auto'}, "
        f"context_range={context_range or 'none'}\n"
    )
    if reasoning_mode == "deep":
        reasoning_line = (
            "추가 지침(Deep reasoning): 복잡 명령이다. 실행 전 내부적으로 단계/의존성을 점검하고 "
            "누락 파라미터를 보수적으로 채운 뒤 JSON만 반환하라.\n"
            f"복잡도 점수={complexity_score_int}\n"
        )
    elif reasoning_mode == "reflect":
        reasoning_line = (
            "추가 지침(Reflection 1회): 이전 저신뢰 계획을 교정하는 재해석 단계다. "
            "원인(의도 불일치/수동 액션 과다)을 피해서 더 직접적인 실행 계획으로 수정하라.\n"
            f"reflection_note={reflection_note or 'none'}, previous_first_action={previous_first_action or 'none'}\n"
        )
    else:
        reasoning_line = (
            "추가 지침(Fast path): 단순 명령 우선, 불필요한 단계 분해를 피하고 최소 단계로 계획하라.\n"
            f"복잡도 점수={complexity_score_int}\n"
        )
    prompt = (
        "너는 Excel Live 작업 플래너다. 사용자 메시지를 실행 계획 JSON으로만 반환해라.\n"
        "허용 action:\n"
        "- excel_live.list_workbooks\n"
        "- excel_live.select_workbook\n"
        "- excel_live.list_sheets\n"
        "- excel_live.select_sheet\n"
        "- excel_live.create_sheet\n"
        "- excel_live.read_range\n"
        "- excel_live.write_range\n"
        "- excel_live.create_table\n"
        "- excel_live.highlight_by_condition\n"
        "- excel_live.fill_range\n"
        "- excel_live.save_workbook\n"
        "- excel_live.apply_border\n"
        "- excel_live.set_formula\n\n"
        "- excel_live.verify_formula_result\n\n"
        "- excel_live.sort_range\n"
        "- excel_live.filter_rows\n"
        "- excel_live.dedupe_rows\n"
        "- excel_live.pivot_table\n"
        "- excel_live.create_chart\n"
        "- excel_live.validate_data\n\n"
        "규칙:\n"
        "1) JSON 외 텍스트 금지\n"
        "2) action_plan은 1~4개 단계\n"
        "3) 각 단계는 action/params/reason 포함\n"
        "4) 범위가 없으면 __ACTIVE_SELECTION__ 또는 __ACTIVE_CELL__ 사용 가능\n"
        "4-1) context_range가 주어졌고 사용자가 '이 범위/여기/전반적으로'처럼 모호하게 말하면 context_range를 우선 사용\n"
        "5) plan 상위에 intent를 반드시 포함한다: edit | read | navigate\n"
        "6) intent=edit이면 첫 단계는 편집 action이어야 한다\n"
        "   (write_range/create_table/highlight_by_condition/fill_range/apply_border/set_formula/sort_range/filter_rows/dedupe_rows/pivot_table/create_chart/save_workbook)\n"
        f"6) forbid_list_action={str(bool(forbid_list_action)).lower()} 일 때 첫 단계를 excel_live.list_workbooks로 반환하면 안 된다\n\n"
        f"7) require_edit_action={str(bool(require_edit_action)).lower()} 일 때 첫 단계는 반드시 편집 액션이어야 한다\n"
        "   (편집 액션: write_range/create_table/highlight_by_condition/fill_range/apply_border/set_formula/sort_range/filter_rows/dedupe_rows/pivot_table/create_chart/save_workbook)\n\n"
        "8) create_table 정보가 부족하면 slot_fill/partial_params/follow_up_question를 함께 넣을 수 있다\n"
        "   - slot_fill 예: {\"rows\":5,\"cols\":5,\"headers\":[\"금액\",\"장소\"]}\n"
        "   - follow_up_question 예: \"표 크기와 헤더를 알려주세요. 예: 5*5, 금액, 장소, 날짜\"\n\n"
        f"{context_line}"
        f"{reasoning_line}"
        "출력 형식:\n"
        '{"intent":"edit","mutates_workbook":true,"action_plan":[{"action":"excel_live.fill_range","params":{"target_range":"__ACTIVE_SELECTION__","fill_color":"#FFFF00"},"reason":"범위 배경색 변경"}],"slot_fill":{},"partial_params":{},"follow_up_question":"","reason":"한 줄 한국어"}\n\n'
        f"사용자 메시지: {message}"
    )
    raw = await llm_service.chat([{"role": "user", "content": prompt}])
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        raise ValueError("LLM 계획 JSON 파싱 실패")
    parsed = json.loads(match.group(0))

    steps_raw = parsed.get("action_plan")
    if isinstance(steps_raw, list) and steps_raw:
        action_plan = []
        for raw_step in steps_raw[:4]:
            if isinstance(raw_step, dict):
                action_plan.append(_ensure_action_step(raw_step))
        if not action_plan:
            raise ValueError("LLM action_plan이 비어 있습니다.")
        intent = str(parsed.get("intent", "")).strip().lower()
        if intent not in {"edit", "read", "navigate"}:
            intent = "unknown"
        return {
            "action_plan": action_plan,
            "reason": str(parsed.get("reason", "")).strip(),
            "intent": intent,
            "mutates_workbook": bool(parsed.get("mutates_workbook", intent == "edit")),
            "slot_fill": parsed.get("slot_fill") if isinstance(parsed.get("slot_fill"), dict) else {},
            "partial_params": (
                parsed.get("partial_params") if isinstance(parsed.get("partial_params"), dict) else {}
            ),
            "follow_up_question": str(parsed.get("follow_up_question", "")).strip(),
        }

    # 하위 호환: action/params 단일 형태도 수용
    single = _ensure_action_step(parsed)
    intent = str(parsed.get("intent", "")).strip().lower()
    if intent not in {"edit", "read", "navigate"}:
        intent = "unknown"
    return {
        "action_plan": [single],
        "reason": str(parsed.get("reason", "")).strip(),
        "intent": intent,
        "mutates_workbook": bool(parsed.get("mutates_workbook", intent == "edit")),
        "slot_fill": parsed.get("slot_fill") if isinstance(parsed.get("slot_fill"), dict) else {},
        "partial_params": parsed.get("partial_params") if isinstance(parsed.get("partial_params"), dict) else {},
        "follow_up_question": str(parsed.get("follow_up_question", "")).strip(),
    }


async def parse_excel_live_command(
    message: str,
    llm_service,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    context = context or {}
    lowered = str(message or "").lower()
    non_edit_actions = {
        "excel_live.list_workbooks",
        "excel_live.select_workbook",
        "excel_live.read_range",
        "excel_live.validate_data",
    }
    # 에이전트 단일 경로: 규칙 파서 없이 LLM 플래너만 사용한다.
    try:
        planned = await parse_command_plan_with_llm(
            message,
            llm_service,
            context=context,
        )
        action_plan = planned["action_plan"]
        intent = str(planned.get("intent", "unknown")).lower()
        explicit_list_intent = any(
            token in lowered
            for token in [
                "열린 통합문서",
                "워크북 목록",
                "열린 파일 목록",
                "list workbooks",
                "workbook list",
            ]
        )
        likely_edit_intent = intent == "edit" or (
            intent == "unknown" and _has_likely_edit_intent(message)
        )
        # 명시적 목록 조회가 아닌데 list_workbooks가 나오면 1회 재계획한다.
        if (
            not explicit_list_intent
            and action_plan
            and action_plan[0].get("action") == "excel_live.list_workbooks"
        ):
            planned = await parse_command_plan_with_llm(
                message,
                llm_service,
                context=context,
                forbid_list_action=True,
            )
            action_plan = planned["action_plan"]
            if (
                action_plan
                and action_plan[0].get("action") == "excel_live.list_workbooks"
                and likely_edit_intent
            ):
                raise ValueError("편집 의도 명령을 목록 조회로 해석했습니다.")
        # 편집 의도인데 관찰/조회 액션으로 끝나면 강제 재계획한다.
        if likely_edit_intent and action_plan and action_plan[0].get("action") in non_edit_actions:
            planned = await parse_command_plan_with_llm(
                message,
                llm_service,
                context=context,
                forbid_list_action=True,
                require_edit_action=True,
            )
            action_plan = planned["action_plan"]
            if action_plan and action_plan[0].get("action") in non_edit_actions:
                raise ValueError("편집 의도 명령을 편집 액션으로 계획하지 못했습니다.")
        first = action_plan[0]
        return {
            "action_plan": action_plan,
            "action": first["action"],
            "params": first["params"],
            "reason": planned.get("reason", "") or first.get("reason", ""),
            "intent": intent,
            "slot_fill": planned.get("slot_fill", {}),
            "partial_params": planned.get("partial_params", {}),
            "follow_up_question": planned.get("follow_up_question", ""),
        }
    except Exception as exc:
        raise ValueError(
            "엑셀 명령을 해석하지 못했습니다. 범위/동작을 함께 적어주세요. 예: 'B2:D5 범위에 경계선 적용해줘'"
        ) from exc


def _column_span(start_col: str, end_col: str) -> int:
    def col_to_num(col: str) -> int:
        n = 0
        for ch in col:
            n = n * 26 + (ord(ch) - ord("A") + 1)
        return n

    start = col_to_num(start_col)
    end = col_to_num(end_col)
    return max(1, end - start + 1)


_DATE_TOKEN_PATTERN = re.compile(r"\b\d{2,4}[./-]\d{1,2}[./-]\d{1,2}\b")


def _clean_table_cell(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def _split_table_line(line: str, *, allow_comma: bool = False) -> list[str]:
    raw = str(line or "").strip()
    if not raw:
        return []
    parts: list[str] | None = None
    if "\t" in raw:
        parts = raw.split("\t")
    elif "|" in raw:
        parts = raw.split("|")
    elif allow_comma and "," in raw:
        parts = re.split(r"\s*,\s*", raw)
    if parts is None:
        return []
    out: list[str] = []
    for p in parts:
        cell = _clean_table_cell(p)
        if cell:
            out.append(cell)
    return out


def _normalize_values_2d(rows: list[list[Any]]) -> list[list[str]]:
    normalized: list[list[str]] = []
    max_cols = 0
    for raw_row in rows[:100]:
        cells = [_clean_table_cell(v) for v in raw_row]
        if not any(cells):
            continue
        normalized.append(cells)
        max_cols = max(max_cols, len(cells))
    if not normalized:
        return []
    max_cols = max(1, min(50, max_cols))
    out: list[list[str]] = []
    for row in normalized[:100]:
        trimmed = row[:max_cols]
        if len(trimmed) < max_cols:
            trimmed.extend([""] * (max_cols - len(trimmed)))
        out.append(trimmed)
    return out


def _detect_compact_expense_headers(header_text: str) -> list[str]:
    compact = re.sub(r"\s+", " ", str(header_text or "")).strip()
    if not compact:
        return []
    # 탭/구분자 없이 붙여넣은 법인카드 내역 문장을 7열 표로 복원한다.
    must_tokens = ["날짜", "사용 목적", "사용처", "금액", "비용 유형"]
    if all(token in compact for token in must_tokens):
        return [
            "날짜",
            "사용 목적",
            "사용처",
            "법인카드 사용내역서 여부",
            "금액",
            "법인카드/조교카드 이체 여부",
            "비용 유형",
        ]
    return []


def _parse_compact_expense_row(segment: str) -> list[str] | None:
    m = re.match(
        r"^\s*(\d{2,4}[./-]\d{1,2}[./-]\d{1,2})\s+(.+?)\s+(O|X|-)\s+(-?\d[\d,]*(?:\.\d+)?)\s+(O|X|-)\s+(.+?)\s*$",
        str(segment or ""),
        re.IGNORECASE,
    )
    if not m:
        return None
    date = _clean_table_cell(m.group(1))
    purpose_place = _clean_table_cell(m.group(2))
    statement_flag = _clean_table_cell(m.group(3)).upper()
    amount = _clean_table_cell(m.group(4))
    transfer_flag = _clean_table_cell(m.group(5)).upper()
    category = _clean_table_cell(m.group(6))

    tokens = purpose_place.split()
    if len(tokens) >= 2:
        place = tokens[-1]
        purpose = " ".join(tokens[:-1]).strip()
    else:
        purpose = purpose_place
        place = ""
    return [date, purpose, place, statement_flag, amount, transfer_flag, category]


def _parse_compact_generic_row(segment: str, col_count: int) -> list[str] | None:
    compact = re.sub(r"\s+", " ", str(segment or "")).strip()
    if not compact:
        return None
    if col_count <= 1:
        return [_clean_table_cell(compact)]
    tokens = compact.split(" ")
    if len(tokens) >= col_count:
        row = tokens[: col_count - 1] + [" ".join(tokens[col_count - 1 :])]
    else:
        row = tokens + [""] * (col_count - len(tokens))
    return [_clean_table_cell(v) for v in row[:col_count]]


def _extract_inline_table_values_2d(text: str) -> list[list[str]]:
    raw = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not raw:
        return []

    # 1) 줄바꿈+구분자(탭/파이프/콤마) 기반 표 추출
    lines = [line.strip() for line in raw.split("\n") if line.strip()]
    if len(lines) >= 2:
        header_idx = -1
        header: list[str] = []
        for idx, line in enumerate(lines):
            parsed_header = _split_table_line(line, allow_comma=True)
            if len(parsed_header) >= 2:
                header_idx = idx
                header = parsed_header
                break
        if header_idx >= 0 and len(header) >= 2:
            body_rows: list[list[str]] = []
            for line in lines[header_idx + 1 :]:
                parsed = _split_table_line(line, allow_comma=False)
                if not parsed and line.count(",") >= 2 and not re.search(r"\d{1,3},\d{3}", line):
                    parsed = _split_table_line(line, allow_comma=True)
                if parsed:
                    body_rows.append(parsed)
            if body_rows:
                matrix = _normalize_values_2d([header, *body_rows])
                if len(matrix) >= 2:
                    return matrix

    # 2) 날짜 토큰이 연속되는 압축 문장(공백 구분) 복원
    date_matches = list(_DATE_TOKEN_PATTERN.finditer(raw))
    if not date_matches:
        return []
    header_block = raw[: date_matches[0].start()].strip()
    if not header_block:
        return []
    headers = _detect_compact_expense_headers(header_block)
    if len(headers) < 2:
        headers = _split_table_line(header_block, allow_comma=True)
    if len(headers) < 2:
        return []

    rows: list[list[str]] = []
    col_count = len(headers)
    for idx, match in enumerate(date_matches):
        seg_start = match.start()
        seg_end = date_matches[idx + 1].start() if idx + 1 < len(date_matches) else len(raw)
        segment = raw[seg_start:seg_end].strip()
        if not segment:
            continue
        parsed_row: list[str] | None = None
        if col_count == 7 and "금액" in headers and "비용 유형" in headers:
            parsed_row = _parse_compact_expense_row(segment)
        if parsed_row is None:
            parsed_row = _parse_compact_generic_row(segment, col_count)
        if parsed_row:
            rows.append(parsed_row)
    if not rows:
        return []
    matrix = _normalize_values_2d([headers, *rows])
    return matrix if len(matrix) >= 2 else []


def extract_create_table_slot_hints(message: str) -> dict[str, Any]:
    """
    create_table 멀티턴 슬롯필링용 힌트를 자연어에서 추출한다.

    반환 키:
    - rows: int | None
    - cols: int | None
    - headers: list[str]
    - values_2d: list[list[str]]
    - start_cell: str | None
    - table_intent: bool
    """
    text = str(message or "").strip()
    lowered = text.lower()
    preset = match_table_preset(text)

    table_intent = (
        any(token in lowered for token in ["표", "테이블", "table"])
        and any(token in lowered for token in ["만들", "생성", "create", "작성"])
    )
    if preset is not None:
        table_intent = True

    rows: int | None = None
    cols: int | None = None
    m = re.search(r"(\d{1,3})\s*(?:\*|x|×)\s*(\d{1,3})", lowered)
    if not m:
        m = re.search(r"(\d{1,3})\s*행\s*(\d{1,3})\s*열", lowered)
    if not m:
        m = re.search(r"(\d{1,3})\s*by\s*(\d{1,3})", lowered)
    if m:
        rows = max(1, min(100, int(m.group(1))))
        cols = max(1, min(50, int(m.group(2))))

    start_cell = _extract_range_ref(text)
    if start_cell and ":" in start_cell:
        start_cell = start_cell.split(":")[0]

    # "금액, 장소, 날짜, 요건, 비고" 형태를 우선 처리한다.
    headers: list[str] = []
    header_source = re.sub(r"\d{1,3}\s*(?:\*|x|×)\s*\d{1,3}", "", text, flags=re.I)
    comma_tokens = [token.strip() for token in re.split(r"[,/|]", header_source) if token.strip()]
    if len(comma_tokens) >= 2:
        filtered: list[str] = []
        for token in comma_tokens:
            t = token.strip()
            if not t:
                continue
            if re.fullmatch(r"\d{1,3}\s*(?:\*|x|×)\s*\d{1,3}", t.lower()):
                continue
            t = re.sub(r"^(?:크기로|헤더는|헤더|컬럼은|컬럼)\s*", "", t, flags=re.I).strip()
            t = re.sub(r"(?:표|테이블|table)\s*(?:로|을|를)?\s*(?:만들어줘|생성해줘|create.*)?$", "", t, flags=re.I).strip()
            if t:
                filtered.append(t)
        if len(filtered) >= 2:
            headers = filtered

    values_2d = _extract_inline_table_values_2d(text)
    if values_2d:
        table_intent = True
        inferred_rows = len(values_2d)
        inferred_cols = max((len(row) for row in values_2d), default=0)
        if inferred_rows > 0:
            rows = max(1, min(100, max(rows or 0, inferred_rows)))
        if inferred_cols > 0:
            cols = max(1, min(50, max(cols or 0, inferred_cols)))
        parsed_headers = [str(v).strip() for v in values_2d[0] if str(v).strip()]
        if parsed_headers:
            headers = parsed_headers

    return {
        "rows": rows,
        "cols": cols,
        "headers": headers,
        "values_2d": values_2d,
        "start_cell": start_cell,
        "table_intent": table_intent,
        "template_key": preset.key if preset else None,
        "template_headers": list(preset.headers) if preset else [],
        "template_rows": preset.default_rows if preset else None,
        "template_cols": preset.default_cols if preset else None,
        "template_follow_up_question": preset.follow_up_question if preset else "",
        "blank_table": any(token in lowered for token in ["빈 표", "빈표", "그냥 빈"]),
        "affirmative": any(
            token in lowered
            for token in ["응", "네", "좋아", "그대로", "그 정도", "맞아", "yes", "ok", "okay"]
        ),
    }

