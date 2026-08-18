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
    build_cross_sheet_aggregate_plan,
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


class TestCrossSheetAggregate:
    """"A4에 지역성과 시트 주문건수 합계를 가져와줘" — 2026-08-18 사람 말투 실측.

    이 문형이 의도 정규화로 새서 **빈 값을 쓰고 성공으로 보고**됐다(가짜 성공,
    KPI 4셀 미기록). 원본 시트를 읽어 =SUM('시트'!구간)을 만들어야 한다.
    """

    SOURCE = (
        "A1:F7",
        [
            ["지역", "주문건수", "출고건수", "정시배송률", "지연건수", "클레임"],
            ["수도권", 10452, 10158, 97.1, 145, 12],
            ["충청권", 3892, 3773, 95.2, 89, 6],
            ["호남권", 3214, 3086, 94.7, 112, 5],
            ["영남권", 6789, 6512, 95.8, 174, 5],
            ["강원제주", 2495, 2383, 92.6, 145, 0],
            ["합계", 26842, 25912, None, 665, 28],
        ],
    )

    def _reader(self, sheet):
        assert sheet == "지역성과", sheet
        return self.SOURCE

    def test_the_formula_points_at_the_source_column_without_the_total_row(self):
        steps = build_cross_sheet_aggregate_plan(
            "A4에 지역성과 시트 주문건수 합계를 가져오는 수식 넣어줘", self._reader
        )
        assert [s["params"] for s in steps] == [
            # 마지막 합계 줄(7행)은 구간에서 빠진다 — 넣으면 이중 집계다.
            {"range_ref": "A4", "formula_a1": "=SUM('지역성과'!B2:B6)"}
        ]

    def test_two_clauses_in_one_sentence_inherit_the_sheet(self):
        steps = build_cross_sheet_aggregate_plan(
            "E4에는 지역성과 시트 지연건수 합계를, F4에는 클레임 합계를 가져와줘", self._reader
        )
        assert [s["params"]["range_ref"] for s in steps] == ["E4", "F4"]
        assert steps[0]["params"]["formula_a1"] == "=SUM('지역성과'!E2:E6)"
        assert steps[1]["params"]["formula_a1"] == "=SUM('지역성과'!F2:F6)"

    def test_an_unknown_header_or_sheet_backs_off(self):
        assert build_cross_sheet_aggregate_plan("A4에 지역성과 시트 없는열 합계 가져와줘", self._reader) == []

        def _boom(_sheet):
            raise RuntimeError("시트 없음")

        assert build_cross_sheet_aggregate_plan("A4에 지역성과 시트 주문건수 합계 가져와줘", _boom) == []

    def test_a_sentence_without_a_sheet_is_not_claimed(self):
        assert build_cross_sheet_aggregate_plan("A4에 주문건수 합계 넣어줘", self._reader) == []


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


