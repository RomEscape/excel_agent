from __future__ import annotations

from office_claw_sidecar.services import user_harness_service as harness


def test_extract_xlwings_ops_from_direct_result():
    payload = {
        "result": {
            "xlwings_ops": [
                {
                    "engine": "xlwings",
                    "action": "excel_live.fill_range",
                    "method": "fill_range",
                    "workbook_id": r"C:\work\sales.xlsx",
                    "sheet_name": "Sheet1",
                    "target_range": "A1:B2",
                    "params": {"target_range": "A1:B2", "fill_color": "#FFFF00"},
                    "result": {"address": "A1:B2", "changed_cells": 4},
                }
            ]
        }
    }

    ops = harness._extract_xlwings_ops(payload)
    assert len(ops) == 1
    assert ops[0]["action"] == "excel_live.fill_range"
    assert ops[0]["method"] == "fill_range"
    assert ops[0]["target_range"] == "A1:B2"


def test_extract_xlwings_ops_from_plan_steps():
    payload = {
        "result": {
            "plan": [
                {
                    "index": 1,
                    "action": "excel_live.create_table",
                    "result": {
                        "xlwings_ops": [
                            {
                                "engine": "xlwings",
                                "action": "excel_live.create_table",
                                "method": "create_table",
                                "workbook_id": r"C:\work\sales.xlsx",
                                "sheet_name": "Sheet1",
                                "target_range": "B2:F6",
                                "params": {"rows": 5, "cols": 5},
                                "result": {"address": "B2:F6", "rows": 5, "cols": 5},
                            }
                        ]
                    },
                }
            ]
        }
    }

    ops = harness._extract_xlwings_ops(payload)
    assert len(ops) == 1
    assert ops[0]["action"] == "excel_live.create_table"
    assert ops[0]["result"]["rows"] == 5


def test_record_user_harness_event_includes_xlwings_fields(tmp_path, monkeypatch):
    captured: list[tuple[str, dict]] = []

    monkeypatch.setattr(harness, "get_data_dir", lambda: tmp_path)
    monkeypatch.setattr(
        harness,
        "append_unified_event",
        lambda event_type, payload: captured.append((event_type, payload)),
    )

    harness.record_user_harness_event(
        route="/excel-live/command",
        method="POST",
        request_payload={"message": "색칠해줘", "session_id": "sess-x"},
        response_payload={
            "ok": True,
            "action": "excel_live.fill_range",
            "result": {
                "address": "A1:B2",
                "xlwings_ops": [
                    {
                        "engine": "xlwings",
                        "action": "excel_live.fill_range",
                        "method": "fill_range",
                        "workbook_id": r"C:\work\sales.xlsx",
                        "sheet_name": "Sheet1",
                        "target_range": "A1:B2",
                        "params": {"target_range": "A1:B2", "fill_color": "#FFFF00"},
                        "result": {"address": "A1:B2", "changed_cells": 4},
                    }
                ],
            },
            "reason": "빠른 규칙 기반 배경색 적용",
        },
        status_code=200,
        elapsed_ms=12,
    )

    assert captured
    event_type, payload = captured[0]
    assert event_type == "harness"
    assert payload["xlwings_op_count"] == 1
    assert payload["xlwings_ops"][0]["method"] == "fill_range"


def test_record_user_feedback_event_and_personalization_snapshot(tmp_path, monkeypatch):
    captured: list[tuple[str, dict]] = []
    monkeypatch.setattr(harness, "get_data_dir", lambda: tmp_path)
    monkeypatch.setattr(
        harness,
        "append_unified_event",
        lambda event_type, payload: captured.append((event_type, payload)),
    )

    saved = harness.record_user_feedback_event(
        user_payload={"session_id": "sess-1"},
        rating="bad",
        reason="의도는 테두리였음",
        route="/excel-live/command",
        message="여기 경계를 기본으로",
        expected_action="excel_live.apply_border",
        expected_behavior="얇은 회색 경계선",
    )
    assert saved["rating"] == "bad"
    assert saved["expected_action"] == "excel_live.apply_border"

    snapshot = harness.get_personalization_snapshot("session_sess-1")
    assert "candidate_prompt" in snapshot
    assert "excel_live.apply_border" in snapshot["candidate_prompt"]
    assert captured
    assert captured[0][0] == "harness_feedback"


def test_quality_gate_promotes_active_prompt(tmp_path, monkeypatch):
    monkeypatch.setattr(harness, "get_data_dir", lambda: tmp_path)
    monkeypatch.setattr(harness, "append_unified_event", lambda *_args, **_kwargs: None)

    harness.record_user_feedback_event(
        user_payload={"user_id": "alice"},
        rating="bad",
        reason="색칠 아님",
        route="/excel-live/command",
        message="경계를 기본으로 맞춰줘",
        expected_action="excel_live.apply_border",
    )
    report = {"route": "/excel-live/command", "replay_total": 10, "replay_success": 8}
    gate = harness.evaluate_quality_gate(replay_total=10, replay_success=8, min_cases=5, min_pass_rate=0.7)
    state = harness.update_learning_state_with_replay(
        user_key="alice",
        replay_report=report,
        quality_gate=gate,
    )
    assert gate["passed"] is True
    assert "excel_live.apply_border" in str(state.get("active_prompt", ""))

    prompt = harness.build_personalization_prompt("alice")
    assert "개인화 힌트" in prompt
