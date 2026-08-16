"""액션 커버리지 학습 사례 생성기.

v4 학습셋을 감사해 보니 액션 분포가 심하게 기울어 있었다.

    pivot_table 161건 · write_range 148건 · set_formula 128건
    ...
    create_table 3건 · fill_range 3건 · compare_ranges 1건 · forecast_linear 1건
    consolidate_workbooks_from_folder / refresh_power_query / run_vba_macro 0건

증류 로그에서 뽑은 데이터라 "그때 사용자가 많이 시킨 것"만 많다. 예제가 한 건뿐인
액션은 모델이 사실상 배우지 못하고, 0건인 액션은 프롬프트에 이름만 있을 뿐 절대
선택되지 않는다. 기능이 있는데 못 쓰는 상태다.

이 모듈은 **실행 가능한 49개 액션 전부**에 대해 통합문서에 근거한 사례를 만들어
바닥을 깔아 준다. 파라미터는 검증기(`excel_live_plan_validator`)를 그대로 통과하도록
맞췄고, 지시문은 실제 사용자가 쓰는 구어체 여러 갈래로 흔든다.
"""

from __future__ import annotations

import random
import re
from collections.abc import Callable
from typing import Any

from office_claw_sidecar.services.excel_live_plan_validator import EDIT_ACTIONS
from office_claw_sidecar.services.excel_workbook_fixtures import (
    WORKBOOK_FIXTURES,
    categorical_headers,
    digest_from_fixtures,
    digest_headers,
    numeric_headers,
)

NAVIGATE_ACTIONS = frozenset(
    {
        "excel_live.list_workbooks",
        "excel_live.select_workbook",
        "excel_live.list_sheets",
        "excel_live.select_sheet",
        "excel_live.create_sheet",
    }
)


# ── 다이제스트 조회 도우미 ───────────────────────────────────────────────


def _active_entry(digest: dict[str, Any]) -> dict[str, Any]:
    active = str(digest.get("active_sheet") or "")
    for sheet in digest.get("sheets") or []:
        if str(sheet.get("name")) == active:
            return sheet
    sheets = digest.get("sheets") or [{}]
    return sheets[0]


def _sheet_names(digest: dict[str, Any]) -> list[str]:
    return [str(sheet.get("name") or "") for sheet in digest.get("sheets") or []]


def _letter_of(digest: dict[str, Any], header: str) -> str:
    for column in _active_entry(digest).get("columns") or []:
        if str(column.get("header")) == header:
            return str(column.get("letter") or "A")
    return "A"


def _row_count(digest: dict[str, Any]) -> int:
    used = str(_active_entry(digest).get("used_range") or "A1:A10")
    match = re.search(r"(\d+)$", used)
    return int(match.group(1)) if match else 10


def _last_letter(digest: dict[str, Any]) -> str:
    used = str(_active_entry(digest).get("used_range") or "A1:A10")
    match = re.search(r":([A-Z]+)\d+$", used)
    return match.group(1) if match else "A"


def _column_range(digest: dict[str, Any], header: str, *, skip_header: bool = True) -> str:
    letter = _letter_of(digest, header)
    return f"{letter}{2 if skip_header else 1}:{letter}{_row_count(digest)}"


def _new_sheet_name(digest: dict[str, Any], base: str) -> str:
    """다이제스트에 없는 이름을 고른다 — 출력 시트는 새로 만드는 자리다."""
    existing = set(_sheet_names(digest))
    if base not in existing:
        return base
    for suffix in range(2, 20):
        candidate = f"{base}{suffix}"
        if candidate not in existing:
            return candidate
    return f"{base}_새시트"


# 한 사례는 (지시문, 계획 단계들, 이유) 하나다.
# 통합문서가 조건을 못 채우면(예: 숫자 열이 없음) None을 반환한다.
Case = tuple[str, list[dict[str, Any]], str]
Builder = Callable[[dict[str, Any], random.Random], Case | None]

_BUILDERS: dict[str, Builder] = {}


def _register(action: str) -> Callable[[Builder], Builder]:
    def wrap(fn: Builder) -> Builder:
        _BUILDERS[action] = fn
        return fn

    return wrap


def _step(action: str, params: dict[str, Any], reason: str) -> dict[str, Any]:
    return {"action": action, "params": params, "reason": reason}


# ── 탐색 ────────────────────────────────────────────────────────────────


@_register("excel_live.list_workbooks")
def _b_list_workbooks(digest: dict[str, Any], rng: random.Random) -> Case | None:
    text = rng.choice(
        [
            "지금 열려있는 엑셀 파일 뭐뭐 있어?",
            "열린 통합문서 목록 보여줘",
            "어떤 파일들 켜져 있는지 알려줘",
            "작업 중인 엑셀 다 보여줘",
        ]
    )
    return text, [_step("excel_live.list_workbooks", {}, "열린 통합문서 조회")], "열린 파일 확인"


@_register("excel_live.select_workbook")
def _b_select_workbook(digest: dict[str, Any], rng: random.Random) -> Case | None:
    name = rng.choice(["2026년_매출집계.xlsx", "3분기_보고서.xlsx", "재고대장.xlsx", "급여_2026.xlsx"])
    text = rng.choice([f"{name} 파일로 바꿔줘", f"{name} 열어서 거기서 작업하자", f"{name}로 전환"])
    return (
        text,
        [_step("excel_live.select_workbook", {"workbook_id": name}, f"{name} 선택")],
        "대상 통합문서 전환",
    )


