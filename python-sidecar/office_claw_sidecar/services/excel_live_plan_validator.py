"""Excel Live 계획 검증기(Validator).

LLM 계획(action_plan)을 실행 가능한 결정적 형태로 정규화한다.
- action 화이트리스트
- action별 params 스키마 강제
- 기본값/문맥(context_range, recent_range) 보정
- 명백한 오분류(write_range + table intent) 복구
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from office_claw_sidecar.services.excel_live_executor import PlanStep


SUPPORTED_ACTIONS = {
    "excel_live.list_workbooks",
    "excel_live.select_workbook",
    "excel_live.read_range",
    "excel_live.write_range",
    "excel_live.create_table",
    "excel_live.highlight_by_condition",
    "excel_live.fill_range",
    "excel_live.apply_border",
    "excel_live.set_formula",
    "excel_live.save_workbook",
}

EDIT_ACTIONS = {
    "excel_live.write_range",
    "excel_live.create_table",
    "excel_live.highlight_by_condition",
    "excel_live.fill_range",
    "excel_live.apply_border",
    "excel_live.set_formula",
    "excel_live.save_workbook",
}

PASSIVE_ACTIONS = {
    "excel_live.list_workbooks",
    "excel_live.select_workbook",
    "excel_live.read_range",
}


@dataclass
class ValidationContext:
    message: str
    workbook_id: str | None = None
    sheet_name: str | None = None
    context_range: str | None = None
    recent_range: str | None = None


def _normalize_range_text(value: Any) -> str:
    return str(value or "").strip().upper().replace("$", "")


def _extract_table_shape(message: str) -> tuple[int, int] | None:
    text = str(message or "").lower()
    m = re.search(r"(\d{1,3})\s*(?:\*|x|×)\s*(\d{1,3})\s*(?:표|테이블|table)", text)
    if not m:
        m = re.search(r"(\d{1,3})\s*행\s*(\d{1,3})\s*열\s*(?:표|테이블|table)", text)
    if not m:
        return None
    rows = max(1, min(100, int(m.group(1))))
    cols = max(1, min(50, int(m.group(2))))
    return rows, cols


def _preferred_range(ctx: ValidationContext) -> str:
    return _normalize_range_text(ctx.context_range) or _normalize_range_text(ctx.recent_range)


def _coerce_operator(op: Any) -> str:
    raw = str(op or "").strip()
    aliases = {
        ">": ">",
        ">=": ">=",
        "<": "<",
        "<=": "<=",
        "==": "==",
        "!=": "!=",
        "eq": "==",
        "ne": "!=",
        "gt": ">",
        "gte": ">=",
        "lt": "<",
        "lte": "<=",
    }
    return aliases.get(raw.lower(), raw)


def _validate_action(action: str) -> None:
    if action not in SUPPORTED_ACTIONS:
        raise ValueError(f"지원하지 않는 action: {action}")


def _validate_step(step: PlanStep, ctx: ValidationContext) -> PlanStep:
    action = str(step.action or "").strip()
    params = dict(step.params or {})
    reason = str(step.reason or "").strip()
    _validate_action(action)

    preferred_range = _preferred_range(ctx)
    lowered = str(ctx.message or "").lower()

    if action == "excel_live.select_workbook":
        workbook = str(params.get("workbook_id") or params.get("name") or "").strip()
        if not workbook:
            raise ValueError("select_workbook에는 workbook_id가 필요합니다.")
        return PlanStep(action=action, params={"workbook_id": workbook}, reason=reason)

    if action == "excel_live.read_range":
        range_ref = _normalize_range_text(params.get("range_ref"))
        if not range_ref:
            range_ref = preferred_range or "__ACTIVE_SELECTION__"
        return PlanStep(action=action, params={"range_ref": range_ref}, reason=reason)

    if action == "excel_live.write_range":
        # LLM이 표 생성 의도를 write_range + 불완전 파라미터로 내는 경우를 복구한다.
        values_2d = params.get("values_2d")
        if not isinstance(values_2d, list):
            shape = _extract_table_shape(ctx.message)
            if shape and any(t in lowered for t in ["표", "테이블", "table", "만들", "생성", "create"]):
                rows, cols = shape
                return PlanStep(
                    action="excel_live.create_table",
                    params={
                        "start_cell": "__ACTIVE_CELL__",
                        "rows": rows,
                        "cols": cols,
                        "with_border": True,
                    },
                    reason=reason or "표 생성 의도 자동 복구",
                )
            raise ValueError("write_range에는 values_2d(2차원 배열)가 필요합니다.")
        start_cell = _normalize_range_text(params.get("start_cell")) or "__ACTIVE_CELL__"
        normalized_rows: list[list[Any]] = []
        for row in values_2d:
            if isinstance(row, list):
                normalized_rows.append(row)
            else:
                normalized_rows.append([row])
        return PlanStep(
            action=action,
            params={"start_cell": start_cell, "values_2d": normalized_rows},
            reason=reason,
        )

    if action == "excel_live.create_table":
        rows_raw = params.get("rows", 5)
        cols_raw = params.get("cols", 5)
        rows = max(1, min(100, int(5 if rows_raw is None else rows_raw)))
        cols = max(1, min(50, int(5 if cols_raw is None else cols_raw)))
        start_cell = _normalize_range_text(params.get("start_cell")) or "__ACTIVE_CELL__"
        with_border = bool(params.get("with_border", True))
        return PlanStep(
            action=action,
            params={
                "start_cell": start_cell,
                "rows": rows,
                "cols": cols,
                "with_border": with_border,
            },
            reason=reason,
        )

    if action == "excel_live.highlight_by_condition":
        target_range = _normalize_range_text(params.get("target_range")) or _normalize_range_text(
            params.get("range_ref")
        )
        if not target_range or target_range == "__ACTIVE_SELECTION__":
            target_range = preferred_range or "A:Z"
        operator = _coerce_operator(params.get("operator") or params.get("condition") or ">=")
        threshold_raw = params.get("threshold", params.get("value", 0))
        try:
            threshold = float(threshold_raw)
        except Exception as exc:
            raise ValueError("highlight_by_condition.threshold는 숫자여야 합니다.") from exc
        fill_color = str(params.get("fill_color") or params.get("color") or "#FFFF00").strip()
        return PlanStep(
            action=action,
            params={
                "target_range": target_range,
                "operator": operator,
                "threshold": threshold,
                "fill_color": fill_color,
            },
            reason=reason,
        )

    if action == "excel_live.fill_range":
        target_range = _normalize_range_text(params.get("target_range"))
        if not target_range or target_range == "__ACTIVE_SELECTION__":
            target_range = preferred_range or "__ACTIVE_SELECTION__"
        fill_color = str(params.get("fill_color") or "#FFFF00").strip()
        return PlanStep(
            action=action,
            params={"target_range": target_range, "fill_color": fill_color},
            reason=reason,
        )

    if action == "excel_live.apply_border":
        target_range = _normalize_range_text(params.get("target_range"))
        if not target_range or target_range == "__ACTIVE_SELECTION__":
            target_range = preferred_range or "__ACTIVE_SELECTION__"
        line_style = str(params.get("line_style") or "continuous").strip().lower()
        weight = str(params.get("weight") or "medium").strip().lower()
        color = str(params.get("color") or "#000000").strip()
        return PlanStep(
            action=action,
            params={
                "target_range": target_range,
                "line_style": line_style,
                "weight": weight,
                "color": color,
            },
            reason=reason,
        )

    if action == "excel_live.set_formula":
        formula_a1 = str(params.get("formula_a1") or "").strip()
        if not formula_a1 or not formula_a1.startswith("="):
            raise ValueError("set_formula.formula_a1은 '='로 시작해야 합니다.")
        range_ref = _normalize_range_text(params.get("range_ref")) or preferred_range or "__ACTIVE_SELECTION__"
        return PlanStep(
            action=action,
            params={"range_ref": range_ref, "formula_a1": formula_a1},
            reason=reason,
        )

    if action in {"excel_live.list_workbooks", "excel_live.save_workbook"}:
        return PlanStep(action=action, params={}, reason=reason)

    return PlanStep(action=action, params=params, reason=reason)


def validate_plan(
    steps: list[PlanStep],
    *,
    context: ValidationContext,
) -> list[PlanStep]:
    if not steps:
        return []
    validated: list[PlanStep] = []
    for step in steps:
        validated.append(_validate_step(step, context))
    return validated

