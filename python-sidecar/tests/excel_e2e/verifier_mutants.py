"""검증기가 잘못된 최종 상태를 잡아내는지 재는 변이(mutation) 수트.

## 무엇을 재는가

여기서 만드는 실패는 **인자 오류가 아니라 상태 오류**다. 계획도 인자도 맞고
실행기도 성공을 보고하는데, 통합문서에 실제로 남은 값이 다르다. Excel 자동화의
현실적인 실패가 이 모양이다 — API는 성공을 돌려주고 셀은 엉뚱한 값이다.

    요청:  A1:B2 = [[1, 2], [3, 4]]
    보고:  written_cells=4  ← 실행기는 성공했다고 말한다
    실제:  [[1, 2], [3, 9]]  ← 파일은 틀렸다

검증기가 `result.success`나 `written_cells`만 보면 이걸 전부 통과시킨다. 그래서
검증은 **명령 검증이 아니라 상태 검증**이어야 한다. 통합문서를 다시 읽어 기대
상태와 비교해야 한다.

## 두 지표를 같이 본다

- **false pass**: 파일이 틀렸는데 검증기가 통과시켰다 (검증 공백)
- **false fail**: 파일이 맞는데 검증기가 막았다 (과잉 검증)

false pass만 0으로 만드는 건 쉽다. 검증기가 항상 False를 돌려주면 된다. 그러면
멀쩡한 작업까지 롤백되어 에이전트가 망가진다. **false pass 감소 + false fail
유지**가 진짜 성공 조건이라 정상 케이스를 같은 수트에 넣는다.

## 부분 변이를 반드시 넣는다

전부 실패하는 것보다 일부 셀만 바뀐 상태가 더 위험하다. 사용자는 나머지가
맞으니 성공했다고 믿는다. `partial_write`·`partial_clear`가 그것이다.

`scripts/run_verifier_gap.py`와 역할이 다르다. 그쪽은 정렬·필터·차트까지
여러 액션에 걸친 넓이를 보고, 여기는 write/clear 사후조건의 깊이를 본다.
"""

from __future__ import annotations

import copy
import inspect
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from .bench_core import isolated_workspace

# ── 케이스 정의 ──────────────────────────────────────────────────────────

