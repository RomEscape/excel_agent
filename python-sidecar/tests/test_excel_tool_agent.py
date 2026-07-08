"""Excel Tool-Calling 에이전트 루프 단위 테스트 (LLM 모킹)."""

from __future__ import annotations

import asyncio
import json

from office_claw_sidecar.services import excel_actions, excel_tool_agent
from office_claw_sidecar.services.excel_tool_agent import (
    MAX_TOOL_ROUNDS,
    resume_excel_tool_turn,
    run_excel_tool_turn,
)


class _FakeLLM:
    """chat_with_tools 호출마다 준비된 응답을 순서대로 돌려주는 가짜 LLMService."""

    def __init__(self, replies: list[dict]):
        self._replies = list(replies)
        self.calls: list[dict] = []

    async def chat_with_tools(self, messages, tools, model=None):
        self.calls.append({"messages": [dict(m) for m in messages], "tools": tools})
        return self._replies.pop(0)


class _FakeExcelService:
    def __init__(self):
        self._workbooks = [
            {
                "workbook_id": r"C:\work\sales.xlsx",
                "name": "sales.xlsx",
                "full_path": r"C:\work\sales.xlsx",
                "active_sheet": "Sheet1",
            }
        ]

    def list_workbooks(self):
        return self._workbooks

    def get_selected_workbook_id(self):
        return r"C:\work\sales.xlsx"

    def calculate_column_stat(self, workbook_id, sheet_name, column, stat):
        return {"column": "B", "header": column, "stat": stat, "value": 400.0, "numeric_count": 3, "address": "B1:B4"}

    def read_range(self, workbook_id, sheet_name, range_ref):
        return {"values": [[1, 2]], "address": range_ref, "row_count": 1, "col_count": 2}


def _patch_excel(monkeypatch) -> _FakeExcelService:
    fake = _FakeExcelService()
    # 컨텍스트 구성(excel_tool_agent)과 실행(excel_actions)이 각각 참조하는 심볼을 모두 패치
    monkeypatch.setattr(excel_tool_agent, "get_excel_live_service", lambda: fake)
    monkeypatch.setattr(excel_actions, "get_excel_live_service", lambda: fake)
    return fake


def _tool_call(name: str, arguments, call_id: str = "call-1") -> dict:
    if not isinstance(arguments, str):
        arguments = json.dumps(arguments, ensure_ascii=False)
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


def _run(coro):
    return asyncio.run(coro)


def test_plain_chat_reply_strips_think_blocks(monkeypatch):
    _patch_excel(monkeypatch)
    llm = _FakeLLM([
        {"content": "<think>추론 중...</think>안녕하세요!", "tool_calls": [], "finish_reason": "stop"},
    ])

    turn = _run(run_excel_tool_turn(message="안녕", llm_service=llm))

    assert turn["type"] == "chat"
    assert turn["assistant_text"] == "안녕하세요!"
    assert turn["executed"] == []


def test_system_prompt_contains_workbook_context_and_history(monkeypatch):
    _patch_excel(monkeypatch)
    llm = _FakeLLM([
        {"content": "네", "tool_calls": [], "finish_reason": "stop"},
    ])

    _run(
        run_excel_tool_turn(
            message="그럼 평균은?",
            llm_service=llm,
            history=[
                {"role": "user", "content": "매출 열 다 더해줘"},
                {"role": "assistant", "content": "합계는 400입니다."},
            ],
        )
    )

    messages = llm.calls[0]["messages"]
    assert messages[0]["role"] == "system"
    assert "sales.xlsx" in messages[0]["content"]
    assert messages[1] == {"role": "user", "content": "매출 열 다 더해줘"}
    assert messages[2] == {"role": "assistant", "content": "합계는 400입니다."}
    assert messages[3] == {"role": "user", "content": "그럼 평균은?"}


def test_safe_tool_executes_and_result_feeds_next_round(monkeypatch):
    _patch_excel(monkeypatch)
    llm = _FakeLLM([
        {
            "content": "",
            "tool_calls": [_tool_call("calculate_column_stat", {"column": "매출", "stat": "sum"})],
            "finish_reason": "tool_calls",
        },
        {"content": "매출 열의 합계는 400입니다.", "tool_calls": [], "finish_reason": "stop"},
    ])

    turn = _run(run_excel_tool_turn(message="매출 열 다 더해줘", llm_service=llm))

    assert turn["type"] == "chat"
    assert turn["assistant_text"] == "매출 열의 합계는 400입니다."
    assert len(turn["executed"]) == 1
    assert turn["executed"][0]["action"] == "excel_live.calculate_column_stat"
    assert turn["executed"][0]["result"]["value"] == 400.0

    # 2라운드 호출의 메시지에 tool 결과가 재주입되었는지 확인
    second_messages = llm.calls[1]["messages"]
    tool_msgs = [m for m in second_messages if m["role"] == "tool"]
    assert len(tool_msgs) == 1
    assert "400" in tool_msgs[0]["content"]
    assert tool_msgs[0]["tool_call_id"] == "call-1"


