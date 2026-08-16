"""벤치마크 하네스 자체 검증 — LLM도 Excel도 없이 돈다.

벤치마크 숫자를 믿으려면 먼저 두 가지가 성립해야 한다.

1. 정답 계획(oracle)을 넣으면 통과한다 → 단언이 지나치게 빡빡하지 않다
2. 틀린 계획(mutant)을 넣으면 떨어진다 → 단언이 헐겁지 않다

2번이 특히 중요하다. `sort_range`를 부르기만 하면 통과하는 검사라면 액션 이름만
채점하는 기존 평가와 다를 게 없다.
"""

from __future__ import annotations

import pytest

pytest.importorskip("openpyxl")

from tests.excel_e2e.bench_cases import all_cases
from tests.excel_e2e.bench_core import run_plan

CASES = all_cases()
CASE_IDS = [case.case_id for case in CASES]


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_oracle_plan_passes(case):
    outcome = run_plan(case, case.oracle)
    assert not outcome.error, f"{case.case_id} 실행 오류: {outcome.error}"
    assert outcome.passed, f"{case.case_id} 정답 계획인데 실패: {outcome.detail}"


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_mutant_plan_fails(case):
    """틀린 계획은 반드시 떨어져야 한다. 실행 자체가 터져도 실패로 친다."""
    outcome = run_plan(case, case.mutant)
    assert not outcome.passed or outcome.error, (
        f"{case.case_id} 틀린 계획인데 통과했습니다 — 단언이 헐겁습니다: {outcome.detail}"
    )


def test_empty_plan_fails_everything():
    """아무것도 안 하면 전부 떨어져야 한다. 초기 상태가 이미 정답이면 안 된다."""
    survivors = [case.case_id for case in CASES if run_plan(case, []).passed]
    assert not survivors, f"빈 계획으로 통과한 케이스: {survivors}"


def test_cases_cover_intended_categories():
    categories = {case.category for case in CASES}
    assert {"write", "clear", "formula", "sort", "filter", "format"} <= categories


def test_tool_calling_dispatcher_drift_is_documented():
    """액션 디스패처가 두 개이고 지원 범위가 다르다는 사실을 고정해 둔다.

    `/command`는 라우터의 `_execute_action`(42종)을 타지만, tool-calling 경로가
    쓰는 `excel_actions.execute_excel_action`은 16종만 처리한다. 후자에는
    sort_range·clear_range·fill_range·create_table 같은 핵심 액션이 없다.

    tool-calling 위에 새 오케스트레이터를 얹으려면 이 격차부터 메워야 한다.
    격차가 줄면 이 테스트가 실패하면서 알려준다 — 그때 숫자를 낮추면 된다.
    """
    import re
    from pathlib import Path

    def dispatched(path: str, marker: str) -> set[str]:
        text = Path(path).read_text(encoding="utf-8")
        body = text[text.index(marker) :]
        return set(re.findall(r'action == "excel_live\.([a-z_]+)"', body))

    router = dispatched(
        "office_claw_sidecar/routers/excel_live.py", "def _execute_action("
    )
    actions = dispatched(
        "office_claw_sidecar/services/excel_actions.py", "def execute_excel_action("
    )

    missing = router - actions
    assert {"sort_range", "clear_range", "fill_range"} <= missing, (
        "격차가 메워졌다면 이 테스트를 갱신하세요."
    )
    # 2026-08-16: 라우터에 액션이 늘어 격차가 32 -> 37종이 됐다. 격차 자체는 알려진 상태이고
    # (tool-calling 경로만 영향), 이 핀은 "모르는 사이에 더 벌어지는 것"을 막는 용도다.
    assert len(missing) == 37, f"디스패처 격차가 {len(missing)}종으로 변했습니다: {sorted(missing)}"
