"""
플래너 SFT 보강 예제를 규칙으로 생성한다.

왜 필요한가 — v2 학습 데이터 161건에서 `write_range`는 6건(고유 문장 2건)뿐이었고
그마저 정상적인 값 입력 문장이 아니었다. 반면 수식 계열은 25건이라 확률이 그쪽으로
쏠렸고, 그 결과 "F6에 true 입력" 같은 단순 값 입력을 set_formula로 계획했다.

규칙 기반으로 만드는 이유는 라벨 정확도다. 값 입력과 수식 입력의 경계는
"= 로 시작하는가"로 기계적으로 판정되므로, 티처 모델을 쓰는 것보다 정확하고
검수도 필요 없다.

출력은 build_planner_sft_jsonl과 같은 중간 스키마(record_id/instruction/output_json)라
그 스크립트로 그대로 프롬프트를 입혀 학습 데이터로 만들 수 있다.

사용:
    python scripts/generate_planner_augmentation.py \
        --output ../datasets/distill/planner_augment_v3.jsonl
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from office_claw_sidecar.services.excel_live_plan_validator import (
    EDIT_ACTIONS,
    SUPPORTED_ACTIONS,
)

PREFIX = "excel_live."

# ── 값 입력(write_range) ──────────────────────────────────────────────────
# 회귀의 핵심. 불리언·플래그·짧은 텍스트는 수식처럼 보이지만 그냥 값이다.
SINGLE_CELLS = ["A1", "B2", "C3", "D4", "E5", "F6", "G7", "H8", "B10", "K3", "M12", "AA2"]

LITERAL_VALUES = [
    # 불리언/플래그 — set_formula로 새기 가장 쉬운 값들
    "true", "false", "TRUE", "FALSE", "Y", "N", "O", "X",
    # 숫자
    "0", "1", "120", "999", "3.14", "-5", "1000000", "0.25",
    # 짧은 한국어 텍스트
    "완료", "보류", "미정", "진행중", "취소", "확인", "해당없음", "재검토",
    # 짧은 영문/코드
    "OK", "NG", "N/A", "TODO", "DONE", "P1", "REV-2", "alpha",
    # 날짜/시간 형태
    "2026-08-08", "26/02/24", "09:30",
]

WRITE_VERBS = ["입력", "입력해줘", "써줘", "작성", "적어줘", "넣어줘", "set", "write", "기입해줘"]

# ── 다중 셀 값 입력 ────────────────────────────────────────────────────────
MULTI_RANGES = ["B2:D2", "A10:C10", "E3:G3", "H4:J4", "K5:M5", "B1:D1", "A2:C2"]

MULTI_VALUE_SETS = [
    ["이름", "수량", "금액"],
    ["사과", "10", "3000"],
    ["Y", "N", "Y"],
    ["1", "2", "3"],
    ["alpha", "beta", "gamma"],
    ["완료", "보류", "미정"],
    ["true", "false", "true"],
    ["부서", "인원", "예산"],
]

# ── 수식 입력(set_formula) 대조군 ──────────────────────────────────────────
# 값 입력과 구분선을 분명히 하려면 "= 로 시작하는 진짜 수식" 예제도 같이 필요하다.
FORMULA_TARGETS = ["C1", "D1", "E1", "J1", "L3", "I1:I10", "K2:K20", "M1:M10"]

FORMULAS = [
    "=SUM(A1:A10)",
    "=AVERAGE(B1:B10)",
    "=MAX(C1:C10)",
    "=MIN(D1:D10)",
    "=COUNT(E1:E10)",
    "=A1*2",
    "=B2*C2",
    '=IF(A2>0,"Y","N")',
    '=COUNTIF(A1:A10,">=5")',
    "=ROUND(B2/C2,2)",
]

FORMULA_PHRASES = ["수식 {f} 적용", "formula {f} set", "{f} 수식 넣어줘", "{f} 넣어줘"]

# 자연어로 표현한 수식 요청 — "합계 수식"처럼 = 없이도 수식이 맞는 경우
FORMULA_NL = [
    ("{cell}에 {src} 합계 수식 넣어줘", "=SUM({src})"),
    ("{cell}에 {src} 평균 수식 적용", "=AVERAGE({src})"),
    ("{cell}에 {src} 최대 수식 적용", "=MAX({src})"),
    ("{cell}에 {src} 최소 수식 적용", "=MIN({src})"),
    ("{cell}에 {src} 개수 수식 적용", "=COUNT({src})"),
]

FORMULA_SOURCES = ["A1:A10", "B2:B20", "C1:C10", "D1:D10", "E1:E10"]

# ── 미학습 액션 보강 ──────────────────────────────────────────────────────
# "실행은 되는데 학습 예제가 0건"인 기능들. 문장 1~3개씩만 넣어 존재를 알린다.
UNTRAINED_TEMPLATES: dict[str, list[tuple[str, dict[str, Any]]]] = {
    "clear_range": [
        ("A1:C10 내용 전부 지워줘", {"target_range": "A1:C10"}),
        ("선택한 범위 비워줘", {"target_range": "__ACTIVE_SELECTION__"}),
        ("B2:B20 깨끗하게 비워", {"target_range": "B2:B20"}),
    ],
    "find_replace": [
        ("서울을 부산으로 전부 바꿔줘", {"find_text": "서울", "replace_text": "부산"}),
        ("'미정' 찾아서 '확인중'으로 치환", {"find_text": "미정", "replace_text": "확인중"}),
    ],
    "freeze_panes": [
        ("첫 행 고정해줘", {"freeze_at": "A2"}),
        ("머리글 틀 고정", {"freeze_at": "A2"}),
        ("첫 열도 같이 고정해줘", {"freeze_at": "B2"}),
    ],
    "merge_cells": [
        ("A1:C1 셀 병합해줘", {"target_range": "A1:C1"}),
        ("제목 줄 합쳐줘", {"target_range": "A1:D1"}),
    ],
    "unmerge_cells": [
        ("병합된 셀 풀어줘", {"target_range": "__ACTIVE_SELECTION__"}),
        ("A1:C1 병합 해제", {"target_range": "A1:C1"}),
    ],
    "autofit_columns": [
        ("열 너비 자동으로 맞춰줘", {"target_range": "__USED_RANGE__"}),
        ("칼럼 폭 내용에 맞게 조정", {"target_range": "__USED_RANGE__"}),
    ],
    "drop_column": [
        ("비고 열 통째로 삭제해줘", {"column_name": "비고"}),
        ("수량 칼럼 없애줘", {"column_name": "수량"}),
    ],
    "rename_column": [
        ("금액 열 이름을 매출로 바꿔줘", {"column_name": "금액", "new_name": "매출"}),
        ("헤더 수량을 판매량으로 변경", {"column_name": "수량", "new_name": "판매량"}),
    ],
    "add_column": [
        ("마진율 열 하나 추가해줘", {"column_name": "마진율"}),
        ("맨 뒤에 비고 칼럼 새로 만들어줘", {"column_name": "비고"}),
    ],
    "save_workbook": [
        ("저장해줘", {}),
        ("파일 저장", {}),
    ],
    "recalculate": [
        ("수식 다시 계산해줘", {}),
        ("계산 새로고침", {}),
    ],
    "export_pdf": [
        ("PDF로 내보내줘", {}),
        ("이 시트 PDF로 저장", {}),
    ],
    "pivot_table": [
        ("지역별 매출 피벗 테이블 만들어줘", {"output_sheet": "피벗", "rows": ["지역"], "values": ["매출"]}),
        ("부서별 예산 집계 피벗으로", {"output_sheet": "피벗", "rows": ["부서"], "values": ["예산"]}),
    ],
    "dedupe_rows": [
        ("중복 행 제거해줘", {"key_columns": ["이름"]}),
        ("이름 기준 중복 삭제", {"key_columns": ["이름"]}),
    ],
    "find_duplicates": [
        ("중복 있는지 찾아줘", {"key_columns": ["이름"]}),
        ("중복 값 확인만 해줘", {"key_columns": ["이름"]}),
    ],
    "calculate_column_stat": [
        ("금액 열 합계 얼마야?", {"column_name": "금액", "stat": "sum"}),
        ("수량 평균 알려줘", {"column_name": "수량", "stat": "average"}),
    ],
    "set_number_format": [
        ("금액 열 천단위 콤마로 표시해줘", {"target_range": "__USED_RANGE__", "format_code": "comma"}),
        ("비율 열 퍼센트 형식으로", {"target_range": "__USED_RANGE__", "format_code": "percent"}),
    ],
    "sort_rows": [
        ("매출 기준으로 정렬해줘", {"key_column": "매출", "order": "desc"}),
        ("이름 오름차순 정렬", {"key_column": "이름", "order": "asc"}),
    ],
    "define_named_range": [
        ("A1:C10에 SalesData 이름 정의해줘", {"target_range": "A1:C10", "name": "SalesData"}),
    ],
    "add_cell_comment": [
        ("B2에 확인 필요 메모 달아줘", {"cell": "B2", "text": "확인 필요"}),
    ],
    "set_print_area": [
        ("A1:F30을 인쇄 영역으로 지정", {"target_range": "A1:F30"}),
        ("한 페이지에 맞춰서 인쇄 설정", {"fit_to_page": True}),
    ],
    "apply_color_scale": [
        ("금액 열 색조로 표시해줘", {"target_range": "__USED_RANGE__"}),
    ],
    "apply_data_bar": [
        ("매출 열에 데이터 막대 넣어줘", {"target_range": "__USED_RANGE__"}),
    ],
    "set_data_validation": [
        ("C열에 예/아니오 드롭다운 만들어줘", {"target_range": "C:C", "list_values": ["예", "아니오"]}),
    ],
    "compare_ranges": [
        ("A1:C10이랑 E1:G10 비교해서 차이 찾아줘", {"left_range": "A1:C10", "right_range": "E1:G10"}),
    ],
    "consolidate_sheets": [
        ("월별 시트 하나로 통합해줘", {"output_sheet": "통합"}),
    ],
    "forecast_linear": [
        ("다음 달 매출 추세로 예측해줘", {"output_sheet": "예측"}),
    ],
    "list_workbooks": [
        ("열린 통합문서 목록 보여줘", {}),
        ("지금 열려 있는 엑셀 파일 확인", {}),
    ],
    "select_workbook": [
        ("워크북 sales.xlsx 선택", {"workbook_name": "sales.xlsx"}),
        ("통합문서 inventory.xlsx로 전환", {"workbook_name": "inventory.xlsx"}),
    ],
    "list_sheets": [
        ("시트 목록 보여줘", {}),
        ("이 파일에 시트 뭐 있어?", {}),
    ],
    "select_sheet": [
        ("Sheet2로 이동해줘", {"sheet_name": "Sheet2"}),
        ("매출 시트로 전환", {"sheet_name": "매출"}),
    ],
    "create_table": [
        ("6*4 표 만들어줘", {"rows": 6, "cols": 4}),
        ("이름,수량,금액 헤더로 표 생성", {"headers": ["이름", "수량", "금액"]}),
    ],
    "apply_border": [
        ("A1:D10에 테두리 넣어줘", {"target_range": "A1:D10"}),
        ("표 전체에 경계선 그려줘", {"target_range": "__USED_RANGE__"}),
    ],
    "fill_range": [
        ("B2:B20 노란색으로 채워줘", {"target_range": "B2:B20", "color": "yellow"}),
        ("선택 영역 배경색 회색으로", {"target_range": "__ACTIVE_SELECTION__", "color": "gray"}),
    ],
    "validate_data": [
        ("데이터에 이상한 값 있는지 검사해줘", {"target_range": "__USED_RANGE__"}),
        ("빈 칸이나 오류 있는지 점검", {"target_range": "__USED_RANGE__"}),
    ],
    "verify_formula_result": [
        ("수식 결과가 맞는지 검증해줘", {"target_range": "__USED_RANGE__"}),
        ("계산 결과 검산해줘", {"target_range": "__USED_RANGE__"}),
    ],
    "group_by_aggregate": [
        ("지역별 매출 합계 알려줘", {"group_by": "지역", "value_column": "매출", "stat": "sum"}),
    ],
}

# ── 읽기(read_range) ──────────────────────────────────────────────────────
# write_range 예제를 대량으로 넣으면 "보여줘/확인" 같은 조회 요청까지 쓰기로 끌려갈 수 있다.
# 읽기 쪽도 같이 늘려 경계를 유지한다.
READ_TARGETS = [
    "A3", "B7", "C12", "D2:D30", "E1:G8", "B2:B15", "A1:F1",
    "C:C", "E:E", "G:G", "AB1:AD5", "H10:J14",
]

READ_VERBS = ["읽어줘", "조회해줘", "보여줘", "값 확인", "내용 확인", "출력해줘", "read", "display", "확인해줘"]


def load_reserved_instructions() -> frozenset[str]:
    """50개 명령 회귀 테스트의 문장은 생성 대상에서 제외한다.

    학습에 그대로 넣으면 그 테스트는 회귀 감지 능력을 잃는다.
    """
    tests_dir = Path(__file__).resolve().parents[1] / "tests"
    sys.path.insert(0, str(tests_dir))
    try:
        import test_excel_live_50_commands as suite
    except Exception:  # noqa: BLE001 - 테스트가 없어도 생성은 되어야 한다
        return frozenset()
    return frozenset(str(s["message"]).strip() for s in suite.SCENARIOS)

NAVIGATE_SHORT = {"list_workbooks", "select_workbook", "list_sheets", "select_sheet", "create_sheet"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="플래너 SFT 보강 예제 생성")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--write-samples", type=int, default=90)
    parser.add_argument("--multi-samples", type=int, default=30)
    parser.add_argument("--formula-samples", type=int, default=45)
    parser.add_argument("--read-samples", type=int, default=45)
    parser.add_argument("--seed", type=int, default=42)
    return parser.parse_args()


def make_record(instruction: str, action_short: str, params: dict[str, Any]) -> dict[str, Any]:
    action = PREFIX + action_short
    if action not in SUPPORTED_ACTIONS:
        raise ValueError(f"실행할 수 없는 액션을 생성하려 했다: {action}")

    if action in EDIT_ACTIONS:
        intent = "edit"
    elif action_short in NAVIGATE_SHORT:
        intent = "navigate"
    else:
        intent = "read"

    digest = hashlib.sha1(f"{instruction}|{action}".encode()).hexdigest()[:10]
    return {
        "record_id": f"augment_v3:{action_short}:{digest}",
        "instruction": instruction,
        "output_json": {
            "intent": intent,
            "mutates_workbook": intent == "edit",
            "action_plan": [{"action": action, "params": params, "reason": ""}],
            "slot_fill": {},
            "partial_params": {},
            "follow_up_question": "",
            "reason": "사용자 요청 실행",
        },
    }


def gen_write_single(rng: random.Random, count: int) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    while len(out) < count:
        cell = rng.choice(SINGLE_CELLS)
        value = rng.choice(LITERAL_VALUES)
        verb = rng.choice(WRITE_VERBS)
        instruction = f"{cell}에 {value} {verb}"
        if instruction in seen:
            continue
        seen.add(instruction)
        out.append(
            make_record(
                instruction,
                "write_range",
                {"target_range": cell, "values": [[value]]},
            )
        )
    return out


def gen_write_multi(rng: random.Random, count: int) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    while len(out) < count:
        target = rng.choice(MULTI_RANGES)
        values = rng.choice(MULTI_VALUE_SETS)
        verb = rng.choice(WRITE_VERBS)
        instruction = f"{target}에 {','.join(values)} {verb}"
        if instruction in seen:
            continue
        seen.add(instruction)
        out.append(
            make_record(
                instruction,
                "write_range",
                {"target_range": target, "values": [values]},
            )
        )
    return out


def gen_set_formula(rng: random.Random, count: int) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    while len(out) < count:
        if rng.random() < 0.6:
            target = rng.choice(FORMULA_TARGETS)
            formula = rng.choice(FORMULAS)
            instruction = f"{target}에 " + rng.choice(FORMULA_PHRASES).format(f=formula)
        else:
            template, formula_template = rng.choice(FORMULA_NL)
            target = rng.choice([c for c in FORMULA_TARGETS if ":" not in c])
            source = rng.choice(FORMULA_SOURCES)
            instruction = template.format(cell=target, src=source)
            formula = formula_template.format(src=source)
        if instruction in seen:
            continue
        seen.add(instruction)
        out.append(
            make_record(
                instruction,
                "set_formula",
                {"range_ref": target, "formula_a1": formula},
            )
        )
    return out


def gen_read_range(rng: random.Random, count: int) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    attempts = 0
    while len(out) < count and attempts < count * 50:
        attempts += 1
        target = rng.choice(READ_TARGETS)
        verb = rng.choice(READ_VERBS)
        instruction = f"{target} {verb}"
        if instruction in seen:
            continue
        seen.add(instruction)
        out.append(make_record(instruction, "read_range", {"target_range": target}))
    return out


def gen_untrained() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for action_short, cases in UNTRAINED_TEMPLATES.items():
        for instruction, params in cases:
            out.append(make_record(instruction, action_short, params))
    return out


def main() -> None:
    args = parse_args()
    rng = random.Random(args.seed)

    groups = {
        "write_range(단일 셀 리터럴)": gen_write_single(rng, args.write_samples),
        "write_range(다중 셀)": gen_write_multi(rng, args.multi_samples),
        "set_formula(대조군)": gen_set_formula(rng, args.formula_samples),
        "read_range(조회 경계)": gen_read_range(rng, args.read_samples),
        "미학습 액션 보강": gen_untrained(),
    }

    reserved = load_reserved_instructions()
    records: list[dict[str, Any]] = []
    dropped = 0
    for label, items in groups.items():
        kept = [r for r in items if r["instruction"].strip() not in reserved]
        dropped += len(items) - len(kept)
        print(f"{label:28s}: {len(kept)}건")
        records.extend(kept)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    print(f"회귀 테스트 문장과 겹쳐 제외: {dropped}건")
    print(f"합계: {len(records)}건")
    print(f"출력: {args.output}")


if __name__ == "__main__":
    main()
