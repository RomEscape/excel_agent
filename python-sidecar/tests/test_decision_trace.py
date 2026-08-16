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


class TestUserInputIsAlwaysRecorded:
    """`message`는 그 턴이 처리한 문장이지 사람이 친 문장이 아니다.

    매크로 하위 단계는 분해기가 만든 문장이 들어가고 승인 턴은 원래 명령을 재사용한다.
    2026-08-16 실측에서 "매출 대시보드 만들어줘" 한 마디가 19개 턴을 만들었는데,
    로그 어디에도 그 한 마디가 남지 않아 무엇에서 비롯됐는지 되짚을 수 없었다.
    """

    def test_a_plain_turn_uses_its_own_message(self, tmp_path, monkeypatch):
        log_path = tmp_path / "chat_log.jsonl"
        monkeypatch.setattr(decision_trace, "get_chat_log_path", lambda: log_path)

        with decision_trace.turn_scope(endpoint="excel-live/command", message="A1에 3 입력"):
            pass

        turn = _read_lines(log_path)[0]
        assert turn["user_input"] == "A1에 3 입력"
        assert turn["origin"]["kind"] == "user"

    def test_a_macro_substep_keeps_the_original_request(self, tmp_path, monkeypatch):
        log_path = tmp_path / "chat_log.jsonl"
        monkeypatch.setattr(decision_trace, "get_chat_log_path", lambda: log_path)

        with decision_trace.origin(
            user_input="매출 대시보드 만들어줘",
            kind="macro_step",
            macro_id="abc123",
            step_index=7,
            total_steps=19,
        ), decision_trace.turn_scope(
            endpoint="excel-live/command",
            message="Dashboard 시트 A2에 수식 =SUM(Sales_Data!J2:J61) 적용",
        ):
            pass

        turn = _read_lines(log_path)[0]
        assert turn["user_input"] == "매출 대시보드 만들어줘"
        assert turn["message"].startswith("Dashboard 시트 A2")
        assert turn["origin"]["kind"] == "macro_step"
        assert turn["origin"]["macro_id"] == "abc123"
        assert turn["origin"]["step_index"] == 7

    def test_an_approval_turn_records_the_decision(self, tmp_path, monkeypatch):
        log_path = tmp_path / "chat_log.jsonl"
        monkeypatch.setattr(decision_trace, "get_chat_log_path", lambda: log_path)

        with decision_trace.origin(
            user_input="머리글 굵게", kind="approval", approved=True
        ), decision_trace.turn_scope(endpoint="excel-live/approval", message="머리글 굵게"):
            pass

        turn = _read_lines(log_path)[0]
        assert turn["origin"]["kind"] == "approval"
        assert turn["origin"]["approved"] is True

    def test_the_origin_block_does_not_leak_out(self, tmp_path, monkeypatch):
        log_path = tmp_path / "chat_log.jsonl"
        monkeypatch.setattr(decision_trace, "get_chat_log_path", lambda: log_path)

        with decision_trace.origin(user_input="바깥", kind="macro_step"):
            pass
        with decision_trace.turn_scope(endpoint="excel-live/command", message="안쪽"):
            pass

        turn = _read_lines(log_path)[0]
        assert turn["user_input"] == "안쪽", "블록을 빠져나온 뒤에도 원문이 남았다"
        assert turn["origin"]["kind"] == "user"
