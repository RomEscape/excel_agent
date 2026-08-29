"""검증기 변이 수트의 회귀 고정.

수치를 박아 두는 이유: 검증기를 손댔을 때 false pass가 늘어나는 것도, 반대로
false fail이 생기는 것도 여기서 즉시 드러나야 한다. 특히 두 번째가 중요하다 —
"항상 False를 돌려주면 false pass는 0%"라는 유혹을 막는 장치다.
"""

from __future__ import annotations

import pytest

from tests.excel_e2e.verifier_mutants import all_cases, run_case, summarize

# 현재 검증기가 원리상 잡을 수 없는 변이. 요청 범위만 다시 읽으므로 그 바깥의
# 부수 피해는 보이지 않는다. 잡으려면 실행 전 전체 스냅샷이 필요하다.
KNOWN_BLIND_SPOTS = {"extra_write"}


@pytest.fixture(scope="module")
def stages() -> dict[str, dict]:
    return {
        stage: summarize([run_case(case, stage=stage) for case in all_cases()])
        for stage in ("V0", "V1", "V2")
    }


def test_baseline_lets_every_broken_state_through(stages):
    """수정 이전에는 깨진 상태를 전부 통과시켰다. 개선폭의 기준점이다."""
    assert stages["V0"]["false_pass_rate"] == 1.0


def test_each_stage_reduces_the_verification_gap(stages):
    rates = [stages[s]["false_pass_rate"] for s in ("V0", "V1", "V2")]
    assert rates[0] > rates[1] > rates[2], f"단계별로 줄어야 한다: {rates}"


def test_current_verifier_only_misses_the_documented_blind_spot(stages):
    assert set(stages["V2"]["missed_kinds"]) == KNOWN_BLIND_SPOTS


@pytest.mark.parametrize("stage", ["V0", "V1", "V2"])
def test_correct_work_is_never_blocked(stages, stage):
    """과잉 검증 금지. 멀쩡한 작업을 막으면 에이전트가 망가진다."""
    assert stages[stage]["false_fail"] == 0


def test_clean_cases_actually_produce_a_correct_file():
    """대조군이 실제로 정상이어야 false fail 측정이 의미를 갖는다."""
    for case in all_cases():
        if not case.is_clean:
            continue
        row = run_case(case, stage="V2")
        assert row["ground_truth_pass"], f"{case.case_id}: {row['actual']}"
        assert row["verifier_passed"], f"{case.case_id}: {row['verifier_detail']}"


def test_every_mutation_actually_breaks_the_file():
    """변이가 파일을 안 깨면 그 케이스는 아무것도 재지 않는다."""
    for case in all_cases():
        if case.is_clean:
            continue
        row = run_case(case, stage="V2")
        assert not row["ground_truth_pass"], f"{case.case_id}가 파일을 깨뜨리지 못했다"
        assert not row["error"], f"{case.case_id} 하네스 오류: {row['error']}"


def test_verifier_does_not_trust_the_address_reported_by_the_executor():
    """검증 범위는 요청에서 계산한다. 실행기가 준 주소를 믿으면 같이 좁아진다."""
    case = next(c for c in all_cases() if c.kind == "narrow_address")
    row = run_case(case, stage="V2")
    assert not row["verifier_passed"]
    assert "write_value_mismatch" in row["verifier_detail"]