def test_confirm_tool_returns_approval_without_execution(monkeypatch):
    _patch_excel(monkeypatch)
    llm = _FakeLLM([
        {
            "content": "셀 값을 수정하겠습니다.",
            "tool_calls": [
                _tool_call(
                    "write_range",
                    {"start_cell": "C3", "values_2d": [[120]], "sheet_name": "Sheet2"},
                )
            ],
            "finish_reason": "tool_calls",
        },
    ])

    turn = _run(run_excel_tool_turn(message="C3에 120 입력해줘", llm_service=llm))

    assert turn["type"] == "approval"
    assert turn["action"] == "excel_live.write_range"
    # sheet_name은 params에서 실행 레벨로 승격된다
    assert turn["params"] == {"start_cell": "C3", "values_2d": [[120]]}
    assert turn["sheet_name"] == "Sheet2"
    assert turn["reason"] == "셀 값을 수정하겠습니다."
    assert turn["executed"] == []


def test_unknown_function_error_is_fed_back_to_llm(monkeypatch):
    _patch_excel(monkeypatch)
    llm = _FakeLLM([
        {
            "content": "",
            "tool_calls": [_tool_call("delete_all_files", {})],
            "finish_reason": "tool_calls",
        },
        {"content": "해당 작업은 지원하지 않습니다.", "tool_calls": [], "finish_reason": "stop"},
    ])

    turn = _run(run_excel_tool_turn(message="파일 다 지워줘", llm_service=llm))

    assert turn["type"] == "chat"
    assert turn["executed"] == []
    tool_msgs = [m for m in llm.calls[1]["messages"] if m["role"] == "tool"]
    assert "알 수 없는 함수" in tool_msgs[0]["content"]


def test_invalid_arguments_json_is_fed_back_to_llm(monkeypatch):
    _patch_excel(monkeypatch)
    llm = _FakeLLM([
        {
            "content": "",
            "tool_calls": [_tool_call("read_range", "{범위: A1:C3")],
            "finish_reason": "tool_calls",
        },
        {"content": "범위를 다시 알려주세요.", "tool_calls": [], "finish_reason": "stop"},
    ])

    turn = _run(run_excel_tool_turn(message="읽어줘", llm_service=llm))

    assert turn["type"] == "chat"
    tool_msgs = [m for m in llm.calls[1]["messages"] if m["role"] == "tool"]
    assert "파싱 실패" in tool_msgs[0]["content"]


def test_round_limit_stops_infinite_tool_loops(monkeypatch):
    _patch_excel(monkeypatch)
    reply = {
        "content": "",
        "tool_calls": [_tool_call("list_workbooks", {})],
        "finish_reason": "tool_calls",
    }
    llm = _FakeLLM([dict(reply) for _ in range(MAX_TOOL_ROUNDS)])

    turn = _run(run_excel_tool_turn(message="목록", llm_service=llm))

    assert turn["type"] == "chat"
    assert len(llm.calls) == MAX_TOOL_ROUNDS
    assert len(turn["executed"]) == MAX_TOOL_ROUNDS
    assert "중단" in turn["assistant_text"]


def test_excel_context_degrades_gracefully_when_excel_unavailable(monkeypatch):
    class _BrokenService:
        def list_workbooks(self):
            raise RuntimeError("Excel 미실행")

    monkeypatch.setattr(excel_tool_agent, "get_excel_live_service", lambda: _BrokenService())
    llm = _FakeLLM([
        {"content": "네, 안녕하세요.", "tool_calls": [], "finish_reason": "stop"},
    ])

    turn = _run(run_excel_tool_turn(message="안녕", llm_service=llm))

    assert turn["type"] == "chat"
    assert "확인 불가" in llm.calls[0]["messages"][0]["content"]


def test_invalid_params_fed_back_instead_of_approval(monkeypatch):
    """CONFIRM 도구라도 인자 검증 실패면 승인으로 넘어가지 않고 오류를 재주입한다."""
    _patch_excel(monkeypatch)
    llm = _FakeLLM([
        {
            "content": "",
            # write_range인데 필수 values_2d 누락
            "tool_calls": [_tool_call("write_range", {"start_cell": "C3"})],
            "finish_reason": "tool_calls",
        },
        {"content": "기록할 값을 알려주세요.", "tool_calls": [], "finish_reason": "stop"},
    ])

    turn = _run(run_excel_tool_turn(message="C3에 써줘", llm_service=llm))

    assert turn["type"] == "chat"
    assert turn["executed"] == []
    tool_msgs = [m for m in llm.calls[1]["messages"] if m["role"] == "tool"]
    assert "검증 실패" in tool_msgs[0]["content"]


# ── B안: 승인 후 재개 (multi-CONFIRM truncation + resume) ─────────────────────


