"""Excel BasicBench v1 — 종단 케이스 10건.

각 케이스는 세 가지를 함께 들고 있다.

- `expectation` — 저장된 파일이 만족해야 하는 최종 상태
- `oracle` — 사람이 쓴 정답 계획. LLM 없이 하네스가 제대로 채점하는지 확인한다
- `mutant` — 일부러 틀린 계획. 단언이 실패를 실제로 잡는지 확인한다

oracle이 통과하고 mutant가 떨어져야 이 벤치마크의 숫자를 믿을 수 있다.
둘 다 통과하면 단언이 헐거운 것이고, 둘 다 떨어지면 실행 경로가 깨진 것이다.
"""

from __future__ import annotations

from typing import Any

from .bench_core import (
    AllOf,
    AllRowsSatisfy,
    BenchCase,
    CellFilled,
    CellValue,
    ColumnValuesEqual,
    FormulaContains,
    NumberFormatContains,
    RangeEmpty,
    SortedByColumn,
)

SHEET = "판매"

# 매출 = 판매수량 * 단가. 정렬·필터·수식 케이스가 같은 표를 공유한다.
HEADERS = ["일자", "상품", "담당자", "판매수량", "단가", "매출"]
ROWS: list[list[Any]] = [
    ["2026-03-02", "노트북", "김민수", 3, 1250000, 3750000],
    ["2026-03-03", "마우스", "이서연", 25, 12500, 312500],
    ["2026-03-04", "키보드", "박준호", 12, 48000, 576000],
    ["2026-03-05", "모니터", "김민수", 7, 320000, 2240000],
    ["2026-03-06", "마우스", "최지훈", 40, 12500, 500000],
    ["2026-03-07", "노트북", "이서연", 2, 1250000, 2500000],
    ["2026-03-08", "키보드", "김민수", 30, 48000, 1440000],
    ["2026-03-09", "모니터", "박준호", 5, 320000, 1600000],
]


def _table() -> list[list[Any]]:
    return [list(HEADERS), *[list(row) for row in ROWS]]


def _step(action: str, **params: Any) -> dict[str, Any]:
    return {"action": f"excel_live.{action}", "params": params}


CASES: list[BenchCase] = [
    BenchCase(
        case_id="001_write_cell",
        category="write",
        prompt="C3에 120 입력해줘",
        sheet=SHEET,
        rows=_table(),
        expectation=CellValue("C3", 120),
        oracle=[_step("write_range", start_cell="C3", values_2d=[[120]])],
        # 엉뚱한 셀에 쓰기
        mutant=[_step("write_range", start_cell="D3", values_2d=[[120]])],
    ),
    BenchCase(
        case_id="002_write_header",
        category="write",
        prompt="G1에 비고라고 써줘",
        sheet=SHEET,
        rows=_table(),
        expectation=CellValue("G1", "비고"),
        oracle=[_step("write_range", start_cell="G1", values_2d=[["비고"]])],
        # 다른 값을 쓰기
        mutant=[_step("write_range", start_cell="G1", values_2d=[["메모"]])],
    ),
    BenchCase(
        case_id="003_clear_range",
        category="clear",
        prompt="담당자 열 내용 지워줘",
        sheet=SHEET,
        rows=_table(),
        expectation=RangeEmpty("C2:C9"),
        oracle=[_step("clear_range", target_range="C2:C9")],
        # 일부만 지우기
        mutant=[_step("clear_range", target_range="C2:C4")],
    ),
    BenchCase(
        case_id="004_formula_sum",
        category="formula",
        prompt="G1에 매출 전체 합계 수식을 넣어줘",
        sheet=SHEET,
        rows=_table(),
        expectation=FormulaContains("G1", "SUM"),
        oracle=[_step("set_formula", range_ref="G1", formula_a1="=SUM(F2:F9)")],
        # 수식이 아니라 값을 박아넣기 — 자주 나오는 실패 방식
        mutant=[_step("write_range", start_cell="G1", values_2d=[[12918500]])],
    ),
    BenchCase(
        case_id="005_sort_desc",
        category="sort",
        prompt="매출 높은 순으로 정렬해줘",
        sheet=SHEET,
        rows=_table(),
        expectation=SortedByColumn("매출", descending=True),
        oracle=[
            _step("sort_range", target_range="A1:F9", key_column="매출", order="desc")
        ],
        # 방향을 반대로
        mutant=[
            _step("sort_range", target_range="A1:F9", key_column="매출", order="asc")
        ],
    ),
    BenchCase(
        case_id="006_sort_asc_other_column",
        category="sort",
        prompt="판매수량 적은 것부터 정렬해줘",
        sheet=SHEET,
        rows=_table(),
        expectation=SortedByColumn("판매수량", descending=False),
        oracle=[
            _step("sort_range", target_range="A1:F9", key_column="판매수량", order="asc")
        ],
        # 엉뚱한 열로 정렬 — 액션 이름만 보는 평가가 놓치는 실패
        mutant=[
            _step("sort_range", target_range="A1:F9", key_column="단가", order="asc")
        ],
    ),
    BenchCase(
        case_id="007_filter_rows",
        category="filter",
        prompt="매출 1,000,000 이상인 행만 남겨줘",
        sheet=SHEET,
        rows=_table(),
        expectation=AllRowsSatisfy(
            header="매출",
            predicate=lambda v: v is not None and float(v) >= 1_000_000,
            expected_count=5,
        ),
        oracle=[
            _step(
                "filter_rows",
                target_range="A1:F9",
                column="매출",
                operator=">=",
                value=1_000_000,
            )
        ],
        # 부등호 방향 반대
        mutant=[
            _step(
                "filter_rows",
                target_range="A1:F9",
                column="매출",
                operator="<",
                value=1_000_000,
            )
        ],
    ),
    BenchCase(
        case_id="008_fill_header",
        category="format",
        prompt="머리글 행을 노란색으로 칠해줘",
        sheet=SHEET,
        rows=_table(),
        expectation=AllOf([CellFilled("A1", "FFFF00"), CellFilled("F1", "FFFF00")]),
        oracle=[_step("fill_range", target_range="A1:F1", fill_color="#FFFF00")],
        # 한 칸만 칠하기
        mutant=[_step("fill_range", target_range="A1:A1", fill_color="#FFFF00")],
    ),
    BenchCase(
        case_id="009_dedupe_rows",
        category="dedupe",
        prompt="상품 기준으로 중복 행을 제거해줘",
        sheet=SHEET,
        rows=_table(),
        expectation=ColumnValuesEqual("상품", ["노트북", "마우스", "키보드", "모니터"]),
        oracle=[_step("dedupe_rows", target_range="A1:F9", key_columns=["상품"])],
        # 기준 열을 잘못 잡아 아무것도 지워지지 않는 경우
        mutant=[_step("dedupe_rows", target_range="A1:F9", key_columns=["일자"])],
    ),
    BenchCase(
        case_id="010_number_format",
        category="format",
        prompt="매출 열에 천단위 콤마를 넣어줘",
        sheet=SHEET,
        rows=_table(),
        expectation=NumberFormatContains("F2", "#,##0"),
        oracle=[_step("set_number_format", target_range="F2:F9", format_code="#,##0")],
        # 다른 열에 적용
        mutant=[_step("set_number_format", target_range="D2:D9", format_code="#,##0")],
    ),
]


def all_cases() -> list[BenchCase]:
    return list(CASES)
