"""대화 판단 추적 로그 테스트."""

from __future__ import annotations

import json

from office_claw_sidecar.services import decision_trace


class _Response:
    def __init__(self):
        self.ok = True
        self.action = "excel_live.write_range"
        self.reason = "완료"
        self.approval_required = False
        self.result = {"executed_steps": 2, "address": "A1:B2"}


def _read_lines(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_turn_scope_writes_one_line_per_turn(tmp_path, monkeypatch):
    log_path = tmp_path / "chat_log.jsonl"
    monkeypatch.setattr(decision_trace, "get_chat_log_path", lambda: log_path)

    with decision_trace.turn_scope(endpoint="excel-live/command", message="표 만들어줘", session_id="s1"):
        decision_trace.note("understand", operation_intent="table")
        decision_trace.set_outcome_from_response(_Response())

    rows = _read_lines(log_path)
    assert len(rows) == 1
    turn = rows[0]
    assert turn["message"] == "표 만들어줘"
    assert turn["session_id"] == "s1"
    assert turn["stages"][0]["stage"] == "understand"
    assert turn["outcome"]["action"] == "excel_live.write_range"
    assert turn["outcome"]["executed_steps"] == 2


def test_turn_scope_records_exceptions(tmp_path, monkeypatch):
    log_path = tmp_path / "chat_log.jsonl"
    monkeypatch.setattr(decision_trace, "get_chat_log_path", lambda: log_path)

    try:
        with decision_trace.turn_scope(endpoint="excel-live/command", message="망가진 명령"):
            raise ValueError("해석 실패")
    except ValueError:
        pass

    turn = _read_lines(log_path)[0]
    assert turn["outcome"]["ok"] is False
    assert turn["outcome"]["error_type"] == "ValueError"
    assert "해석 실패" in turn["outcome"]["error"]


def test_note_outside_turn_is_ignored():
    decision_trace.note("understand", foo="bar")


def test_compact_truncates_huge_values():
    compacted = decision_trace.compact({"values": ["x" * 900] * 50})
    assert "...(+38개)" in compacted["values"][-1]
    assert compacted["values"][0].endswith("자)")


def test_plan_summary_reads_objects_and_dicts():
    class _Step:
        def __init__(self):
            self.action = "excel_live.set_formula"
            self.params = {"range_ref": "C2:C10"}
            self.reason = "수식"

    summary = decision_trace.plan_summary([_Step(), {"action": "excel_live.save_workbook", "params": {}}])
    assert summary[0]["action"] == "excel_live.set_formula"
    assert summary[0]["params"]["range_ref"] == "C2:C10"
    assert summary[1]["action"] == "excel_live.save_workbook"