def _augment_fake_for_resume(fake):
    """resume 실행에 필요한 CONFIRM 액션 메서드를 가짜 서비스에 추가한다."""
    fake.highlight_by_condition = lambda workbook_id, sheet_name, target_range, operator, threshold, fill_color: {
        "matched_cells": 3, "changed_cells": 3, "address": target_range,
    }
    fake.write_range = lambda workbook_id, sheet_name, start_cell, values_2d: {
        "written_cells": 1, "address": start_cell,
    }
    fake.get_active_selection_ref = lambda workbook_id, sheet_name: "A1"
    return fake


def test_multiple_tool_calls_with_confirm_truncates_and_returns_resume(monkeypatch):
    fake = _patch_excel(monkeypatch)
    _augment_fake_for_resume(fake)
    llm = _FakeLLM([
        {
            "content": "합계를 구한 뒤 강조하겠습니다.",
            "tool_calls": [
                _tool_call("calculate_column_stat", {"column": "매출", "stat": "sum"}, "safe-1"),
                _tool_call(
                    "highlight_by_condition",
                    {"target_range": "A:A", "operator": ">=", "threshold": 50},
                    "confirm-1",
                ),
            ],
            "finish_reason": "tool_calls",
        },
    ])

    turn = _run(run_excel_tool_turn(message="매출 합계 내고 50 이상 강조해줘", llm_service=llm))

    assert turn["type"] == "approval"
    assert turn["action"] == "excel_live.highlight_by_condition"
    # SAFE 도구는 승인 전에 이미 실행됨
    assert len(turn["executed"]) == 1
    assert turn["executed"][0]["action"] == "excel_live.calculate_column_stat"
    # 재개 상태: assistant 메시지의 tool_calls는 CONFIRM까지로 잘리고, SAFE 결과가 재주입됨
    resume = turn["resume"]
    assert resume["tool_call_id"] == "confirm-1"
    assistant_msg = next(m for m in reversed(resume["messages"]) if m["role"] == "assistant")
    assert len(assistant_msg["tool_calls"]) == 2
    tool_msgs = [m for m in resume["messages"] if m["role"] == "tool"]
    assert len(tool_msgs) == 1 and tool_msgs[0]["tool_call_id"] == "safe-1"


def test_resume_after_approval_executes_and_answers(monkeypatch):
    fake = _patch_excel(monkeypatch)
    _augment_fake_for_resume(fake)
    llm = _FakeLLM([
        {"content": "50 이상 셀을 강조했습니다.", "tool_calls": [], "finish_reason": "stop"},
    ])
    resume = {
        "messages": [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "50 이상 강조해줘"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [_tool_call("highlight_by_condition", {}, "c1")],
            },
        ],
        "tool_call_id": "c1",
    }

    turn = _run(
        resume_excel_tool_turn(
            resume=resume,
            action="excel_live.highlight_by_condition",
            params={"target_range": "A:A", "operator": ">=", "threshold": 50},
            workbook_id=r"C:\work\sales.xlsx",
            sheet_name="Sheet1",
            llm_service=llm,
        )
    )

    assert turn["type"] == "chat"
    assert turn["assistant_text"] == "50 이상 셀을 강조했습니다."
    assert len(turn["executed"]) == 1
    assert turn["executed"][0]["result"]["changed_cells"] == 3
    # 승인 실행 결과가 tool 메시지로 재주입되어 LLM이 그걸 보고 답변
    tool_msgs = [m for m in llm.calls[0]["messages"] if m["role"] == "tool"]
    assert tool_msgs[-1]["tool_call_id"] == "c1"
    assert "changed_cells" in tool_msgs[-1]["content"]


def test_resume_hits_second_confirm_returns_new_approval(monkeypatch):
    fake = _patch_excel(monkeypatch)
    _augment_fake_for_resume(fake)
    # 승인 실행 후 LLM이 또 다른 CONFIRM(정렬)을 요청
    llm = _FakeLLM([
        {
            "content": "이제 정렬하겠습니다.",
            "tool_calls": [_tool_call("sort_rows", {"column": "매출", "order": "desc"}, "confirm-2")],
            "finish_reason": "tool_calls",
        },
    ])
    resume = {
        "messages": [
            {"role": "user", "content": "강조하고 정렬해줘"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [_tool_call("highlight_by_condition", {}, "c1")],
            },
        ],
        "tool_call_id": "c1",
    }

    turn = _run(
        resume_excel_tool_turn(
            resume=resume,
            action="excel_live.highlight_by_condition",
            params={"target_range": "A:A", "operator": ">=", "threshold": 50},
            workbook_id=r"C:\work\sales.xlsx",
            sheet_name="Sheet1",
            llm_service=llm,
        )
    )

    assert turn["type"] == "approval"
    assert turn["action"] == "excel_live.sort_rows"
    # 첫 승인 작업(강조)은 실행되어 executed에 남는다
    assert len(turn["executed"]) == 1
    assert turn["executed"][0]["action"] == "excel_live.highlight_by_condition"
    assert "resume" in turn
