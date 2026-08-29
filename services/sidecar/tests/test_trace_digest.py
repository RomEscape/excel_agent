"""반복 실행한 턴 로그를 케이스 단위로 접는 집계기 테스트.

이 집계기가 존재하는 이유는 하나다 — **결정적 결함과 비결정적 결함을 가르는 것**.
둘을 섞어 보면 진단이 어긋난다. 매번 같게 깨지는 것은 코드에서 찾으면 나오지만,
들쭉날쭉한 것은 몇 번 돌려 보고 "고쳐졌다"고 착각하기 쉽다.
"""

from __future__ import annotations

from office_claw_sidecar.services import trace_digest
from office_claw_sidecar.services.trace_report import (
    BROKEN,
    DEFERRED,
    DONE,
    outcome_class,
)


def _turn(case, *, routes=(), ok=True, ask=False, action="excel_live.write_range", **outcome):
    return {
        "turn_id": f"t{case}{len(routes)}",
        "message": f"{case} 명령",
        "source": {"kind": "diagnostic", "case": case},
        "elapsed_ms": 100.0,
        "routes": [{"at": r} for r in routes],
        "stages": [],
        "outcome": {"ok": ok, "ask_follow_up": ask, "action": action, **outcome},
    }


def _ok(case):
    return _turn(case, routes=("planner:llm", "execute", "final:ok"))


def _verify_failed(case, why="값 불일치"):
    turn = _turn(case, routes=("planner:llm", "final:failed"), ok=False)
    turn["routes"].insert(1, {"at": "verify:failed", "why": why})
    return turn


def _asked(case):
    return _turn(case, routes=("planner:llm", "final:asked_back"), ok=False, ask=True)


# ── 세 부류 가르기 ─────────────────────────────────────────────────────────


def test_a_case_that_always_works_is_marked_stable():
    digest = trace_digest.build([_ok("값입력") for _ in range(3)])
    (case,) = digest.cases
    assert case.runs == 3
    assert case.state == f"stable_{DONE}"
    assert not case.flaky


def test_a_case_that_always_breaks_the_same_way_is_deterministic():
    digest = trace_digest.build([_verify_failed("정렬") for _ in range(3)])
    (case,) = digest.cases
    assert case.state == f"stable_{BROKEN}"
    assert not case.flaky, "매번 같게 깨지는 것은 들쭉날쭉이 아니다 — 코드에 원인이 있다"
    assert case.top_detail == "값 불일치"


def test_a_case_with_mixed_verdicts_is_flagged_flaky():
    """같은 입력에 판정이 갈리면 코드를 뒤지기 전에 이것부터 알아야 한다."""
    digest = trace_digest.build([_ok("정렬"), _verify_failed("정렬"), _ok("정렬")])
    (case,) = digest.cases
    assert case.flaky
    assert case.state == "flaky"
    assert case.verdicts["ok"] == 2
    assert case.done_runs == 2


def test_flaky_cases_come_first():
    """읽는 사람이 가장 먼저 봐야 할 것이 맨 위에 온다."""
    turns = [_ok("잘됨"), _ok("잘됨"), _ok("갈림"), _verify_failed("갈림")]
    digest = trace_digest.build(turns)
    assert digest.cases[0].case == "갈림"


# ── 되물음을 성공으로 세지 않는다 ─────────────────────────────────────────


def test_asking_back_is_not_counted_as_done():
    """되물음은 에러가 아니지만 파일은 그대로다. 성공으로 세면 이행률이 부풀려진다."""
    digest = trace_digest.build([_asked("모호한명령") for _ in range(2)])
    (case,) = digest.cases
    assert case.state == f"stable_{DEFERRED}"
    assert case.done_runs == 0
    assert digest.done_rate == 0.0
    assert digest.by_class[DEFERRED] == 2


def test_done_rate_counts_only_real_work():
    digest = trace_digest.build([_ok("a"), _ok("a"), _asked("b"), _verify_failed("c")])
    assert digest.done_rate == 0.5
    assert digest.by_class[DONE] == 2
    assert digest.by_class[DEFERRED] == 1
    assert digest.by_class[BROKEN] == 1


def test_outcome_class_treats_unknown_codes_as_broken():
    """분류에서 빠진 판정을 성공 쪽에 넣으면 문제가 숨는다."""
    assert outcome_class("무언가_새로운_코드") == BROKEN
    assert outcome_class("verify_recovered") == DONE


# ── 경로가 갈리는 것 ───────────────────────────────────────────────────────