@_register("excel_live.list_sheets")
def _b_list_sheets(digest: dict[str, Any], rng: random.Random) -> Case | None:
    text = rng.choice(["시트 목록 보여줘", "탭 뭐뭐 있어?", "이 파일에 시트 몇 개야?", "시트 이름들 알려줘"])
    return text, [_step("excel_live.list_sheets", {}, "시트 목록 조회")], "시트 구성 확인"


@_register("excel_live.select_sheet")
def _b_select_sheet(digest: dict[str, Any], rng: random.Random) -> Case | None:
    names = _sheet_names(digest)
    if len(names) < 2:
        return None
    target = names[1]
    text = rng.choice([f"{target} 시트로 가줘", f"{target} 탭 열어줘", f"{target} 시트 보여줘"])
    return (
        text,
        [_step("excel_live.select_sheet", {"sheet_name": target}, f"{target} 시트 활성화")],
        "작업 시트 전환",
    )


@_register("excel_live.create_sheet")
def _b_create_sheet(digest: dict[str, Any], rng: random.Random) -> Case | None:
    name = _new_sheet_name(digest, rng.choice(["요약", "정리본", "분석", "보고용"]))
    text = rng.choice([f"{name}이라는 시트 하나 만들어줘", f"새 탭 만들어서 {name}으로 이름 붙여줘"])
    return (
        text,
        [_step("excel_live.create_sheet", {"sheet_name": name, "make_active": True}, f"{name} 시트 생성")],
        "새 시트 생성",
    )


@_register("excel_live.rename_sheet")
def _b_rename_sheet(digest: dict[str, Any], rng: random.Random) -> Case | None:
    names = _sheet_names(digest)
    if not names:
        return None
    old = names[0]
    new = _new_sheet_name(digest, rng.choice(["Dashboard", "요약본", "정리"]))
    text = rng.choice(
        [
            f"{old} 시트 이름을 {new}로 바꿔줘",
            f"{old} 시트 이름을 {new}으로 변경해줘",
        ]
    )
    return (
        text,
        [_step("excel_live.rename_sheet", {"sheet_name": old, "new_name": new}, f"{old}→{new}")],
        "시트 이름 변경",
    )


@_register("excel_live.delete_sheet")
def _b_delete_sheet(digest: dict[str, Any], rng: random.Random) -> Case | None:
    names = _sheet_names(digest)
    if len(names) < 2:
        return None
    target = names[-1]
    text = rng.choice([f"{target} 시트 삭제해줘", f"{target} 탭 제거해줘"])
    return (
        text,
        [_step("excel_live.delete_sheet", {"sheet_name": target}, f"{target} 시트 삭제")],
        "시트 삭제",
    )


# ── 읽기·쓰기 ───────────────────────────────────────────────────────────


@_register("excel_live.read_range")
def _b_read_range(digest: dict[str, Any], rng: random.Random) -> Case | None:
    ref = rng.choice(["A1:D10", "A1:C5", f"A1:{_last_letter(digest)}20", "B2:E8"])
    text = rng.choice([f"{ref} 좀 보여줘", f"{ref} 뭐 들어있는지 알려줘", f"{ref} 조회해줘"])
    return text, [_step("excel_live.read_range", {"range_ref": ref}, f"{ref} 읽기")], "범위 조회"


@_register("excel_live.write_range")
def _b_write_range(digest: dict[str, Any], rng: random.Random) -> Case | None:
    headers = digest_headers(digest)[:3]
    if len(headers) < 3:
        return None
    cell = rng.choice(["B2", "A1", "C3", "B5"])
    joined = ",".join(headers)
    text = rng.choice([f"{cell}부터 {joined} 넣어줘", f"{cell}에 {joined} 순서대로 입력"])
    return (
        text,
        [
            _step(
                "excel_live.write_range",
                {"start_cell": cell, "values_2d": [headers]},
                f"{cell}부터 머리글 입력",
            )
        ],
        "값 입력",
    )


@_register("excel_live.create_table")
def _b_create_table(digest: dict[str, Any], rng: random.Random) -> Case | None:
    preset = rng.choice(
        [
            ("가계부", ["날짜", "항목", "분류", "수입", "지출", "잔액"]),
            ("근태 관리표", ["사번", "이름", "날짜", "출근", "퇴근", "비고"]),
            ("회의록", ["일시", "참석자", "안건", "결정사항", "담당자"]),
            ("재고 관리표", ["품목", "규격", "입고", "출고", "현재고"]),
            ("체크리스트", ["항목", "담당", "기한", "완료여부"]),
        ]
    )
    title, headers = preset
    rows = rng.choice([8, 10, 12, 15, 20])
    text = rng.choice([f"{title} 만들어줘", f"{title} 양식 하나 짜줘", f"{title} 표 좀 만들어줘"])
    return (
        text,
        [
            _step(
                "excel_live.create_table",
                {
                    "start_cell": "A1",
                    "rows": rows,
                    "cols": len(headers),
                    "headers": headers,
                    "with_border": True,
                },
                f"{title} 표 생성",
            )
        ],
        "표 양식 생성",
    )


# ── 서식 ────────────────────────────────────────────────────────────────


