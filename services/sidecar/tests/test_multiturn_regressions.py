"""멀티턴에서만 드러난 파괴적 버그 둘.

2026-08-17: 지금까지의 측정은 전부 **한 턴짜리**였다. 실제 사용은 이어지는 대화다.
실제 HTTP 경로로 멀티턴 5개를 돌리자마자 둘이 나왔고, 둘 다 사용자 데이터를 부쉈다.
"""

from __future__ import annotations

import time

import pytest

from office_claw_sidecar.routers.excel_live import (
    PendingCreateTableSlots,
    _build_quick_action_plan,
    _safe_table_start_cell,
)


def _plan(message: str, context: str | None = "A1:D1"):
    return [(s["action"], s["params"]) for s in (_build_quick_action_plan(message, context) or [])]


class TestFontColorDoesNotPaintTheBackground:
    """ "글자도 흰색으로"가 방금 칠한 배경을 덮었다.

        [1] "배경색 #1E6B4F로 칠해줘"  → fill_range   (배경 남색)
        [2] "글자도 흰색으로"          → fill_range   ← 배경이 흰색으로 덮임

    조사 "도"가 글자색 패턴에 없어 배경색 규칙으로 샜다. 앞 턴에서 배경을 칠하고
    "글자도 …"라고 하는 건 멀티턴에서 가장 자연스러운 말투다.
    """

    @pytest.mark.parametrize(
        "message",
        [
            "글자도 흰색으로",
            "글씨도 흰색으로",
            "글자만 흰색으로",
            "글씨는 흰색으로",
            "글자 흰색으로",
            "글자색 흰색으로",
        ],
    )
    def test_it_changes_the_font_not_the_fill(self, message):
        actions = [a for a, _ in _plan(message)]
        assert actions == ["excel_live.set_font"], f"배경을 덮는다: {message} → {actions}"

    def test_the_font_color_is_carried(self):
        steps = dict(_plan("글자도 흰색으로"))
        assert steps["excel_live.set_font"]["color"] == "#FFFFFF"

    @pytest.mark.parametrize(
        "message", ["배경도 흰색으로", "배경색 흰색으로", "흰색으로 칠해줘", "셀 색 흰색으로"]
    )
    def test_background_requests_still_fill(self, message):
        # 반대 방향 오탐: 배경을 말했는데 글자만 바뀌면 요청이 무시된다.
        actions = [a for a, _ in _plan(message)]
        assert actions == ["excel_live.fill_range"], f"{message} → {actions}"


class TestNewTableDoesNotOverwriteData:
    """ "표 만들어줘" → "5행 4열로" 두 턴 만에 시트가 비워졌다.

    `create_table`은 빈 값을 쓰는데 시작 칸 기본값이 A1이라, 데이터가 A1부터 있는
    시트를 그대로 덮었다. 머리글과 데이터가 통째로 사라지고 "완료"로 보고됐다.
    """

    def _slot(self, start=None, sheet=None):
        now = time.time()
        return PendingCreateTableSlots(
            session_id="s", workbook_id=None, sheet_name=None, headers=[],
            created_at_ts=now, updated_at_ts=now,
            start_cell=start, explicit_sheet_name=sheet,
        )

    WITH_DATA = {"active_sheet": "매출", "sheets": [{"name": "매출", "used_range": "A1:D4"}]}
    EMPTY = {"active_sheet": "Sheet1", "sheets": [{"name": "Sheet1", "used_range": "A1"}]}

    def test_it_drops_below_existing_data(self):
        # A1:D4가 차 있으면 한 줄 띄우고 A6부터.
        assert _safe_table_start_cell(self._slot(), self.WITH_DATA) == "A6"

    def test_an_empty_sheet_starts_at_a1(self):
        assert _safe_table_start_cell(self._slot(), self.EMPTY) == "A1"

    def test_a_named_cell_is_respected(self):
        # 사용자가 칸을 지목했으면 덮어쓰기를 원할 수도 있다. 승인 카드에서 보인다.
        assert _safe_table_start_cell(self._slot("C3"), self.WITH_DATA) == "C3"

    def test_it_uses_the_named_sheet_not_the_active_one(self):
        digest = {
            "active_sheet": "요약",
            "sheets": [
                {"name": "요약", "used_range": "A1"},
                {"name": "매출", "used_range": "A1:D9"},
            ],
        }
        assert _safe_table_start_cell(self._slot(sheet="매출"), digest) == "A11"

    def test_an_unknown_sheet_falls_back_to_a1(self):
        assert _safe_table_start_cell(self._slot(sheet="없는시트"), self.WITH_DATA) == "A1"

    def test_a_missing_digest_falls_back_to_a1(self):
        assert _safe_table_start_cell(self._slot(), {}) == "A1"