def test_same_verdict_through_different_routes_is_surfaced():
    """결말이 같아도 도착한 길이 다르면 한 원인으로 묶어 고치면 안 된다."""
    first = _turn("표만들기", routes=("quick_rule:hit", "final:ok"))
    second = _turn("표만들기", routes=("planner:llm", "final:ok"))
    (case,) = trace_digest.build([first, second]).cases
    assert not case.flaky
    assert case.route_flaky
    assert len(case.routes) == 2


def test_repeated_route_steps_fold_into_one_entry():
    """재계획으로 같은 칸을 두 번 지난 것과 한 번 지난 것은 다른 경로다."""
    once = _turn("재시도", routes=("planner:llm", "final:ok"))
    twice = _turn("재시도", routes=("planner:llm", "planner:llm", "final:ok"))
    (case,) = trace_digest.build([once, twice]).cases
    assert case.route_flaky
    assert any("×2" in path for path in case.routes)


# ── 케이스 묶는 기준 ───────────────────────────────────────────────────────


def test_turns_without_a_case_tag_group_by_message():
    """실제 앱 트래픽에는 태그가 없다. 그때는 사용자 문장으로 묶는다."""
    turn = _ok("무시됨")
    del turn["source"]
    turn["message"] = "H3에 120 입력해줘"
    (case,) = trace_digest.build([turn]).cases
    assert case.case == "H3에 120 입력해줘"


def test_the_report_keeps_turn_ids_for_drilling_down():
    """집계에서 이상한 케이스를 찾으면 그 턴을 바로 펼쳐 볼 수 있어야 한다."""
    report = trace_digest.to_report(trace_digest.build([_ok("값입력"), _verify_failed("값입력")]))
    (case,) = report["case_detail"]
    assert case["state"] == "flaky"
    assert len(case["turn_ids"]) == 2
    assert report["done_rate"] == 0.5


def test_render_does_not_crash_on_an_empty_log():
    assert trace_digest.render(trace_digest.build([]))


# ── 성공이라 했지만 요청한 일을 안 한 경우 ────────────────────────────────


def _executed(case, actions, *, expect=""):
    """실행 기록을 가진 성공 턴. `expect`는 이 요청이 당연히 해야 할 액션."""
    turn = _turn(case, routes=("planner:llm", "final:ok"))
    turn["stages"] = [
        {"stage": "executed", "steps": [{"action": a, "ok": True, "verified": True} for a in actions]}
    ]
    if expect:
        turn["source"]["expect"] = expect
    return turn


def test_running_a_different_action_and_reporting_success_is_caught():
    """차트를 요청했는데 피벗만 만들고 '검증 통과·성공'으로 끝난 실제 사례."""
    turns = [_executed("차트", ["excel_live.pivot_table"], expect="excel_live.create_chart") for _ in range(3)]
    digest = trace_digest.build(turns)
    (case,) = digest.cases

    assert case.dominant == "ok", "시스템은 성공이라고 판정했다 — 그게 문제의 핵심이다"
    assert case.state == "silent_wrong"
    assert case.missed["excel_live.create_chart"] == 3
    assert digest.silently_wrong == 3


def test_doing_the_asked_action_is_not_flagged():
    turns = [_executed("차트", ["excel_live.create_chart"], expect="excel_live.create_chart")]
    (case,) = trace_digest.build(turns).cases
    assert case.state == f"stable_{DONE}"
    assert not case.goal_missed


def test_the_asked_action_can_come_with_extra_steps():
    """피벗을 만들고 그 위에 차트를 얹는 것은 정상이다."""
    turns = [
        _executed(
            "차트",
            ["excel_live.pivot_table", "excel_live.create_chart"],
            expect="excel_live.create_chart",
        )
    ]
    (case,) = trace_digest.build(turns).cases
    assert not case.goal_missed


def test_cases_without_an_expectation_are_never_flagged():
    """기대 액션을 안 정한 케이스까지 채점하면 거짓 경보가 난다."""
    (case,) = trace_digest.build([_executed("값입력", ["excel_live.write_range"])]).cases
    assert case.state == f"stable_{DONE}"
    assert not case.goal_missed


def test_silently_wrong_cases_are_listed_first():
    """가장 위험한 것이 맨 위에 온다 — 지표만 보면 깨끗해 보이기 때문이다."""
    turns = [
        _ok("잘됨"),
        _verify_failed("대놓고깨짐"),
        _executed("조용히틀림", ["excel_live.pivot_table"], expect="excel_live.create_chart"),
    ]
    digest = trace_digest.build(turns)
    assert digest.cases[0].case == "조용히틀림"


def test_the_report_records_which_action_was_skipped():
    report = trace_digest.to_report(
        trace_digest.build([_executed("차트", ["excel_live.pivot_table"], expect="excel_live.create_chart")])
    )
    assert report["silently_wrong"] == 1
    assert report["case_detail"][0]["missed_actions"] == {"excel_live.create_chart": 1}