@_register("excel_live.highlight_by_condition")
def _b_highlight(digest: dict[str, Any], rng: random.Random) -> Case | None:
    numbers = numeric_headers(digest)
    if not numbers:
        return None
    header = rng.choice(numbers)
    threshold = rng.choice([100, 1000, 10000, 100000, 1000000])
    text = rng.choice(
        [
            f"{header}이 {threshold} 넘는 거 노란색으로 칠해줘",
            f"{header} {threshold} 이상인 셀 강조해줘",
            f"{header}에서 {threshold}보다 큰 값 눈에 띄게",
        ]
    )
    return (
        text,
        [
            _step(
                "excel_live.highlight_by_condition",
                {
                    "target_range": _column_range(digest, header),
                    "operator": ">=",
                    "threshold": threshold,
                    "fill_color": "#FFFF00",
                },
                f"{header} {threshold} 이상 강조",
            )
        ],
        "조건부 강조",
    )


@_register("excel_live.fill_range")
def _b_fill_range(digest: dict[str, Any], rng: random.Random) -> Case | None:
    headers = digest_headers(digest)
    if not headers:
        return None
    header = rng.choice(headers)
    color_name, color = rng.choice(
        [("노란색", "#FFFF00"), ("연두색", "#C6EFCE"), ("주황색", "#FFC000"), ("하늘색", "#DDEBF7")]
    )
    text = rng.choice([f"{header} 열 전체 {color_name}으로 칠해줘", f"{header} 칸 배경 {color_name}으로"])
    return (
        text,
        [
            _step(
                "excel_live.fill_range",
                {"target_range": _column_range(digest, header, skip_header=False), "fill_color": color},
                f"{header} 열 {color_name} 배경",
            )
        ],
        "배경색 지정",
    )


@_register("excel_live.clear_range")
def _b_clear_range(digest: dict[str, Any], rng: random.Random) -> Case | None:
    headers = digest_headers(digest)
    if not headers:
        return None
    header = headers[-1]
    text = rng.choice([f"{header} 열 내용 다 지워줘", f"{header} 값만 비워줘 서식은 두고"])
    return (
        text,
        [
            _step(
                "excel_live.clear_range",
                {"target_range": _column_range(digest, header)},
                f"{header} 열 내용 삭제",
            )
        ],
        "내용 비우기",
    )


@_register("excel_live.apply_border")
def _b_apply_border(digest: dict[str, Any], rng: random.Random) -> Case | None:
    ref = f"A1:{_last_letter(digest)}{_row_count(digest)}"
    text = rng.choice(["표 전체에 테두리 넣어줘", "경계선 그려줘", "선 좀 쳐줘 표처럼 보이게"])
    return (
        text,
        [
            _step(
                "excel_live.apply_border",
                {"target_range": ref, "line_style": "continuous", "weight": "medium", "color": "#000000"},
                "표 전체 테두리",
            )
        ],
        "테두리 적용",
    )


@_register("excel_live.apply_color_scale")
def _b_color_scale(digest: dict[str, Any], rng: random.Random) -> Case | None:
    numbers = numeric_headers(digest)
    if not numbers:
        return None
    header = rng.choice(numbers)
    text = rng.choice(
        [f"{header} 크기에 따라 색 진하기 다르게 해줘", f"{header} 값 높낮이가 색으로 보이게 해줘"]
    )
    return (
        text,
        [
            _step(
                "excel_live.apply_color_scale",
                {"target_range": _column_range(digest, header)},
                f"{header} 색조 적용",
            )
        ],
        "색조 규칙",
    )


@_register("excel_live.apply_data_bar")
def _b_data_bar(digest: dict[str, Any], rng: random.Random) -> Case | None:
    numbers = numeric_headers(digest)
    if not numbers:
        return None
    header = rng.choice(numbers)
    text = rng.choice([f"{header}을 막대로 셀 안에 표시해줘", f"{header} 칸에 데이터 막대 넣어줘"])
    return (
        text,
        [
            _step(
                "excel_live.apply_data_bar",
                {"target_range": _column_range(digest, header)},
                f"{header} 데이터 막대",
            )
        ],
        "데이터 막대",
    )


@_register("excel_live.set_number_format")
def _b_number_format(digest: dict[str, Any], rng: random.Random) -> Case | None:
    numbers = numeric_headers(digest)
    if not numbers:
        return None
    header = rng.choice(numbers)
    label, code = rng.choice(
        [("천 단위 콤마", "#,##0"), ("원화 표시", '#,##0"원"'), ("퍼센트", "0.00%")]
    )
    text = rng.choice([f"{header} {label}로 보이게 해줘", f"{header} 서식 {label}로 바꿔줘"])
    return (
        text,
        [
            _step(
                "excel_live.set_number_format",
                {"target_range": _column_range(digest, header), "format_code": code},
                f"{header} {label} 서식",
            )
        ],
        "표시 형식",
    )


@_register("excel_live.set_font")
def _b_set_font(digest: dict[str, Any], rng: random.Random) -> Case | None:
    text = rng.choice(["머리글을 굵게 해줘", "첫 행 글자를 볼드 처리해줘", "A1 글꼴 굵게"])
    return (
        text,
        [_step("excel_live.set_font", {"target_range": "A1:A1", "bold": True}, "머리글 굵게")],
        "글꼴 굵게",
    )


