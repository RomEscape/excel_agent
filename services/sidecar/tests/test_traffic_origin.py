"""트래픽 출처 판별 — 학습 데이터에 자동화 트래픽이 섞이지 않는지 지킨다."""

from __future__ import annotations

from office_claw_sidecar.services import decision_trace, traffic_origin
from office_claw_sidecar.services.traffic_origin import (
    PROBE,
    TEST,
    UNKNOWN,
    USER,
    classify,
    current_origin,
    is_user_traffic,
)


def test_declared_tag_beats_every_guess():
    """기록 시점 태그가 있으면 추정하지 않는다."""
    payload = {"origin": USER, "session_id": "sess-table-fast", "workbook_id": "C:/work/x.xlsx"}
    origin = classify(payload)
    assert origin.kind == USER
    assert origin.rule == "declared"
    assert is_user_traffic(payload)


def test_pytest_session_id_is_test_traffic():
    """`test_excel_live_router.py`가 쓰는 세션 id."""
    assert classify({"session_id": "sess-table-fast"}).kind == TEST
    assert classify({"session_id": "clarify-invalid-plan"}).kind == TEST


def test_probe_scripts_are_recognized():
    assert classify({"session_id": "battery-1bf97efb"}).kind == PROBE
    assert classify({"session_id": "smoke-multi-7a4636e6"}).kind == PROBE
    assert classify({"session_id": "dashboard-measure"}).kind == PROBE


def test_sweep_work_folder_is_not_a_human_document():
    """시나리오 스윕이 쓰는 고정 폴더. 세션 id가 비어 있어도 잡아야 한다."""
    origin = classify({"session_id": "", "workbook_id": r"C:\work\complex_scenario.xlsx"})
    assert origin.kind == PROBE
    assert origin.rule == "sweep_workbook"


def test_temp_workbook_is_test_traffic():
    payload = {"session_id": "", "workbook_id": r"C:\Users\me\AppData\Local\Temp\tmpkz59hpx9\book.xlsx"}
    assert classify(payload).kind == TEST
    assert classify({"workbook_id": "/tmp/pytest-of-me/book.xlsx"}).kind == TEST


def test_fixture_strings_are_not_excel_commands():
    """`alpha123` 같은 픽스처 토큰이 학습 명령으로 들어가면 안 된다."""
    assert classify({"message": "alpha123"}).kind == TEST
    assert classify({"message": "지금 alpha123"}).kind == TEST
    assert classify({"message": "A1에 120 입력해줘"}).kind != TEST


def test_untagged_traffic_is_never_counted_as_human():
    """자동화 흔적이 없다는 것만으로 사람이라고 볼 수 없다.

    이 기준을 관대하게 뒀을 때 `logs/all_events.jsonl` 10,827건 중 5,844건이
    사람으로 잡혔는데, 실제로는 요청 간격 중앙값 1.7초짜리 스윕이었다.
    """
    payload = {"session_id": "", "workbook_id": "", "message": "매출 높은 순으로 정렬해줘"}
    origin = classify(payload)
    assert origin.kind == UNKNOWN
    assert not is_user_traffic(payload)


def test_inside_pytest_every_turn_is_test_traffic():
    """conftest가 모든 테스트 턴에 nodeid를 붙이므로 테스트 안에서는 항상 test다.

    테스트 안에서 프로브를 돌려도 결국 테스트가 만든 트래픽이다.
    """
    assert current_origin() == TEST
    with decision_trace.source(probe="battery"):
        assert current_origin() == TEST


def test_current_origin_reads_the_ambient_tag(monkeypatch):
    monkeypatch.setattr(decision_trace, "current_source", dict)
    assert current_origin() == USER

    monkeypatch.setattr(decision_trace, "current_source", lambda: {"probe": "battery"})
    assert current_origin() == PROBE

    monkeypatch.setattr(decision_trace, "current_source", lambda: {"test": "tests/test_x.py::test_y"})
    assert current_origin() == TEST


def test_label_groups_without_exploding_on_session_ids():
    """집계 키에 세션 id가 섞이면 건수만큼 키가 늘어난다."""
    first = classify({"session_id": "sess-table-fast"})
    second = classify({"session_id": "sess-sort-1"})
    assert first.label == second.label == "test/pytest_session"
    assert first.detail != second.detail


def test_harness_event_records_its_origin(tmp_path, monkeypatch):
    """기록 시점 태깅이 실제 이벤트에 남는지."""
    from office_claw_sidecar.services import user_harness_service as harness

    captured: list[tuple[str, dict]] = []
    monkeypatch.setattr(harness, "get_data_dir", lambda: tmp_path)
    monkeypatch.setattr(harness, "append_unified_event", lambda kind, event: captured.append((kind, event)))
    monkeypatch.setattr(harness, "_update_profile", lambda *_args, **_kwargs: None)

    with decision_trace.source(test="tests/test_traffic_origin.py::test_harness_event_records_its_origin"):
        harness.record_user_harness_event(
            route="/excel-live/command",
            method="POST",
            request_payload={"message": "A1에 120 입력해줘", "session_id": "sess-1"},
            response_payload={"ok": True, "action": "excel_live.write_range"},
            status_code=200,
            elapsed_ms=12,
        )

    assert captured
    _kind, event = captured[0]
    assert event["origin"] == TEST
    assert traffic_origin.classify(event).kind == TEST
