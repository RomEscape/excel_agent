"""턴 트레이스가 실패 원인을 가를 수 있는 형태로 남는지 확인한다."""

from __future__ import annotations

import json

from office_claw_sidecar.services import decision_trace
from office_claw_sidecar.services.decision_trace import (
    Long,
    route,
    route_path,
    turn_scope,
)
from office_claw_sidecar.services.trace_report import classify, read_turns, render


def _turn(routes=(), stages=(), outcome=None, message="정렬해줘"):
    return {
        "turn_id": "abc123",
        "at": "2026-08-11T02:00:00+09:00",
        "endpoint": "POST /excel-live/command",
        "message": message,
        "elapsed_ms": 1200.0,
        "routes": [{"at": r} if isinstance(r, str) else r for r in routes],
        "stages": list(stages),
        "outcome": outcome or {},
    }


# ── 경로 기록 ────────────────────────────────────────────────────────────


def test_route_records_the_path_in_order(tmp_path, monkeypatch):
    log = tmp_path / "chat_log.jsonl"
    monkeypatch.setattr(decision_trace, "get_chat_log_path", lambda: log)

    with turn_scope(endpoint="POST /excel-live/command", message="정렬해줘"):
        route("quick_rule:miss", why="규칙으로 확정하지 못함")
        route("planner:local")
        route("final:ok")

    entry = json.loads(log.read_text(encoding="utf-8").strip())
    assert route_path(entry) == "quick_rule:miss → planner:local → final:ok"
    assert entry["routes"][0]["why"] == "규칙으로 확정하지 못함"
    assert entry["routes"][0]["at_ms"] >= 0


def test_route_outside_a_turn_is_ignored():
    route("고아 경로")  # 예외 없이 무시되어야 한다


def test_repeated_route_steps_are_folded():
    """재시도가 같은 칸을 두 번 지나면 경로가 길어져 읽기 어렵다."""
    from office_claw_sidecar.services.trace_report import route_path as report_path

    turn = _turn(routes=["execute", "verify:failed", "verify:failed", "final:failed"])
    assert report_path(turn) == "execute → verify:failed×2 → final:failed"


def test_nested_plan_params_survive_double_compaction():
    """계획 파라미터는 plan_summary와 note를 연달아 지난다. 여기서 뭉개지면 안 된다."""
    steps = decision_trace.plan_summary(
        [{"action": "excel_live.write_range", "params": {"values_2d": [[120]]}}]
    )
    assert decision_trace.compact(steps)[0]["params"]["values_2d"] == [[120]]


def test_long_text_survives_the_default_truncation():
    """LLM 원본 응답이 400자에서 잘리면 계획 JSON을 확인할 수 없다."""
    text = "가" * 1500
    assert len(str(decision_trace.compact(text))) < 500
    assert len(str(decision_trace.compact(Long(text)))) == 1500


# ── 실패 분류 ────────────────────────────────────────────────────────────


def test_execution_error_outranks_everything_else():
    turn = _turn(
        routes=[{"at": "execute:error", "why": "KeyError: 매출"}, "verify:failed"],
        outcome={"ok": False},
    )
    assert classify(turn).code == "executor"


def test_verify_failure_without_replan_is_a_loop_gap():
    turn = _turn(
        routes=[{"at": "verify:failed", "why": "sort_not_applied"}, "final:failed"],
        outcome={"ok": False},
    )
    verdict = classify(turn)
    assert verdict.code == "loop_missing"
    assert "sort_not_applied" in verdict.detail


def test_verify_failure_with_replan_is_separated():
    turn = _turn(
        routes=["verify:failed", "replan:1", "final:failed"],
        outcome={"ok": False},
    )
    assert classify(turn).code == "verify_failed"


def test_replan_that_recovered_is_not_a_failure():
    turn = _turn(routes=["verify:failed", "replan:1", "final:ok"], outcome={"ok": True})
    assert classify(turn).code == "verify_recovered"


def test_unparsable_planner_output_is_its_own_category():
    turn = _turn(routes=["planner:json_missing", "final:failed"], outcome={"ok": False})
    assert classify(turn).code == "planner_output"


def test_asking_back_is_not_a_failure():
    turn = _turn(routes=["final:asked_back"], outcome={"ok": True, "ask_follow_up": True})
    assert classify(turn).code == "asked_back"


def test_unhandled_exception_is_reported_as_a_crash():
    turn = _turn(outcome={"ok": False, "error_type": "ValueError", "error": "boom"})
    verdict = classify(turn)
    assert verdict.code == "crash"
    assert "ValueError" in verdict.detail


# ── 렌더링 ───────────────────────────────────────────────────────────────


def test_render_shows_what_the_model_saw_and_what_it_chose():
    turn = _turn(
        message="매출 높은 순으로 정렬해줘",
        routes=["quick_rule:miss", "planner:local", {"at": "verify:failed", "why": "sort_not_applied"}],
        stages=[
            {
                "stage": "observation",
                "sheet_name": "Sheet1",
                "used_range": "A1:F100",
                "context_range": "(없음)",
                "headers": ["날짜", "상품", "수량", "매출"],
                "sheets": ["Sheet1"],
            },
            {
                "stage": "plan_final",
                "steps": [
                    {"action": "excel_live.sort_range", "params": {"key_column": "수량"}}
                ],
            },
            {
                "stage": "executed",
                "replans": 0,
                "steps": [
                    {
                        "action": "excel_live.sort_range",
                        "ok": True,
                        "verified": False,
                        "verify_detail": "sort_not_applied",
                    }
                ],
            },
        ],
        outcome={"ok": False, "failure_detail": "sort_not_applied"},
    )

    text = render(turn)

    # 관측에 '매출'이 있는데 계획은 '수량'을 골랐다 — 인자 오류를 눈으로 가를 수 있어야 한다.
    assert "매출" in text
    assert "key_column" in text and "수량" in text
    assert "quick_rule:miss → planner:local → verify:failed" in text
    assert "sort_not_applied" in text
    assert "검증 실패" in text


def test_render_hides_the_raw_llm_response_unless_asked():
    turn = _turn(
        stages=[{"stage": "llm_call", "model": "ax7b", "elapsed_ms": 900, "raw_response": "{...}"}]
    )
    assert "raw=" not in render(turn)
    assert "raw=" in render(turn, show_prompt=True)


# ── 읽기 ─────────────────────────────────────────────────────────────────


def test_read_turns_skips_corrupted_lines(tmp_path):
    log = tmp_path / "chat_log.jsonl"
    log.write_text('{"turn_id":"a"}\n깨진 줄\n\n{"turn_id":"b"}\n', encoding="utf-8")

    assert [t["turn_id"] for t in read_turns(log)] == ["a", "b"]


def test_read_turns_on_a_missing_file_is_empty(tmp_path):
    assert list(read_turns(tmp_path / "없음.jsonl")) == []