@_register("excel_live.convert_to_excel_table")
def _b_convert_table(digest: dict[str, Any], rng: random.Random) -> Case | None:
    name = rng.choice(["SalesTable", "DataTable", "목록표"])
    used = str(_active_entry(digest).get("used_range") or "A1:D10")
    text = rng.choice(
        [
            f"이 범위를 {name} 이름으로 엑셀 표 테이블로 만들어줘",
            f"{used}를 {name}라는 엑셀 표로 변환해줘",
        ]
    )
    return (
        text,
        [
            _step(
                "excel_live.convert_to_excel_table",
                {"target_range": used, "table_name": name, "has_header": True},
                f"{name} 표 변환",
            )
        ],
        "엑셀 표 변환",
    )


@_register("excel_live.apply_formula_cf")
def _b_formula_cf(digest: dict[str, Any], rng: random.Random) -> Case | None:
    cats = categorical_headers(digest)
    if not cats:
        return None
    header = rng.choice(cats)
    letter = _letter_of(digest, header)
    needle = rng.choice(["완료", "미납", "발주필요", "지연"])
    formula = f'={letter}2="{needle}"'
    used = _column_range(digest, header)
    text = rng.choice(
        [
            f"{header}가 {needle}이면 빨간 조건부서식",
            f"{used} {needle}면 빨간 조건부서식",
        ]
    )
    return (
        text,
        [
            _step(
                "excel_live.apply_formula_cf",
                {"target_range": used, "formula": formula, "fill_color": "#FFC7CE"},
                f"{header} {needle} 조건부서식",
            )
        ],
        "수식 조건부 서식",
    )


@_register("excel_live.merge_cells")
def _b_merge(digest: dict[str, Any], rng: random.Random) -> Case | None:
    ref = f"A1:{_last_letter(digest)}1"
    sheet = str(_active_entry(digest).get("name") or "시트")
    text = rng.choice(
        [
            f"{ref} 셀 합쳐줘",
            f"{sheet} 맨 윗줄 병합해서 제목 칸으로 만들어줘",
            f"{sheet} {ref} 병합해줘",
        ]
    )
    return text, [_step("excel_live.merge_cells", {"target_range": ref}, f"{ref} 병합")], "셀 병합"


@_register("excel_live.unmerge_cells")
def _b_unmerge(digest: dict[str, Any], rng: random.Random) -> Case | None:
    ref = f"A1:{_last_letter(digest)}1"
    text = rng.choice(["병합된 셀 풀어줘", f"{ref} 셀 병합 해제해줘"])
    return text, [_step("excel_live.unmerge_cells", {"target_range": ref}, f"{ref} 병합 해제")], "병합 해제"


@_register("excel_live.freeze_panes")
def _b_freeze(digest: dict[str, Any], rng: random.Random) -> Case | None:
    text = rng.choice(["제목 행 고정해줘", "스크롤해도 머리글 보이게 해줘", "첫 줄 틀 고정"])
    return text, [_step("excel_live.freeze_panes", {"freeze_at": "A2"}, "1행 틀 고정")], "틀 고정"


@_register("excel_live.autofit_columns")
def _b_autofit(digest: dict[str, Any], rng: random.Random) -> Case | None:
    text = rng.choice(["열 너비 내용에 맞게 맞춰줘", "글자 잘리는데 칸 넓혀줘", "열 폭 자동 조정"])
    return (
        text,
        [_step("excel_live.autofit_columns", {"target_range": "__USED_RANGE__"}, "열 너비 자동 맞춤")],
        "열 너비 조정",
    )


# ── 수식 ────────────────────────────────────────────────────────────────


@_register("excel_live.set_formula")
def _b_set_formula(digest: dict[str, Any], rng: random.Random) -> Case | None:
    numbers = numeric_headers(digest)
    if len(numbers) < 2:
        return None
    left, right = numbers[0], numbers[1]
    left_letter = _letter_of(digest, left)
    right_letter = _letter_of(digest, right)
    last_row = _row_count(digest)
    out_letter = chr(ord(_last_letter(digest)) + 1)
    text = rng.choice(
        [
            f"{left}하고 {right} 곱해서 옆 칸에 넣어줘",
            f"{left} 곱하기 {right} 계산식 만들어줘",
        ]
    )
    return (
        text,
        [
            _step(
                "excel_live.set_formula",
                {
                    "range_ref": f"{out_letter}2:{out_letter}{last_row}",
                    "formula_a1": f"={left_letter}2*{right_letter}2",
                },
                f"{left}×{right} 수식",
            )
        ],
        "수식 입력",
    )


@_register("excel_live.verify_formula_result")
def _b_verify_formula(digest: dict[str, Any], rng: random.Random) -> Case | None:
    numbers = numeric_headers(digest)
    if not numbers:
        return None
    header = numbers[-1]
    text = rng.choice([f"{header} 계산 제대로 됐는지 확인해줘", "수식 결과 값 맞는지 봐줘"])
    return (
        text,
        [
            _step(
                "excel_live.verify_formula_result",
                {"range_ref": _column_range(digest, header)},
                f"{header} 결과 검증",
            )
        ],
        "수식 결과 확인",
    )


@_register("excel_live.recalculate")
def _b_recalculate(digest: dict[str, Any], rng: random.Random) -> Case | None:
    text = rng.choice(["다시 계산해줘", "수식 새로고침", "값이 안 바뀌는데 재계산해줘"])
    return text, [_step("excel_live.recalculate", {}, "통합문서 재계산")], "재계산"


