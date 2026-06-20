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


SUPPORTED_ACTIONS = {
    "excel_live.list_workbooks",
    "excel_live.select_workbook",
    "excel_live.read_range",
    "excel_live.write_range",
    "excel_live.highlight_by_condition",
    "excel_live.set_formula",
}


def _extract_range_ref(text: str) -> str | None:
    match = re.search(r"\b([a-z]+\d+:[a-z]+\d+|[a-z]:[a-z]|[a-z]+\d+)\b", text, re.IGNORECASE)
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


def parse_command_rule_based(message: str) -> dict[str, Any] | None:
    text = message.strip()
    lowered = text.lower()

    if any(
        token in lowered
        for token in [
            "열린 엑셀",
            "워크북 목록",
            "열린 파일 목록",
            "열린 통합문서",
            "workbook list",
            "list workbooks",
        ]
    ):
        return {
            "action": "excel_live.list_workbooks",
            "params": {},
            "reason": "열린 통합문서 목록 조회 요청",
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

    # 예: "A1:C10 읽어줘", "B열 보여줘"
    read_verbs = r"(읽어|읽기|보여|조회|확인|read|show|display)"
    if re.search(read_verbs, lowered):
        range_ref = _extract_target_range_from_text(lowered)
        if range_ref:
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
    if re.search(r"(칠해|강조|표시|highlight|색)", lowered):
        op_threshold = _parse_operator_threshold(lowered)
        target_range = _extract_target_range_from_text(lowered) or "A:A"
        color_match = re.search(
            r"(노란색|노랑|yellow|빨간색|빨강|red|초록색|초록|green|파란색|파랑|blue)",
            lowered,
        )
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
        has_write_verb = bool(re.search(r"(써|작성|입력|write|set|fill)", lowered))
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

    # 예: "A1에 120 입력", "C3에 '완료' 써줘"
    single_write = re.search(
        r"([a-z]+\d+)\s*에\s*['\"]?([^'\"]+?)['\"]?\s*(?:값\s*)?(입력(?:해(?:줘)?)?|써(?:줘)?|작성(?:해(?:줘)?)?|적어(?:줘)?|넣어(?:줘)?|write|set)",
        text,
        re.IGNORECASE,
    )
    if single_write:
        cell = single_write.group(1).upper()
        raw_value = single_write.group(2).strip()
        if "수식" not in raw_value and "formula" not in raw_value.lower() and "=" not in raw_value:
            value = _parse_literal_value(raw_value)
            return {
                "action": "excel_live.write_range",
                "params": {"start_cell": cell, "values_2d": [[value]]},
                "reason": "단일 셀 값 입력 요청",
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

    return None


async def parse_command_with_llm(message: str, llm_service) -> dict[str, Any]:
    prompt = (
        "너는 Excel Live 작업 분류기다. 사용자 메시지를 아래 JSON으로만 반환해라.\n"
        "허용 action:\n"
        "- excel_live.list_workbooks\n"
        "- excel_live.select_workbook\n"
        "- excel_live.read_range\n"
        "- excel_live.write_range\n"
        "- excel_live.highlight_by_condition\n"
        "- excel_live.set_formula\n\n"
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


async def parse_excel_live_command(message: str, llm_service) -> dict[str, Any]:
    rule = parse_command_rule_based(message)
    if rule is not None:
        return rule
    try:
        return await parse_command_with_llm(message, llm_service)
    except Exception:
        return {
            "action": "excel_live.list_workbooks",
            "params": {},
            "reason": "명령이 모호하여 열린 통합문서 목록 조회로 폴백",
        }


def _column_span(start_col: str, end_col: str) -> int:
    def col_to_num(col: str) -> int:
        n = 0
        for ch in col:
            n = n * 26 + (ord(ch) - ord("A") + 1)
        return n

    start = col_to_num(start_col)
    end = col_to_num(end_col)
    return max(1, end - start + 1)

