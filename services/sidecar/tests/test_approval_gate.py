"""승인 경로가 계획을 끝까지 실행하는지 지킨다.

원래는 승인 게이트가 첫 CONFIRM 단계 하나만 `_pending_approvals`에 담고 나머지를
버렸다. 쓰기 계열 액션이 전부 CONFIRM이라 다단계 계획은 승인 직후 첫 단계만
실행되고 끝났고, `post_approval`이 `_execute_action`을 직접 부르는 바람에
검증·롤백·재계획도 통째로 우회됐다.

이 테스트들은 처음에 그 결함을 고정하려고 썼고, 승인을 plan 단위로 바꾼 뒤
기대값을 뒤집었다. 지금은 **회귀 방지**다. 승인 경로가 다시 잘리기 시작하면
여기서 걸린다.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from office_claw_sidecar.main import app
from tests.excel_e2e import approval_gate

client = TestClient(app)


@pytest.fixture(scope="module")
def outcomes():
    """전체 수트를 한 번만 돌려 재사용한다.

    케이스마다 경로를 2~4번 태우므로 테스트마다 다시 돌리면 느리다.
    module 스코프라 `monkeypatch` 픽스처(함수 스코프)를 쓸 수 없어 직접 만든다.
    """
    patcher = pytest.MonkeyPatch()
    try:
        return approval_gate.run_all(client, patcher)
    finally:
        patcher.undo()


def _by_id(outcomes, case_id):
    return next(o for o in outcomes if o.case_id == case_id)


def test_approval_runs_the_whole_plan_not_just_the_first_step(outcomes):
    """승인 경로가 대조 경로와 똑같은 수의 단계를 실행한다.

    "어차피 하나만 실행된다"고 가정하지 않고, 응답이 보고한 실행 단계 수를
    두 경로에서 각각 세어 비교한다.
    """
    multi = [o for o in outcomes if o.planned_steps > 1]
    assert multi, "다단계 케이스가 있어야 이 회귀를 막을 수 있다"

    for outcome in multi:
        assert outcome.approval_required, f"{outcome.case_id}: CONFIRM 계획은 승인을 요구해야 한다"
        assert outcome.lost_steps == 0, (
            f"{outcome.case_id}: 대조 {outcome.direct.executed_steps}단계 vs "
            f"승인 {outcome.gated.executed_steps}단계 — {outcome.lost_steps}단계가 사라졌다"
        )


def test_approved_work_matches_what_the_plan_intended(outcomes):
    """승인하면 파일이 계획대로 끝난다.

    사용자가 승인 버튼을 누르고 "실행되었습니다"를 봤다면, 파일도 계획의 마지막
    단계까지 반영돼 있어야 한다.
    """
    for outcome in outcomes:
        assert outcome.direct.file_correct, (
            f"{outcome.case_id}: 대조 경로(approve=true)는 계획대로 끝나야 한다. "
            f"실제 {outcome.direct.cells}"
        )
        assert outcome.gated.file_correct, (
            f"{outcome.case_id}: 승인 경로도 같아야 한다. 실제 {outcome.gated.cells}"
        )
        assert not outcome.diverged, (
            f"{outcome.case_id}: 두 경로의 최종 상태가 같아야 한다. "
            f"대조 {outcome.direct.cells} / 승인 {outcome.gated.cells}"
        )


def test_approval_keeps_the_formatting_step(outcomes):
    """서식 단계도 살아남는다.

    `[write_range, fill_range]`에서 잘리던 건 강조였다. 셀 값만 보는 검사는 이
    손실을 통과시켰으므로, 배경색을 따로 확인한다.
    """
    formatting = [o for o in outcomes if o.loss_kind == "formatting"]
    assert formatting, "서식을 확인하는 케이스가 있어야 한다"

    for outcome in formatting:
        assert outcome.direct.fill_applied, f"{outcome.case_id}: 대조 경로는 칠해야 한다"
        assert outcome.gated.fill_applied, f"{outcome.case_id}: 승인 경로도 칠해야 한다"
        assert not outcome.formatting_lost


def test_approval_keeps_the_verification_step(outcomes):
    """검증 단계도 실행된다.

    `[set_formula, verify_formula_result]`에서 잘리던 건 검증이었다. 수식은
    들어갔으니 파일도 응답도 정상으로 보여서, 파일 상태만 보는 지표로는 이 손실을
    잡을 수 없었다. 그래서 여기서는 실행 단계 수를 본다.
    """
    silent = [o for o in outcomes if o.loss_kind == "verification" and o.planned_steps > 1]
    assert silent, "검증 단계를 가진 케이스가 있어야 한다"

    for outcome in silent:
        assert outcome.lost_steps == 0, (
            f"{outcome.case_id}: 검증 단계가 사라졌다 — 파일만 봐서는 안 보인다"
        )
        assert outcome.gated.executed_steps == outcome.direct.executed_steps


def test_a_single_step_plan_survives_approval(outcomes):
    """대조군 — 단계가 하나면 잘릴 것이 없다.

    이게 실패하면 하네스가 승인 경로를 무조건 실패로 찍고 있다는 뜻이다.
    """
    control = _by_id(outcomes, "single_step_control")

    assert control.planned_steps == 1
    assert control.lost_steps == 0
    assert control.gated.file_correct
    assert not control.diverged


def test_approval_path_still_verifies_and_rolls_back_a_lying_write(outcomes):
    """승인 경로에서도 검증기와 롤백이 살아 있다.

    `post_approval`이 `_execute_action`을 직접 부르던 시절에는 실행기가 거짓말하면
    틀린 값이 그대로 남았다. write_range false pass를 0%로 만들어 둔 검증기가
    정작 사용자가 타는 경로에서는 호출조차 되지 않았다. 이제 두 경로가 같은
    실행 루프를 탄다.
    """
    control = _by_id(outcomes, "single_step_control")

    assert control.rollback_measured
    assert control.rollback_direct == 999, "대조 경로는 원래 값으로 되돌려야 한다"
    assert control.rollback_gated == 999, "승인 경로도 되돌려야 한다"
    assert not control.rollback_lost


def test_a_longer_plan_does_not_lose_more(outcomes):
    """3단계 계획도 2단계와 마찬가지로 전부 실행된다.

    예전에는 손실이 단계 수에 비례해서 커졌다.
    """
    two = _by_id(outcomes, "write_then_highlight")
    three = _by_id(outcomes, "write_three_cells")

    assert two.lost_steps == 0
    assert three.lost_steps == 0
    assert three.gated.executed_steps == 3


def test_summary_numbers_are_consistent(outcomes):
    """보고서 집계가 케이스별 값과 어긋나지 않는지."""
    summary = approval_gate.summarize(outcomes)

    assert summary["cases"] == len(outcomes)
    assert summary["lost_steps"] == sum(o.lost_steps for o in outcomes)
    assert summary["direct_file_correct"] == len(outcomes), "대조 경로는 전부 통과해야 한다"
    assert summary["gated_file_correct"] == len(outcomes), "승인 경로도 전부 통과해야 한다"
    assert summary["completion_rate"] == 1.0


# ── 승인 레코드의 계약 ────────────────────────────────────────────────────
# 파일 상태만 봐서는 안 보이는 것들. 무엇이 보관되고, 승인 후 무엇이 실행되는가.

THREE_STEP_PLAN = [
    {"action": "excel_live.write_range", "params": {"start_cell": "E1", "values_2d": [["하나"]]}},
    {"action": "excel_live.write_range", "params": {"start_cell": "E2", "values_2d": [["둘"]]}},
    {"action": "excel_live.write_range", "params": {"start_cell": "E3", "values_2d": [["셋"]]}},
]


def _request_approval(monkeypatch):
    """3단계 계획을 승인 대기 상태까지 몰고 간다."""
    from office_claw_sidecar.routers import excel_live as router

    workbook_id = approval_gate.isolated_workbook(monkeypatch)
    approval_gate.install_fixed_plan(monkeypatch, router, THREE_STEP_PLAN)
    body = client.post(
        "/excel-live/command",
        json={
            "message": "세 셀에 순서대로 채운다",
            "workbook_id": workbook_id,
            "sheet_name": "Sheet1",
            "approve": False,
        },
        headers=approval_gate.HEADERS,
    ).json()

    assert body["approval_required"] is True, body
    return router, body, body["pending_approval"]["approval_id"]


@pytest.fixture
def pending_three_steps(monkeypatch):
    """승인 대기 레코드까지 만들어 준다."""
    router, body, approval_id = _request_approval(monkeypatch)
    return body, approval_id, router._pending_approvals[approval_id]


def test_the_whole_plan_is_kept_while_waiting_for_approval(pending_three_steps):
    """승인 대기 레코드에 계획 전체가 들어 있다.

    한 단계만 담기면 나머지는 승인해도 돌아올 곳이 없다.
    """
    _, _, pending = pending_three_steps

    assert pending.resume is not None, "이어서 실행할 컨텍스트가 있어야 한다"
    assert [step.action for step in pending.resume.plan] == [
        "excel_live.write_range",
        "excel_live.write_range",
        "excel_live.write_range",
    ]
    assert [step.params["start_cell"] for step in pending.resume.plan] == ["E1", "E2", "E3"]
    assert pending.resume.approved, "승인 후 재개할 때 게이트가 다시 서면 무한 루프가 된다"


def test_the_dialog_lists_every_step_being_approved(pending_three_steps):
    """사용자가 첫 단계만 보고 계획 전체를 승인하게 두지 않는다."""
    body, _, _ = pending_three_steps
    summary = body["pending_approval"]["summary"]

    assert "3단계" in summary
    assert summary.count("엑셀 셀 값을 수정합니다.") == 3


def test_rejecting_throws_the_whole_plan_away(pending_three_steps):
    """거부하면 계획이 남지 않는다 — 다음 승인에 섞여 실행되면 안 된다."""
    from office_claw_sidecar.routers import excel_live as router

    _, approval_id, _ = pending_three_steps
    out = client.post(
        "/excel-live/approval",
        json={"approval_id": approval_id, "approved": False},
        headers=approval_gate.HEADERS,
    ).json()

    assert out["ok"] is True
    assert out["result"]["approved"] is False
    assert approval_id not in router._pending_approvals


def test_approving_executes_the_approved_plan_without_replanning(monkeypatch):
    """승인한 계획과 실행되는 계획이 같다.

    승인 후 다시 계획하면 사용자가 본 적 없는 작업이 실행될 수 있다. 재개 경로는
    보관된 계획을 그대로 쓰고 플래너를 부르지 않아야 한다.
    """
    router, _, approval_id = _request_approval(monkeypatch)

    def _explode(*args, **kwargs):
        raise AssertionError("승인 후에는 플래너를 다시 부르면 안 된다")

    monkeypatch.setattr(router, "parse_command_plan_with_llm", _explode)
    out = client.post(
        "/excel-live/approval",
        json={"approval_id": approval_id, "approved": True},
        headers=approval_gate.HEADERS,
    )

    assert out.status_code == 200
    assert approval_id not in router._pending_approvals