@_register("excel_live.define_named_range")
def _b_named_range(digest: dict[str, Any], rng: random.Random) -> Case | None:
    headers = digest_headers(digest)
    if not headers:
        return None
    header = rng.choice(headers)
    name = re.sub(r"[^0-9A-Za-z가-힣_]", "", header) or "범위1"
    text = rng.choice([f"{header} 열에 {name}이라는 이름 붙여줘", f"{header} 범위 이름 정의해줘"])
    return (
        text,
        [
            _step(
                "excel_live.define_named_range",
                {"name": name, "target_range": _column_range(digest, header)},
                f"{name} 이름 정의",
            )
        ],
        "이름 정의",
    )


# ── 정렬·필터·중복 ──────────────────────────────────────────────────────


@_register("excel_live.sort_rows")
def _b_sort_rows(digest: dict[str, Any], rng: random.Random) -> Case | None:
    numbers = numeric_headers(digest)
    if not numbers:
        return None
    header = rng.choice(numbers)
    desc = rng.random() < 0.6
    word = "높은" if desc else "낮은"
    text = rng.choice([f"{header} {word} 순으로 정렬해줘", f"{header} 기준 {word} 순서대로 줄 세워줘"])
    return (
        text,
        [
            _step(
                "excel_live.sort_rows",
                {"sheet_name": str(digest.get("active_sheet")), "column": header, "order": "desc" if desc else "asc"},
                f"{header} {'내림' if desc else '오름'}차순",
            )
        ],
        "정렬",
    )


@_register("excel_live.sort_range")
def _b_sort_range(digest: dict[str, Any], rng: random.Random) -> Case | None:
    ref = f"A1:{_last_letter(digest)}{_row_count(digest)}"
    key = rng.choice([1, 2, 3])
    text = rng.choice([f"{ref} 범위를 {key}번째 열 기준으로 정렬", f"선택한 표 {key}번째 열로 정렬해줘"])
    return (
        text,
        [
            _step(
                "excel_live.sort_range",
                {"target_range": ref, "key_column": key, "order": "asc", "has_header": True},
                f"{key}열 오름차순 정렬",
            )
        ],
        "범위 정렬",
    )


@_register("excel_live.filter_rows")
def _b_filter(digest: dict[str, Any], rng: random.Random) -> Case | None:
    categories = categorical_headers(digest)
    if not categories:
        return None
    header, values = rng.choice(categories)
    keep = values[0]
    text = rng.choice([f"{keep}인 것만 남겨줘", f"{header}가 {keep}인 행만 보고 싶어", f"{keep}만 걸러줘"])
    return (
        text,
        [
            _step(
                "excel_live.filter_rows",
                {
                    "sheet_name": str(digest.get("active_sheet")),
                    "target_range": "__USED_RANGE__",
                    "column": header,
                    "operator": "==",
                    "value": keep,
                    "has_header": True,
                    "mode": "keep",
                },
                f"{header}={keep} 필터",
            )
        ],
        "행 필터",
    )


@_register("excel_live.dedupe_rows")
def _b_dedupe(digest: dict[str, Any], rng: random.Random) -> Case | None:
    headers = digest_headers(digest)
    if not headers:
        return None
    key = headers[0]
    text = rng.choice([f"{key} 같은 거 중복 지워줘", f"{key} 기준으로 중복 행 제거"])
    return (
        text,
        [
            _step(
                "excel_live.dedupe_rows",
                {"target_range": "__USED_RANGE__", "key_columns": [key], "has_header": True},
                f"{key} 기준 중복 제거",
            )
        ],
        "중복 제거",
    )


@_register("excel_live.find_duplicates")
def _b_find_duplicates(digest: dict[str, Any], rng: random.Random) -> Case | None:
    headers = digest_headers(digest)
    if not headers:
        return None
    key = headers[0]
    text = rng.choice([f"{key} 중복된 거 있는지만 찾아줘", "중복 있나 확인만 해줘 지우지는 말고"])
    return (
        text,
        [
            _step(
                "excel_live.find_duplicates",
                {"target_range": "__USED_RANGE__", "key_columns": [key], "has_header": True},
                f"{key} 중복 탐지",
            )
        ],
        "중복 조회",
    )


@_register("excel_live.find_replace")
def _b_find_replace(digest: dict[str, Any], rng: random.Random) -> Case | None:
    categories = categorical_headers(digest)
    if not categories:
        return None
    _header, values = rng.choice(categories)
    old = values[0]
    new = f"{old}(확인)"
    text = rng.choice([f"{old}을 {new}로 다 바꿔줘", f"{old} 전부 {new}로 치환"])
    return (
        text,
        [
            _step(
                "excel_live.find_replace",
                {
                    "target_range": "__USED_RANGE__",
                    "find_text": old,
                    "replace_text": new,
                    "match_case": False,
                    "whole_cell": True,
                },
                f"{old}→{new} 치환",
            )
        ],
        "찾아 바꾸기",
    )


# ── 열 도구 ─────────────────────────────────────────────────────────────


@_register("excel_live.drop_column")
def _b_drop_column(digest: dict[str, Any], rng: random.Random) -> Case | None:
    headers = digest_headers(digest)
    if len(headers) < 3:
        return None
    header = headers[-1]
    text = rng.choice([f"{header} 열 삭제해줘", f"{header} 칸 필요 없어 지워줘"])
    return (
        text,
        [
            _step(
                "excel_live.drop_column",
                {"sheet_name": str(digest.get("active_sheet")), "column": header},
                f"{header} 열 삭제",
            )
        ],
        "열 삭제",
    )


