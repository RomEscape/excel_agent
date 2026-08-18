"""규칙이 만든 여러 단계가 파이프라인을 통과해 **전부** 실행되는가.

2026-08-17 실측(GUI 스크린샷 + 로그). 사용자가 표를 붙여넣고 "이 부분은 원래대로
초기화해줄 수 있어? 표 없애줘"라고 했다:

    understand : [apply_border(none), fill_range(#FFFFFF), clear_range]   3단계
    plan_final : [clear_range]                                            1단계
    why        : "원문 근거가 없는 단계를 덜어낸 계획"

규칙이 일부러 낸 3단계를, 플래너 헛발질을 막으려는 근거 필터가 도로 잘랐다 —
문장에 '테두리'라는 낱말이 없다는 이유로. 내용만 비워지고 테두리는 남았는데
"완료"로 보고됐다.

교훈: 단위 테스트(`_build_quick_action_plan`)는 통과하고 있었다. 한 단계가 만든
계획을 다른 단계가 조용히 되돌리는 부류는 **파이프라인 전체를 지나야** 잡힌다.
그래서 이 파일은 라우터 HTTP 경계에서 실행된 액션 목록을 확인한다.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from office_claw_sidecar.main import app
from office_claw_sidecar.routers import excel_live as excel_live_router

sys.path.insert(0, str(Path(__file__).parent))
from test_excel_live_router import _FakeExcelService

HEADERS = {"Authorization": "Bearer dev-token"}
client = TestClient(app)


class _RecordingService(_FakeExcelService):
    """실행된 (액션, 범위)를 순서대로 기록한다."""

    def __init__(self):
        super().__init__()
        self.calls: list[tuple[str, str]] = []

    def fill_range(self, workbook_id, sheet_name, target_range, fill_color):
        self.calls.append(("fill_range", str(target_range)))
        return super().fill_range(workbook_id, sheet_name, target_range, fill_color)

    def clear_range(self, workbook_id, sheet_name, target_range):
        self.calls.append(("clear_range", str(target_range)))
        return super().clear_range(workbook_id, sheet_name, target_range)

    def apply_border(self, workbook_id, sheet_name, target_range, line_style, weight, color):
        self.calls.append(("apply_border", str(target_range)))
        return super().apply_border(workbook_id, sheet_name, target_range, line_style, weight, color)

    def write_range(self, workbook_id, sheet_name, start_cell, values_2d, **kwargs):
        self.calls.append(("write_range", str(start_cell)))
        self.written = values_2d
        return super().write_range(workbook_id, sheet_name, start_cell, values_2d, **kwargs)


@pytest.fixture()
def service(monkeypatch):
    fake = _RecordingService()
    monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: fake)
    excel_live_router._pending_operation_slots.clear()
    excel_live_router._pending_create_table_slots.clear()

    async def _no_llm(_message, llm_service, context):
        raise AssertionError("규칙 경로여야 한다 — LLM이 호출되면 그 자체가 회귀다")

    monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _no_llm)
    return fake


def _run(message: str, context_range: str | None = None) -> dict:
    payload = {
        "message": message,
        "session_id": "sess-rule-pipeline",
        "approve": False,
    }
    if context_range:
        payload["context_range"] = context_range
    first = client.post("/excel-live/command", json=payload, headers=HEADERS)
    assert first.status_code == 200
    body = first.json()
    if body.get("approval_required"):
        second = client.post(
            "/excel-live/command", json={**payload, "approve": True}, headers=HEADERS
        )
        assert second.status_code == 200
        body = second.json()
    return body


class TestClearTableRunsAllThreeSteps:
    """스크린샷의 바로 그 턴들. 내용·배경·테두리가 모두 지워져야 한다."""

    MESSAGE = "이 부분은 원래대로 초기화해줄 수 있어? 표 없애줘"

    def test_the_full_plan_reaches_the_executor(self, service):
        body = _run(self.MESSAGE, context_range="A1:D9")
        assert body["ok"] is True
        actions = [a for a, _ in service.calls]
        assert actions == ["apply_border", "fill_range", "clear_range"], (
            f"3단계 중 일부가 잘렸다: {actions} — "
            "근거 필터가 규칙 계획을 다시 자르고 있다"
        )

    def test_a_reset_phrasing_also_strips_formatting(self, service):
        # 2026-08-17 두 번째 GUI 실측. "초기화"가 값 비우기로만 분류돼, 서식만
        # 있고 값이 없는 범위에서 아무것도 안 바뀐 채 "완료"가 나갔다.
        body = _run("A1:D9 여기 부분 초기화시켜줄 수 있어?")
        assert body["ok"] is True
        actions = [a for a, _ in service.calls]
        assert actions == ["apply_border", "fill_range", "clear_range"], (
            f"초기화가 서식을 안 걷어낸다: {actions}"
        )

    def test_every_step_targets_the_pasted_range(self, service):
        _run(self.MESSAGE, context_range="A1:D9")
        assert {rng for _, rng in service.calls} == {"A1:D9"}

    def test_the_planner_evidence_filter_still_guards_llm_plans(self, service, monkeypatch):
        """이 수정이 반대 방향 사고를 되살리면 안 된다.

        근거 필터의 존재 이유: 플래너가 "정렬해줘"에 create_table을 끼워 넣는 부류.
        규칙 계획(plan_source=rule)만 면제하고 LLM 계획은 계속 걸러야 한다.
        """

        async def _llm_plan(_message, llm_service, context):
            return {
                "action_plan": [
                    # 정렬 요청에 근거 없는 표 생성이 끼어든 상황을 흉내 낸다.
                    {"action": "excel_live.create_table", "params": {"rows": 3, "cols": 3}},
                    {"action": "excel_live.sort_range", "params": {"key_column": "B", "order": "desc"}},
                ],
                "action": "excel_live.create_table",
                "params": {},
                "reason": "플래너 헛발질 흉내",
                "intent": "edit",
            }

        monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _llm_plan)
        body = _run("매출 기준으로 내림차순 정렬해줘")
        assert body["ok"] is True
        # create_table이 실행되지 않았어야 한다.
        assert "create_table" not in str(body.get("action", "")), body.get("action")


class TestKeywordBearingValuesStillWrite:
    """값 나열에 작업 낱말이 섞여도 완결된 쓰기는 되묻지 않는다.

    2026-08-18 ex2 재현 실측: "A3:D3에 …,비교 기준 전월 대비,… 입력"이
    compare 슬롯 질문으로, "A5:F5에 총 매출,…,식자재 원가율 입력"이 formula
    슬롯 질문으로 샜다. 힌트 추출이 값 안의 낱말을 의도로 오인해, 범위와
    값을 다 말한 쓰기 앞에서 새 멀티턴을 열었다.
    """

    @pytest.mark.parametrize(
        "message",
        [
            "A3:D3에 조회 기간 2026-06-01~2026-06-30,브랜드 푸드AI 키친,비교 기준 전월 대비,업데이트 2026-07-01 09:30 입력",
            "A5:F5에 총 매출,총 순이익,총 주문 건수,신규 고객 수,고객 만족도,식자재 원가율 입력",
        ],
    )
    def test_the_row_is_written_without_a_question(self, service, message):
        body = _run(message)
        assert body["ok"] is True
        assert not (body.get("result") or {}).get("ask_follow_up"), (
            f"완결된 쓰기에 되묻는다: {body.get('reason')}"
        )
        assert [a for a, _ in service.calls] == ["write_range"], service.calls

    def test_an_intent_misclassification_cannot_erase_the_row(self, service, monkeypatch):
        """2026-08-18 ex5 재현 실측: "A7:F7에 순위,SKU,… 입력"의 '순위'를
        정규화가 pivot_table로 오분류했고, "쓰기 아닌 분류는 의도" 면제가
        그대로 통과시켜 라벨 행이 사라졌다. 완결된 쓰기는 모델 분류를 이긴다."""

        async def _intent_pivot(_message, llm_service, context):
            return {
                "action_plan": [
                    {"action": "excel_live.pivot_table", "params": {"group_by": "순위"}}
                ],
                "action": "excel_live.pivot_table",
                "params": {},
                "reason": "정규화 오분류 흉내",
                "intent": "edit",
                "plan_source": "intent",
            }

        monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _intent_pivot)
        body = _run("A7:F7에 순위,SKU,상품명,이슈유형,재고상태,영향예측 입력")
        assert body["ok"] is True
        assert [a for a, _ in service.calls] == ["write_range"], service.calls
        assert service.written == [["순위", "SKU", "상품명", "이슈유형", "재고상태", "영향예측"]]

    def test_a_bare_comparison_request_still_opens_the_slot(self, service):
        # 반대 방향: 쓸 값이 없는 진짜 비교 요청은 여전히 질문해야 한다.
        body = _run("전월이랑 비교해줘")
        result = body.get("result") or {}
        assert result.get("ask_follow_up") or body.get("action") != "excel_live.write_range"
        assert "write_range" not in [a for a, _ in service.calls]


class TestBorderOnlyStaysNarrow:
    def test_a_border_removal_does_not_clear_contents(self, service):
        # "테두리 없애줘"는 내용을 비우면 안 된다 — 면제가 과하게 넓어지는 걸 막는다.
        _run("A1:D9 테두리 없애줘")
        actions = [a for a, _ in service.calls]
        assert "clear_range" not in actions, f"테두리 요청이 내용까지 지운다: {actions}"


class TestPasteThenWriteFlow:
    """2026-08-18 GUI 실측: 붙여넣기+"입력해줘"가 '건수' 오인 슬롯에 낚였고,
    그 슬롯이 명시적 좌표 문장까지 붙들어 같은 질문을 3번 반복 — 대화가 막혔다.
    """

    PASTE_MSG = (
        "지역,주문건수,출고건수,정시배송률,지연건수,클레임; "
        "수도권,10452,10158,97.1,145,12; 충청권,3892,3773,95.2,89,6 입력해줘"
    )

    def test_pasted_values_with_context_are_written(self, service):
        # 붙여넣기 마커를 벗긴 뒤의 실제 백엔드 입력: 값 나열 + 동사 + context_range.
        body = _run(self.PASTE_MSG, context_range="A1:F6")
        assert body["ok"] is True
        assert not (body.get("result") or {}).get("ask_follow_up"), body.get("reason")
        assert ("write_range", "A1") in service.calls, service.calls
        assert len(service.written) == 3 and service.written[0][0] == "지역"

    def test_a_stale_question_slot_releases_for_a_complete_command(self, service):
        import time as _time

        from office_claw_sidecar.routers.excel_live import PendingExcelOperationSlots

        # 값 낱말 오인으로 열린 가짜 countif 슬롯을 그대로 재현한다.
        excel_live_router._pending_operation_slots["sess-rule-pipeline"] = (
            PendingExcelOperationSlots(
                session_id="sess-rule-pipeline",
                intent="formula",
                workbook_id=None,
                sheet_name=None,
                params={"formula_mode": "countif"},
                created_at_ts=_time.time(),
                updated_at_ts=_time.time(),
            )
        )
        body = _run("A1:F2에 지역,주문건수; 수도권,10452 입력")
        assert not (body.get("result") or {}).get("ask_follow_up"), (
            f"슬롯이 완결 명령을 붙들었다: {body.get('reason')}"
        )
        assert ("write_range", "A1") in service.calls, service.calls
        # 슬롯도 사라져 다음 턴이 자유로워야 한다.
        assert "sess-rule-pipeline" not in excel_live_router._pending_operation_slots


class TestTheThreePasteScreenshots:
    """2026-08-18 GUI 스크린샷의 실제 문장 셋 — 그대로 재현한다."""

    def test_paste_with_here_prefix_writes_all_rows_not_one_cell(self, service):
        # "여기에 …" 접두가 붙자 단일 쓰기 규칙이 문장 전체를 F9 한 칸에 넣었다.
        body = _run(
            "여기에 지역,주문건수,출고건수,정시배송률,지연건수,클레임; "
            "수도권,10452,10158,97.1,145,12; 충청권,3892,3773,95.2,89,6 입력해줘",
            context_range="A1:F9",
        )
        assert body["ok"] is True
        assert not (body.get("result") or {}).get("ask_follow_up"), body.get("reason")
        assert ("write_range", "A1") in service.calls, service.calls
        assert len(service.written) == 3, service.written
        assert service.written[0][0] == "지역", service.written[0]

    def test_delete_charts_and_reset_in_one_sentence(self, service):
        # "여기 안에 차트 같은거 다 지워주고 셀 초기화 전체 해줘"가
        # 차트 **생성** 슬롯("차트 종류를 선택해 주세요")으로 샜다.
        body = _run(
            "여기 안에 차트 같은거 다 지워주고 셀 초기화 전체 해줘",
            context_range="A1:T21",
        )
        assert body["ok"] is True
        assert not (body.get("result") or {}).get("ask_follow_up"), body.get("reason")
        actions = [a for a, _ in service.calls]
        assert "clear_range" in actions, actions
        # delete_charts는 범위 기록이 없는 액션이라 calls 훅 밖 — 실행 리포트의
        # 1단계(차트 삭제)로 확인한다.
        report = str((body.get("result") or {}).get("execution_report", ""))
        assert "차트 삭제" in report, report

    def test_charts_only_deletion_keeps_the_values(self, service):
        # "차트 다 지워줘"는 값을 건드리면 안 된다.
        body = _run("차트 다 지워줘")
        assert body["ok"] is True
        actions = [a for a, _ in service.calls]
        assert "clear_range" not in actions, f"차트만 지우랬는데 값을 비운다: {actions}"