class TestTheGuiRobustnessScreenshot:
    """2026-08-18 16:28 GUI 실측 3연속 실패 — 문장 그대로 재현한다.

    셋 다 룰 레이어 결함: ① 활성 셀 쓰기 규칙이 집계 훅을 선점해 플래너 실패 후
    문장 전체가 A1 값이 됨 ② "열 별로 합계"에 규칙이 없어 pivot 오분류
    ③ "시트를 만들어줘"의 조사 하나로 퀵 미스 → 플래너가 이름 끝 글자를 자름.
    """

    REGION_ROWS = [
        ["지역", "주문건수", "출고건수", "정시배송률", "지연건수", "클레임"],
        ["수도권", 10452, 10158, 97.1, 145, 12],
        ["충청권", 3892, 3773, 95.2, 89, 6],
        ["호남권", 3214, 3086, 94.7, 112, 5],
        ["영남권", 6789, 6512, 95.8, 174, 5],
        ["강원제주", 2495, 2383, 92.6, 145, 0],
    ]

    @pytest.fixture()
    def service(self, monkeypatch):
        class _Recording(_FakeExcelService):
            def __init__(self):
                super().__init__()
                self.formulas: list[tuple[str, str]] = []
                self.writes: list[tuple[str, list]] = []

            def get_used_range_ref(self, workbook_id, sheet_name):
                return "A1:F6"

            def get_active_selection_ref(self, workbook_id, sheet_name):
                # 붙여넣기 직후에는 붙여넣은 자리가 곧 선택이다 — GUI 실측과 동일.
                return self.selection

            def set_formula(self, workbook_id, sheet_name, range_ref, formula_a1):
                self.formulas.append((str(range_ref), str(formula_a1)))
                return super().set_formula(workbook_id, sheet_name, range_ref, formula_a1)

            def write_range(self, workbook_id, sheet_name, start_cell, values_2d, **kwargs):
                self.writes.append((str(start_cell), values_2d))
                return super().write_range(workbook_id, sheet_name, start_cell, values_2d, **kwargs)

        fake = _Recording()
        fake.selection = "A1:F6"
        fake._written["A1:F6"] = self.REGION_ROWS
        monkeypatch.setattr(excel_live_router, "get_excel_live_service", lambda: fake)
        excel_live_router._pending_operation_slots.clear()
        excel_live_router._pending_create_table_slots.clear()

        async def _no_llm(_message, llm_service, context):
            raise ValueError("skip")

        monkeypatch.setattr(excel_live_router, "parse_excel_live_command", _no_llm)
        return fake

    def _turn(self, message, ctx=None):
        payload = {"message": message, "session_id": "sess-gui-robust", "approve": False}
        if ctx:
            payload["context_range"] = ctx
        body = client.post("/excel-live/command", json=payload, headers=HEADERS).json()
        approval_id = (body.get("pending_approval") or {}).get("approval_id")
        if body.get("approval_required") and approval_id:
            body = client.post(
                "/excel-live/approval",
                json={"approval_id": approval_id, "approved": True},
                headers=HEADERS,
            ).json()
        return body

    def test_the_totals_sentence_beats_the_active_cell_write(self, service):
        body = self._turn("합계를 표 아래에 한 줄로 넣어줘", ctx="A1:F6")
        assert body["ok"] is True
        assert ("B7", "=SUM(B2:B6)") in service.formulas, service.formulas
        # 문장 전체가 셀 값으로 들어가는 사고가 재발하면 여기서 잡힌다.
        for _cell, values in service.writes:
            flat = [v for row in values for v in (row if isinstance(row, list) else [row])]
            assert "합계를 표 아래에 한 줄로" not in str(flat), flat

    def test_columnwise_totals_into_the_pasted_row(self, service):
        service.selection = "A7:F7"
        body = self._turn("합계를 여기 위치에 열 별로 합계를 만들어줘", ctx="A7:F7")
        assert body["ok"] is True
        assert not (body.get("result") or {}).get("ask_follow_up"), body.get("reason")
        assert ("B7", "=SUM(B2:B6)") in service.formulas, service.formulas
        assert ("F7", "=SUM(F2:F6)") in service.formulas, service.formulas

    def test_sheet_creation_survives_the_object_particle(self, service):
        from office_claw_sidecar.routers.excel_live import _build_quick_action_plan

        plan = _build_quick_action_plan("지역성과 시트를 만들어줘", None)
        assert plan and plan[0]["action"] == "excel_live.create_sheet"
        # 조사 하나 때문에 플래너로 가면 이름 끝 글자(과)가 잘린다.
        assert plan[0]["params"]["sheet_name"] == "지역성과", plan[0]


class TestMessyHumanVocab:
    """사람 말투 배터리(2026-08-18, 3라운드 41/41)가 잡은 어휘 구멍의 회귀."""

    @pytest.mark.parametrize(
        "message",
        [
            "여기다가 합 좀 밑에다 적어줄래?",
            "이 표 아래로 각 열 합 넣어주라",
            "합계행 하나 만들어서 표 밑에 붙여줘",
            "아래쪽에 총합 좀 계산해서 넣어줘",
        ],
    )
    def test_messy_sum_phrasings_match(self, message):
        got = match_aggregate_below(message)
        assert got is not None and got[0] == "SUM", message

    def test_merge_words_still_pass_through(self):
        assert match_aggregate_below("A1:B2 병합해줘") is None
        assert match_aggregate_below("시트를 통합해줘") is None