@_register("excel_live.rename_column")
def _b_rename_column(digest: dict[str, Any], rng: random.Random) -> Case | None:
    headers = digest_headers(digest)
    if not headers:
        return None
    header = rng.choice(headers)
    new_name = f"{header}_수정"
    text = rng.choice([f"{header} 머리글을 {new_name}으로 바꿔줘", f"{header} 열 이름 {new_name}로"])
    return (
        text,
        [
            _step(
                "excel_live.rename_column",
                {"sheet_name": str(digest.get("active_sheet")), "column": header, "new_name": new_name},
                f"{header}→{new_name}",
            )
        ],
        "열 이름 변경",
    )


@_register("excel_live.add_column")
def _b_add_column(digest: dict[str, Any], rng: random.Random) -> Case | None:
    numbers = numeric_headers(digest)
    if len(numbers) < 2:
        return None
    left, right = numbers[0], numbers[1]
    name = rng.choice(["합계", "계산결과", "소계"])
    text = rng.choice([f"{name} 열 새로 만들어서 {left}+{right} 넣어줘", f"맨 뒤에 {name} 열 추가해줘"])
    return (
        text,
        [
            _step(
                "excel_live.add_column",
                {
                    "sheet_name": str(digest.get("active_sheet")),
                    "name": name,
                    "formula_a1": f"={_letter_of(digest, left)}2+{_letter_of(digest, right)}2",
                },
                f"{name} 열 추가",
            )
        ],
        "열 추가",
    )


@_register("excel_live.calculate_column_stat")
def _b_column_stat(digest: dict[str, Any], rng: random.Random) -> Case | None:
    numbers = numeric_headers(digest)
    if not numbers:
        return None
    header = rng.choice(numbers)
    label, stat = rng.choice([("합계", "sum"), ("평균", "avg"), ("최댓값", "max"), ("개수", "count")])
    text = rng.choice([f"{header} {label} 얼마야?", f"{header} {label} 좀 알려줘"])
    return (
        text,
        [
            _step(
                "excel_live.calculate_column_stat",
                {"sheet_name": str(digest.get("active_sheet")), "column": header, "stat": stat},
                f"{header} {label}",
            )
        ],
        "열 통계",
    )


@_register("excel_live.group_by_aggregate")
def _b_group_by(digest: dict[str, Any], rng: random.Random) -> Case | None:
    numbers = numeric_headers(digest)
    categories = categorical_headers(digest)
    if not numbers or not categories:
        return None
    group, _values = rng.choice(categories)
    value = rng.choice(numbers)
    text = rng.choice([f"{group}별 {value} 합계 알려줘", f"{group}로 묶어서 {value} 더해줘"])
    return (
        text,
        [
            _step(
                "excel_live.group_by_aggregate",
                {
                    "sheet_name": str(digest.get("active_sheet")),
                    "group_column": group,
                    "value_column": value,
                    "agg": "sum",
                },
                f"{group}별 {value} 합계",
            )
        ],
        "그룹 집계",
    )


# ── 분석·출력 ───────────────────────────────────────────────────────────


@_register("excel_live.pivot_table")
def _b_pivot(digest: dict[str, Any], rng: random.Random) -> Case | None:
    numbers = numeric_headers(digest)
    categories = categorical_headers(digest)
    if not numbers or not categories:
        return None
    group, _values = rng.choice(categories)
    value = rng.choice(numbers)
    out = _new_sheet_name(digest, f"{group}별집계")
    text = rng.choice([f"{group}별 {value} 피벗으로 정리해줘", f"{group} 기준 {value} 집계표 새 시트에 만들어줘"])
    return (
        text,
        [
            _step(
                "excel_live.pivot_table",
                {
                    "source_sheet": str(digest.get("active_sheet")),
                    "source_range": "__USED_RANGE__",
                    "row_field": group,
                    "value_field": value,
                    "agg": "sum",
                    "output_sheet": out,
                    "output_start": "A1",
                    "has_header": True,
                },
                f"{group}별 {value} 집계표",
            )
        ],
        "피벗 집계",
    )


@_register("excel_live.create_chart")
def _b_chart(digest: dict[str, Any], rng: random.Random) -> Case | None:
    numbers = numeric_headers(digest)
    categories = categorical_headers(digest)
    if not numbers or not categories:
        return None
    axis, _values = rng.choice(categories)
    value = rng.choice(numbers)
    kind_word, kind = rng.choice([("막대", "bar"), ("선", "line"), ("원형", "pie")])
    text = rng.choice([f"{axis}별 {value} {kind_word} 그래프로 만들어줘", f"{value} {kind_word} 차트 그려줘"])
    return (
        text,
        [
            _step(
                "excel_live.create_chart",
                {
                    "source_range": "__USED_RANGE__",
                    "chart_type": kind,
                    "title": f"{axis}별 {value}",
                    "output_sheet": str(digest.get("active_sheet")),
                },
                f"{axis}별 {value} {kind_word} 차트",
            )
        ],
        "차트 생성",
    )


