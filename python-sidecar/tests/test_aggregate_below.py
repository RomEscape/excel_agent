"""사람 말투 집계 — "붙여넣은 것들 합을 밑에 기록해줘"가 실제로 동작하는가.

2026-08-18 사용자 실측 문장 그대로: "컨트롤 c, 컨트롤 v 한 위치에 있는 모든 합을
밑에 있는 시트에 기록할 수 있게 해줘". 좌표도, SUM이라는 낱말도, 수식도 없다.
재현 각본의 "B7에 =SUM(B2:B6) 수식 넣어줘"는 신뢰성 바닥짐이지 제품의 문장이
아니다 — 제품의 문장은 이쪽이다.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from office_claw_sidecar.main import app
from office_claw_sidecar.routers import excel_live as excel_live_router
from office_claw_sidecar.services.excel_aggregate_below import (
    build_aggregate_below_plan,
    match_aggregate_below,
)
from office_claw_sidecar.services.excel_selection_context import mentions_selection

sys.path.insert(0, str(Path(__file__).parent))
from test_excel_live_router import _FakeExcelService

HEADERS = {"Authorization": "Bearer dev-token"}
client = TestClient(app)

THE_SENTENCE = "컨트롤 c, 컨트롤 v 한 위치에 있는 모든 합을 밑에 있는 시트에 기록할 수 있게 해줘"


class TestMatching:
    @pytest.mark.parametrize(
        ("message", "func"),
        [
            (THE_SENTENCE, "SUM"),
            ("붙여넣은 데이터 합계를 아래에 넣어줘", "SUM"),
            ("여기 평균을 밑에 적어줘", "AVERAGE"),
            ("이 표 각 열의 최대값을 아랫줄에 기록해줘", "MAX"),
        ],
    )
    def test_human_phrasings_match(self, message, func):
        got = match_aggregate_below(message)
        assert got is not None and got[0] == func, message

    @pytest.mark.parametrize(
        "message",
        [
            "A1:B2 병합해줘",                      # "합"이 합계가 아니다
            "시트를 통합해줘",
            "밑에 행 추가해줘",                     # 아래는 있는데 집계가 없다
            "B7에 =SUM(B2:B6) 수식 넣어줘",        # 명시 수식은 기존 경로 소유
            "합계 행을 굵게 해줘",                  # 아래 방향 언급이 없다
        ],
    )
    def test_non_aggregate_sentences_pass_through(self, message):
        assert match_aggregate_below(message) is None, message

    def test_paste_words_count_as_deictic_selection(self):
        # "컨트롤 c/v 한 위치"가 살아 있는 선택으로 풀려야 좌표 없이 대상이 잡힌다.
        assert mentions_selection("컨트롤 c, 컨트롤 v 한 위치") is True
        assert mentions_selection("붙여넣은 데이터") is True
        assert mentions_selection("복붙한 표") is True


class TestPlanBuilding:
    VALUES = [
        ["지역", "주문건수", "출고건수"],
        ["수도권", 10452, 10158],
        ["충청권", 3892, 3773],
    ]

    def test_numeric_columns_get_formulas_and_the_label_column_gets_a_name(self):
        steps = build_aggregate_below_plan("SUM", "합계", "A10:C12", self.VALUES)
        by_action = {}
        for s in steps:
            by_action.setdefault(s["action"], []).append(s["params"])
        formulas = by_action["excel_live.set_formula"]
        # 머리글(10행)을 빼고 11~12행만 집계하며, 13행(아랫줄)에 놓는다.
        assert {p["range_ref"] for p in formulas} == {"B13", "C13"}
        assert formulas[0]["formula_a1"] == "=SUM(B11:B12)"
        # 글자 열(A)에는 수식 대신 이름표.
        assert by_action["excel_live.write_range"][0] == {
            "start_cell": "A13",
            "values_2d": [["합계"]],
        }

    def test_headerless_all_numeric_range_has_no_label(self):
        steps = build_aggregate_below_plan("SUM", "합계", "B2:C3", [[1, 2], [3, 4]])
        assert [s["action"] for s in steps] == ["excel_live.set_formula"] * 2
        assert steps[0]["params"]["formula_a1"] == "=SUM(B2:B3)"

    def test_no_numbers_means_no_plan(self):
        assert build_aggregate_below_plan("SUM", "합계", "A1:B2", [["가", "나"], ["다", "라"]]) == []
        assert build_aggregate_below_plan("SUM", "합계", "A1:B2", None) == []


class TestThePipeline:
    @pytest.fixture()
    def service(self, monkeypatch):
        class _Recording(_FakeExcelService):
            def __init__(self):
                super().__init__()
                self.formulas: list[tuple[str, str]] = []
                self.labels: list[tuple[str, list]] = []

            def get_active_selection_ref(self, workbook_id, sheet_name):
                return "A10:C12"

            def set_formula(self, workbook_id, sheet_name, range_ref, formula_a1):
                self.formulas.append((str(range_ref), str(formula_a1)))
                return super().set_formula(workbook_id, sheet_name, range_ref, formula_a1)

            def write_range(self, workbook_id, sheet_name, start_cell, values_2d, **kwargs):
                self.labels.append((str(start_cell), values_2d))
                return super().write_range(workbook_id, sheet_name, start_cell, values_2d, **kwargs)

        fake = _Recording()
        fake._written["A10:C12"] = TestPlanBuilding.VALUES
        monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: fake)
        excel_live_router._pending_operation_slots.clear()
        excel_live_router._pending_create_table_slots.clear()

        async def _no_llm(_message, llm_service, context):
            raise AssertionError("사람 말투 집계는 규칙 경로여야 한다")

        monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _no_llm)
        return fake

    def test_the_users_exact_sentence_writes_totals_below_the_pasted_range(self, service):
        # 앱과 같은 경로: 승인 카드가 뜨면 approval_id로 보관된 계획을 재개한다.
        # 같은 문장을 approve=True로 재전송하면 선택 해석 없이 처음부터 다시 돌아,
        # 붙여넣은 자리가 아니라 사용 범위에 계획이 잡힌다.
        payload = {"message": THE_SENTENCE, "session_id": "sess-agg", "approve": False}
        body = client.post("/excel-live/command", json=payload, headers=HEADERS).json()
        approval_id = (body.get("pending_approval") or {}).get("approval_id")
        if body.get("approval_required") and approval_id:
            body = client.post(
                "/excel-live/approval",
                json={"approval_id": approval_id, "approved": True},
                headers=HEADERS,
            ).json()
        assert body["ok"] is True
        assert not (body.get("result") or {}).get("ask_follow_up"), body.get("reason")
        assert ("B13", "=SUM(B11:B12)") in service.formulas, service.formulas
        assert ("C13", "=SUM(C11:C12)") in service.formulas, service.formulas
        assert ("A13", [["합계"]]) in service.labels, service.labels