# (원본 메서드, 요청 인자 dict) → 실행기가 보고할 결과.
# 인자를 dict로 넘기는 이유: 호출부가 위치 인자를 쓰는지 키워드를 쓰는지에
# 변이 코드가 좌우되면, 잡히지 않은 변이와 하네스 버그를 구별할 수 없다.
Mutator = Callable[[Any, dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class MutationCase:
    """하나의 변이. 정상 케이스는 `mutate=None`이다."""

    case_id: str
    action: str
    kind: str
    description: str
    sheet: str
    rows: list[list[Any]]
    params: dict[str, Any]
    # 올바르게 수행됐을 때 파일에 남아야 하는 상태. 요청 범위 밖 이웃 칸도 넣는다 —
    # 엉뚱한 곳을 덮어쓰는 변이는 그 칸을 봐야만 잡힌다.
    expected_cells: dict[str, Any] = field(default_factory=dict)
    mutate: Mutator | None = None

    @property
    def is_clean(self) -> bool:
        return self.mutate is None


def _grid(values: list[list[Any]]) -> list[list[Any]]:
    return copy.deepcopy(values)


def _shift_cell(cell: str, *, rows: int = 0, cols: int = 0) -> str:
    letters = "".join(c for c in cell if c.isalpha()).upper()
    digits = int("".join(c for c in cell if c.isdigit()) or 1)
    index = 0
    for ch in letters:
        index = index * 26 + (ord(ch) - 64)
    index += cols
    out = ""
    while index > 0:
        index, rem = divmod(index - 1, 26)
        out = chr(65 + rem) + out
    return f"{out}{digits + rows}"


# ── write_range 변이 ─────────────────────────────────────────────────────
#
# 모두 **요청 기준의** 성공 결과를 돌려준다. 실행기가 거짓말을 하는 상황이므로
# written_cells도 address도 요청한 그대로다 — 실제 서비스가 돌려주는 모양과
# 같아야 한다. 여기서 좁은 주소를 돌려주면 검증기가 좁게 읽어서, 검증 공백이
# 아니라 하네스가 만든 착시가 된다.


def _written(args: dict[str, Any]) -> dict[str, Any]:
    values = args["values_2d"]
    rows = len(values)
    cols = max((len(r) for r in values), default=0)
    start = str(args["start_cell"])
    end = _shift_cell(start, rows=rows - 1, cols=cols - 1)
    return {"written_cells": rows * cols, "address": start if rows * cols == 1 else f"{start}:{end}"}


def _apply(real, args: dict[str, Any], **overrides) -> None:
    real(**{**args, **overrides})


def _write_wrong_value(real, args):
    """마지막 칸 하나만 다른 값이 들어간다."""
    mutated = _grid(args["values_2d"])
    mutated[-1][-1] = "WRONG"
    _apply(real, args, values_2d=mutated)
    return _written(args)


def _write_missing_cell(real, args):
    """마지막 칸을 아예 쓰지 않는다 — 기존 값이 그대로 남는다."""
    mutated = _grid(args["values_2d"])
    mutated[-1] = mutated[-1][:-1]
    if mutated[-1]:
        _apply(real, args, values_2d=mutated)
    return _written(args)


def _write_partial(real, args):
    """첫 행만 쓰고 나머지 행은 손대지 않는다. 가장 위험한 실패다."""
    _apply(real, args, values_2d=_grid(args["values_2d"])[:1])
    return _written(args)


def _write_shifted(real, args):
    """한 행 아래에 기록된다. 요청 범위는 비고 엉뚱한 칸이 덮인다."""
    _apply(real, args, start_cell=_shift_cell(str(args["start_cell"]), rows=1))
    return _written(args)


def _write_extra(real, args):
    """요청대로 쓰고, 범위 밖 칸까지 덮어쓴다 (부수 피해)."""
    _apply(real, args)
    _apply(
        real,
        args,
        start_cell=_shift_cell(str(args["start_cell"]), rows=len(args["values_2d"])),
        values_2d=[["침범"]],
    )
    return _written(args)


def _write_wrong_shape(real, args):
    """행과 열을 뒤집어 쓴다."""
    transposed = [list(row) for row in zip(*_grid(args["values_2d"]), strict=False)]
    _apply(real, args, values_2d=transposed)
    return _written(args)


def _write_narrow_address(real, args):
    """요청대로 쓰지 않고, 보고 주소까지 첫 칸으로 좁혀 부른다.

    검증기는 `result["address"]`를 믿고 그 범위만 다시 읽는다. 실행기가 주소를
    좁혀 보고하면 검증도 같이 좁아진다 — 검증이 실행기가 준 값에 기대는 한
    남는 구멍이다.
    """
    mutated = _grid(args["values_2d"])
    mutated[-1][-1] = "WRONG"
    _apply(real, args, values_2d=mutated)
    report = _written(args)
    report["address"] = str(args["start_cell"])
    return report


# ── clear_range 변이 ─────────────────────────────────────────────────────


def _cleared(args: dict[str, Any]) -> dict[str, Any]:
    return {"cleared_cells": 4, "address": str(args["target_range"])}


def _clear_none(real, args):
    """아무것도 지우지 않고 성공을 보고한다."""
    return _cleared(args)


def _clear_partial(real, args):
    """첫 행만 지운다."""
    first = str(args["target_range"]).split(":")[0]
    _apply(real, args, target_range=f"{first}:{_shift_cell(first, cols=1)}")
    return _cleared(args)


def _clear_wrong_range(real, args):
    """엉뚱한 범위를 지운다. 대상은 그대로 남고 멀쩡한 데이터가 사라진다."""
    first = str(args["target_range"]).split(":")[0]
    _apply(real, args, target_range=_shift_cell(first, rows=-1))
    return _cleared(args)


def _clear_value_remains(real, args, service):
    """지운 뒤 한 칸에 값이 되살아난다."""
    _apply(real, args)
    first = str(args["target_range"]).split(":")[0]
    service.write_range(args["workbook_id"], args["sheet_name"], first, [["잔존"]])
    return _cleared(args)


def _clear_formula_remains(real, args, service):
    """값은 지웠는데 수식 칸이 살아남는다."""
    _apply(real, args)
    first = str(args["target_range"]).split(":")[0]
    service.write_range(args["workbook_id"], args["sheet_name"], first, [["=1+1"]])
    return _cleared(args)


# ── 수트 ─────────────────────────────────────────────────────────────────

_SHEET = "데이터"
_ROWS: list[list[Any]] = [
    ["월", "지역", "금액"],
    ["1월", "서울", 100],
    ["2월", "부산", 200],
    ["3월", "대구", 300],
]

# A1:B2에 [[1,2],[3,4]]를 쓰는 요청. 원본 머리글을 덮는다.
_WRITE_PARAMS = {"start_cell": "A1", "values_2d": [[1, 2], [3, 4]]}
_WRITE_EXPECTED = {
    "A1": 1,
    "B1": 2,
    "A2": 3,
    "B2": 4,
    # 범위 밖은 그대로여야 한다. 덮어쓰기 변이는 이 칸에서만 드러난다.
    "C1": "금액",
    "A3": "2월",
}

# A2:B3을 비우는 요청.
_CLEAR_PARAMS = {"target_range": "A2:B3"}
_CLEAR_EXPECTED = {
    "A2": None,
    "B2": None,
    "A3": None,
    "B3": None,
    "A1": "월",
    "C2": 100,
}


def _write_case(kind: str, description: str, mutate: Mutator | None) -> MutationCase:
    return MutationCase(
        case_id=f"write_{kind}",
        action="excel_live.write_range",
        kind=kind,
        description=description,
        sheet=_SHEET,
        rows=_ROWS,
        params=dict(_WRITE_PARAMS),
        expected_cells=dict(_WRITE_EXPECTED),
        mutate=mutate,
    )


def _clear_case(kind: str, description: str, mutate: Mutator | None) -> MutationCase:
    return MutationCase(
        case_id=f"clear_{kind}",
        action="excel_live.clear_range",
        kind=kind,
        description=description,
        sheet=_SHEET,
        rows=_ROWS,
        params=dict(_CLEAR_PARAMS),
        expected_cells=dict(_CLEAR_EXPECTED),
        mutate=mutate,
    )


def all_cases() -> list[MutationCase]:
    return [
        # 정상 — false fail을 재는 대조군
        _write_case("clean", "요청대로 정확히 기록", None),
        _clear_case("clean", "요청 범위를 정확히 비움", None),
        # write_range 변이
        _write_case("wrong_value", "한 칸에 다른 값이 들어감", _write_wrong_value),
        _write_case("missing_cell", "한 칸을 쓰지 않아 기존 값이 남음", _write_missing_cell),
        _write_case("partial_write", "첫 행만 기록되고 나머지는 그대로", _write_partial),
        _write_case("shifted_range", "한 행 아래에 기록됨", _write_shifted),
        _write_case("extra_write", "요청 범위 밖까지 덮어씀", _write_extra),
        _write_case("wrong_shape", "행과 열이 뒤집혀 기록됨", _write_wrong_shape),
        _write_case("narrow_address", "틀리게 쓰고 보고 주소를 좁힘", _write_narrow_address),
        # clear_range 변이
        _clear_case("no_clear", "아무것도 지우지 않음", _clear_none),
        _clear_case("partial_clear", "첫 행만 지워짐", _clear_partial),
        _clear_case("wrong_range_clear", "엉뚱한 범위를 지움", _clear_wrong_range),
        _clear_case("value_remains", "지운 자리에 값이 남음", _clear_value_remains),
        _clear_case("formula_remains", "지운 자리에 수식이 남음", _clear_formula_remains),
    ]


# ── 실행 ────────────────────────────────────────────────────────────────

# 어느 단계의 검증기를 태울지. 과거 판정을 재현하려면 강화된 검증 함수를 끈다.
STAGES = ("V0", "V1", "V2")
_STAGE_LABEL = {
    "V0": "검증 강화 이전",
    "V1": "+ write_range 상태 검증",
    "V2": "+ clear_range 상태 검증",
}


def stage_label(stage: str) -> str:
    return _STAGE_LABEL.get(stage, stage)


def _install_mutation(service: Any, case: MutationCase) -> None:
    """실행기를 감싼다. 호출 인자는 시그니처로 묶어 항상 이름으로 넘긴다."""
    if case.mutate is None:
        return
    name = case.action.split(".")[-1]
    real = getattr(service, name)
    signature = inspect.signature(real)
    mutate = case.mutate
    wants_service = "service" in inspect.signature(mutate).parameters

    def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
        bound = signature.bind(*args, **kwargs)
        bound.apply_defaults()
        resolved = dict(bound.arguments)
        if wants_service:
            return mutate(real, resolved, service)
        return mutate(real, resolved)

    setattr(service, name, wrapper)


def _neutralize_verifiers(stage: str, monkey: list[tuple[Any, str, Any]]) -> None:
    """단계에 맞춰 강화된 검증 함수를 무력화한다.

    프로덕션 코드에 단계 플래그를 심지 않는다. 과거 판정 재현은 측정 하네스의
    사정이지 제품의 사정이 아니다.
    """
    from office_claw_sidecar.services import excel_result_verifier as verifier

    passthrough = ["_verify_write", "_verify_clear"] if stage == "V0" else []
    if stage == "V1":
        passthrough = ["_verify_clear"]
    for name in passthrough:
        monkey.append((verifier, name, getattr(verifier, name)))
        setattr(verifier, name, lambda *a, **kw: (True, ""))


def _ground_truth(path: Path, case: MutationCase) -> tuple[bool, dict[str, Any]]:
    """파일을 직접 열어 최종 상태를 확인한다. 서비스를 거치지 않는다."""
    worksheet = load_workbook(path, data_only=False)[case.sheet]
    actual = {cell: worksheet[cell].value for cell in case.expected_cells}
    ok = all(actual[cell] == expected for cell, expected in case.expected_cells.items())
    return ok, actual


def run_case(case: MutationCase, *, stage: str = "V2") -> dict[str, Any]:
    """한 변이를 실행하고 검증기 판정과 파일 실제 상태를 나란히 기록한다."""
    from office_claw_sidecar.routers.excel_live import (
        _execute_action,
        _verify_step_result,
        get_excel_live_service,
    )

    restore: list[tuple[Any, str, Any]] = []
    _neutralize_verifiers(stage, restore)
    try:
        with isolated_workspace() as root:
            from .bench_core import BenchCase, build_workbook

            path = build_workbook(
                root,
                BenchCase(
                    case_id=case.case_id,
                    category="mutation",
                    prompt="",
                    sheet=case.sheet,
                    rows=case.rows,
                    expectation=None,  # type: ignore[arg-type]
                ),
            )
            service = get_excel_live_service()
            _install_mutation(service, case)

            error = ""
            verifier_passed = True
            detail = ""
            try:
                result = _execute_action(
                    action=case.action,
                    params=dict(case.params),
                    workbook_id=str(path),
                    sheet_name=case.sheet,
                )
                checked = _verify_step_result(
                    action=case.action,
                    params=dict(case.params),
                    result=result,
                    workbook_id=str(path),
                    sheet_name=case.sheet,
                )
                verifier_passed, detail = (
                    checked if isinstance(checked, tuple) else (bool(checked), "")
                )
                _execute_action(
                    action="excel_live.save_workbook",
                    params={},
                    workbook_id=str(path),
                    sheet_name=case.sheet,
                )
            except Exception as exc:  # noqa: BLE001 - 실행 실패도 판정 결과다
                error = f"{type(exc).__name__}: {exc}"
                verifier_passed = False

            truth, actual = _ground_truth(path, case)

            if verifier_passed and not truth:
                classification = "false_pass"
            elif not verifier_passed and truth:
                classification = "false_fail"
            elif verifier_passed:
                classification = "true_pass"
            else:
                classification = "true_fail"

            return {
                "case": case.case_id,
                "action": case.action.split(".")[-1],
                "kind": case.kind,
                "description": case.description,
                "params": dict(case.params),
                "expected": dict(case.expected_cells),
                "actual": actual,
                "verifier_passed": verifier_passed,
                "verifier_detail": detail,
                "ground_truth_pass": truth,
                "classification": classification,
                "error": error,
            }
    finally:
        for target, name, original in restore:
            setattr(target, name, original)


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """false pass / false fail 비율. 두 개를 같이 봐야 의미가 있다."""
    broken = [r for r in rows if not r["ground_truth_pass"]]
    intact = [r for r in rows if r["ground_truth_pass"]]
    false_pass = [r for r in broken if r["verifier_passed"]]
    false_fail = [r for r in intact if not r["verifier_passed"]]
    return {
        "cases": len(rows),
        "broken_states": len(broken),
        "intact_states": len(intact),
        "false_pass": len(false_pass),
        "false_fail": len(false_fail),
        "false_pass_rate": round(len(false_pass) / len(broken), 4) if broken else 0.0,
        "false_fail_rate": round(len(false_fail) / len(intact), 4) if intact else 0.0,
        "missed_kinds": [r["kind"] for r in false_pass],
    }