@_register("excel_live.validate_data")
def _b_validate_data(digest: dict[str, Any], rng: random.Random) -> Case | None:
    text = rng.choice(["이상한 값 있는지 점검해줘", "빈칸이나 음수 있나 봐줘", "데이터 문제 있는지 검사"])
    return (
        text,
        [
            _step(
                "excel_live.validate_data",
                {"target_range": "__USED_RANGE__", "checks": ["empty", "negative", "outlier"], "has_header": True},
                "빈값·음수·이상치 점검",
            )
        ],
        "데이터 점검",
    )


@_register("excel_live.compare_ranges")
def _b_compare(digest: dict[str, Any], rng: random.Random) -> Case | None:
    names = _sheet_names(digest)
    if len(names) < 2:
        return None
    left, right = names[0], names[1]
    out = _new_sheet_name(digest, "비교결과")
    text = rng.choice([f"{left}하고 {right} 뭐가 다른지 비교해줘", f"{left} {right} 두 시트 차이 찾아줘"])
    return (
        text,
        [
            _step(
                "excel_live.compare_ranges",
                {
                    "left_sheet": left,
                    "left_range": "A1:H50",
                    "right_sheet": right,
                    "right_range": "A1:H50",
                    "output_sheet": out,
                },
                f"{left}↔{right} 비교",
            )
        ],
        "범위 비교",
    )


@_register("excel_live.forecast_linear")
def _b_forecast(digest: dict[str, Any], rng: random.Random) -> Case | None:
    numbers = numeric_headers(digest)
    if not numbers:
        return None
    header = numbers[-1]
    horizon = rng.choice([3, 6, 12])
    out = _new_sheet_name(digest, "예측")
    text = rng.choice([f"{header} 앞으로 {horizon}개월 예측해줘", f"{header} 추세로 {horizon}기간 뒤 예상치 뽑아줘"])
    return (
        text,
        [
            _step(
                "excel_live.forecast_linear",
                {
                    "source_range": _column_range(digest, header),
                    "horizon": horizon,
                    "output_sheet": out,
                    "output_start": "A1",
                },
                f"{header} {horizon}기간 예측",
            )
        ],
        "선형 예측",
    )


@_register("excel_live.export_pdf")
def _b_export_pdf(digest: dict[str, Any], rng: random.Random) -> Case | None:
    text = rng.choice(["PDF로 저장해줘", "이거 PDF로 뽑아줘", "PDF 파일로 내보내기"])
    return text, [_step("excel_live.export_pdf", {}, "PDF 내보내기")], "PDF 저장"


@_register("excel_live.set_print_area")
def _b_print_area(digest: dict[str, Any], rng: random.Random) -> Case | None:
    ref = f"A1:{_last_letter(digest)}{_row_count(digest)}"
    text = rng.choice(["인쇄 영역 표 부분만 잡아줘", "출력할 때 한 장에 들어가게 해줘", "인쇄 범위 설정해줘"])
    return (
        text,
        [
            _step(
                "excel_live.set_print_area",
                {"print_area": ref, "orientation": "landscape", "fit_to_page": True},
                "인쇄 영역·용지 방향 설정",
            )
        ],
        "인쇄 설정",
    )


@_register("excel_live.add_cell_comment")
def _b_comment(digest: dict[str, Any], rng: random.Random) -> Case | None:
    headers = digest_headers(digest)
    if not headers:
        return None
    header = rng.choice(headers)
    letter = _letter_of(digest, header)
    note = rng.choice(["확인 필요", "담당자 검토 요망", "출처: 원본 대장"])
    text = rng.choice([f"{letter}1에 '{note}' 메모 달아줘", f"{header} 머리글에 메모 남겨줘"])
    return (
        text,
        [
            _step(
                "excel_live.add_cell_comment",
                {"target_range": f"{letter}1", "text": note, "author": "OfficeClaw AI"},
                f"{letter}1 메모 추가",
            )
        ],
        "셀 메모",
    )


@_register("excel_live.save_workbook")
def _b_save(digest: dict[str, Any], rng: random.Random) -> Case | None:
    text = rng.choice(["저장해줘", "지금까지 작업 저장", "파일 저장 좀"])
    return text, [_step("excel_live.save_workbook", {}, "통합문서 저장")], "저장"


# ── 보호·입력 제한·통합·자동화 ──────────────────────────────────────────


@_register("excel_live.protect_sheet")
def _b_protect(digest: dict[str, Any], rng: random.Random) -> Case | None:
    headers = digest_headers(digest)
    if not headers:
        return None
    header = headers[0]
    text = rng.choice(
        ["수식 칸 못 건드리게 잠가줘", "시트 보호 걸어줘 입력칸만 열어두고", "계산식 실수로 지우지 않게 해줘"]
    )
    return (
        text,
        [
            _step(
                "excel_live.protect_sheet",
                {
                    "lock_formula_cells": True,
                    "unlock_range": _column_range(digest, header),
                    "password": None,
                },
                "수식 셀 잠금 + 입력 범위 허용",
            )
        ],
        "시트 보호",
    )


@_register("excel_live.set_data_validation")
def _b_data_validation(digest: dict[str, Any], rng: random.Random) -> Case | None:
    categories = categorical_headers(digest)
    if not categories:
        return None
    header, values = rng.choice(categories)
    source = ",".join(values[:4])
    text = rng.choice([f"{header}은 선택해서 입력하게 해줘", f"{header} 드롭다운으로 만들어줘 ({source})"])
    return (
        text,
        [
            _step(
                "excel_live.set_data_validation",
                {
                    "target_range": _column_range(digest, header),
                    "validation_type": "list",
                    "source": source,
                    "allow_blank": True,
                    "show_error": True,
                },
                f"{header} 목록 입력 제한",
            )
        ],
        "입력 제한",
    )


