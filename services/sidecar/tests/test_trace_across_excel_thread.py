"""Excel 전담 스레드에서 남긴 기록도 턴 로그에 들어가야 한다.

추적 로그는 `ContextVar`로 "지금 어느 턴인가"를 들고 다닌다. 그런데 Excel 호출을
전담 스레드로 옮기면서, 그 스레드에는 컨텍스트가 따라가지 않게 됐다. 실행부 안에서
부르는 `trace_route("execute:error")` · `trace_route("verify:failed")`가 조용히
버려진다는 뜻이다.

하필 진단에 가장 필요한 두 줄이다. 이게 빠지면 로그만 보고는 "검증이 통과했다"와
"검증기가 아예 안 돌았다"를 구분할 수 없다. 실패 원인을 분류하는 `trace_report`도
그 두 경로를 보고 판정하므로, 없으면 전부 다른 결론으로 샌다.
"""

from __future__ import annotations

import asyncio
import json
import threading

from office_claw_sidecar.routers import excel_live as router
from office_claw_sidecar.services import decision_trace


def _turns(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _route_names(turn):
    return [r.get("at") for r in turn.get("routes", [])]


def test_notes_from_the_excel_thread_reach_the_turn_log(tmp_path, monkeypatch):
    """전담 스레드 안에서 부른 `route`/`note`가 턴에 남는다."""
    log_path = tmp_path / "chat_log.jsonl"
    monkeypatch.setattr(decision_trace, "get_chat_log_path", lambda: log_path)

    seen_thread: dict[str, str] = {}

    def _work():
        seen_thread["name"] = threading.current_thread().name
        decision_trace.route("verify:failed", why="값 불일치")
        decision_trace.note("executed", steps=[{"action": "excel_live.write_range", "ok": True}])
        return {"ok": True}

    async def _drive():
        with decision_trace.turn_scope(endpoint="excel-live/command", message="A1에 1 입력해줘"):
            await router._run_in_excel_queue_async("command-plan", _work)

    asyncio.run(_drive())

    assert seen_thread["name"].startswith("excel-com"), (
        f"전담 스레드에서 돌지 않았다({seen_thread['name']}) — 이 테스트가 재려는 상황이 아니다"
    )

    turn = _turns(log_path)[0]
    assert "verify:failed" in _route_names(turn), (
        "전담 스레드에서 남긴 경로가 사라졌다. 컨텍스트가 스레드로 넘어가지 않는다."
    )
    assert any(s.get("stage") == "executed" for s in turn.get("stages", [])), (
        "전담 스레드에서 남긴 단계 기록이 사라졌다."
    )


def test_the_synchronous_queue_keeps_the_turn_too(tmp_path, monkeypatch):
    """동기판(sync 라우트 핸들러용)도 같은 스레드로 넘기므로 같이 확인한다."""
    log_path = tmp_path / "chat_log.jsonl"
    monkeypatch.setattr(decision_trace, "get_chat_log_path", lambda: log_path)

    def _work():
        decision_trace.route("execute:error", why="COM 오류")
        return {"ok": False}

    with decision_trace.turn_scope(endpoint="excel-live/action", message="저장해줘"):
        router._run_in_excel_queue("action", _work)

    turn = _turns(log_path)[0]
    assert "execute:error" in _route_names(turn)


def test_the_turn_does_not_leak_into_unrelated_work(tmp_path, monkeypatch):
    """턴 밖에서 전담 스레드를 쓰면 아무 턴에도 붙지 않는다.

    컨텍스트를 복사해 넘기다 보면 이전 턴이 스레드에 남아 다음 작업에 묻을 수 있다.
    그러면 남의 턴에 기록이 섞여 진단이 더 어려워진다.
    """
    log_path = tmp_path / "chat_log.jsonl"
    monkeypatch.setattr(decision_trace, "get_chat_log_path", lambda: log_path)

    with decision_trace.turn_scope(endpoint="excel-live/command", message="첫 턴"):
        router._run_in_excel_queue("first", lambda: decision_trace.route("execute:ok"))

    # 턴 밖 — 여기서 남긴 것은 어디에도 붙으면 안 된다.
    router._run_in_excel_queue("orphan", lambda: decision_trace.route("execute:orphan"))

    with decision_trace.turn_scope(endpoint="excel-live/command", message="둘째 턴"):
        router._run_in_excel_queue("second", lambda: decision_trace.route("execute:second"))

    first, second = _turns(log_path)
    assert _route_names(first) == ["execute:ok"]
    assert _route_names(second) == ["execute:second"], (
        f"이전 턴의 기록이 묻어 들어왔다: {_route_names(second)}"
    )
