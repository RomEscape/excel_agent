from __future__ import annotations

from fastapi.testclient import TestClient

from office_claw_sidecar.main import app
from office_claw_sidecar.routers import harness as harness_router

HEADERS = {"Authorization": "Bearer dev-token"}
client = TestClient(app)


def test_harness_feedback_endpoint(monkeypatch):
    monkeypatch.setattr(harness_router, "resolve_user_key", lambda _payload: "session_sess-1")
    monkeypatch.setattr(
        harness_router,
        "record_user_feedback_event",
        lambda **_kwargs: {"rating": "bad", "expected_action": "excel_live.apply_border"},
    )

    resp = client.post(
        "/harness/feedback",
        headers=HEADERS,
        json={
            "session_id": "sess-1",
            "rating": "bad",
            "reason": "의도 오해",
            "message": "여기 경계 기본으로",
            "expected_action": "excel_live.apply_border",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["user_key"] == "session_sess-1"
    assert body["saved"]["expected_action"] == "excel_live.apply_border"


def test_harness_replay_endpoint(monkeypatch):
    async def _fake_parse(message, llm_service, context=None):
        return {
            "action_plan": [
                {
                    "action": "excel_live.apply_border",
                    "params": {"target_range": "B2:C3"},
                    "reason": "ok",
                }
            ],
            "reason": "replayed",
        }

    monkeypatch.setattr(harness_router, "resolve_user_key", lambda _payload: "local_user")
    monkeypatch.setattr(
        harness_router,
        "list_recent_failure_events",
        lambda user_key, route, limit: [
            {
                "message": "여기 경계 기본으로",
                "action": "excel_live.fill_range",
                "status_code": 400,
                "error": "misclassified",
                "workbook_id": r"C:\work\sales.xlsx",
                "sheet_name": "Sheet1",
            }
        ],
    )
    monkeypatch.setattr(harness_router, "build_personalization_prompt", lambda _user_key: "개인화 힌트")
    monkeypatch.setattr(harness_router, "parse_excel_live_command", _fake_parse)
    monkeypatch.setattr(harness_router, "record_replay_report", lambda _user_key, report: report)
    monkeypatch.setattr(
        harness_router,
        "update_learning_state_with_replay",
        lambda **_kwargs: {"active_prompt": "개인화 힌트"},
    )

    resp = client.post(
        "/harness/replay-failures",
        headers=HEADERS,
        json={
            "session_id": "sess-1",
            "route": "/excel-live/command",
            "limit": 5,
            "parse_timeout_seconds": 5,
            "min_gate_cases": 1,
            "min_gate_pass_rate": 0.5,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["quality_gate"]["passed"] is True
    assert body["replay_report"]["replay_success"] == 1