@_register("excel_live.consolidate_sheets")
def _b_consolidate_sheets(digest: dict[str, Any], rng: random.Random) -> Case | None:
    names = _sheet_names(digest)
    if len(names) < 2:
        return None
    out = _new_sheet_name(digest, "통합결과")
    text = rng.choice(["시트들 하나로 합쳐줘", f"{names[0]}랑 {names[1]} 한 시트로 모아줘"])
    return (
        text,
        [
            _step(
                "excel_live.consolidate_sheets",
                {
                    "source_sheets": names[:2],
                    "output_sheet": out,
                    "include_header_once": True,
                    "add_source_sheet_col": True,
                },
                "시트 통합",
            )
        ],
        "시트 통합",
    )


@_register("excel_live.consolidate_workbooks_from_folder")
def _b_consolidate_folder(digest: dict[str, Any], rng: random.Random) -> Case | None:
    folder = rng.choice(
        [r"C:\작업\월별보고", r"C:\Users\user\Desktop\지점자료", r"D:\정산\2026", r"C:\data\지사별"]
    )
    out = _new_sheet_name(digest, "파일통합결과")
    text = rng.choice(
        [f"{folder} 폴더에 있는 엑셀 파일들 다 합쳐줘", f"{folder} 안 파일 전부 한 시트로 모아줘"]
    )
    return (
        text,
        [
            _step(
                "excel_live.consolidate_workbooks_from_folder",
                {
                    "folder_path": folder,
                    "pattern": "*.xlsx",
                    "output_sheet": out,
                    "include_header_once": True,
                    "add_source_file_col": True,
                },
                "폴더 내 파일 통합",
            )
        ],
        "파일 통합",
    )


@_register("excel_live.refresh_power_query")
def _b_refresh(digest: dict[str, Any], rng: random.Random) -> Case | None:
    text = rng.choice(["외부 데이터 새로고침 해줘", "쿼리 다시 불러와줘", "연결된 데이터 갱신"])
    return text, [_step("excel_live.refresh_power_query", {}, "연결·쿼리 새로고침")], "데이터 새로고침"


@_register("excel_live.run_vba_macro")
def _b_run_macro(digest: dict[str, Any], rng: random.Random) -> Case | None:
    macro = rng.choice(["월말정산", "Module1.정리매크로", "데이터정리", "AutoFormat"])
    text = rng.choice([f"{macro} 매크로 실행해줘", f"{macro} 돌려줘"])
    return (
        text,
        [_step("excel_live.run_vba_macro", {"macro_name": macro, "args": []}, f"{macro} 실행")],
        "매크로 실행",
    )


# ── 생성 진입점 ─────────────────────────────────────────────────────────


def _intent_of(steps: list[dict[str, Any]]) -> str:
    first = str(steps[0].get("action", ""))
    if first in EDIT_ACTIONS:
        return "edit"
    if first in NAVIGATE_ACTIONS:
        return "navigate"
    return "read"


def _output(steps: list[dict[str, Any]], reason: str) -> dict[str, Any]:
    intent = _intent_of(steps)
    return {
        "intent": intent,
        "mutates_workbook": intent == "edit",
        "action_plan": steps,
        "slot_fill": {},
        "partial_params": {},
        "follow_up_question": "",
        "reason": reason,
    }


def covered_actions() -> frozenset[str]:
    """이 모듈이 사례를 만들 수 있는 액션."""
    return frozenset(_BUILDERS)


def build_action_coverage_records(
    *, per_action: int = 20, seed: int = 11
) -> list[dict[str, Any]]:
    """액션마다 `per_action`건을 목표로, 통합문서를 바꿔 가며 사례를 만든다.

    같은 액션이라도 통합문서가 매번 달라야 모델이 문장이 아니라 다이제스트를 보고
    파라미터를 채운다. 그래서 픽스처 조합을 돌면서 생성한다.
    """
    fixture_names = [fixture.name for fixture in WORKBOOK_FIXTURES]
    records: list[dict[str, Any]] = []

    for action, builder in _BUILDERS.items():
        action_rng = random.Random(f"{seed}:{action}")
        made = 0
        attempt = 0
        # 픽스처 조합을 넉넉히 돌아도 조건을 못 채우는 액션이 있다(숫자 열 2개 필요 등).
        # 무한 루프 대신 시도 상한을 둔다.
        while made < per_action and attempt < per_action * 6:
            primary = fixture_names[attempt % len(fixture_names)]
            others = [name for name in fixture_names if name != primary]
            secondary = others[(attempt // len(fixture_names)) % len(others)]
            digest = digest_from_fixtures(
                [primary, secondary], seed=f"cover-{action}-{attempt}"
            )
            attempt += 1
            built = builder(digest, action_rng)
            if built is None:
                continue
            instruction, steps, reason = built
            records.append(
                {
                    "record_id": f"cover_v5:{action}:{primary}:{made}",
                    "instruction": instruction,
                    "output_json": _output(steps, reason),
                    "digest": digest,
                }
            )
            made += 1

    return records
