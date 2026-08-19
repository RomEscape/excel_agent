"""서식 계열 사후조건 — "요청한 효과가 파일에 남았는가".

2026-08-19 블라인드 게이트에서 **성공으로 집계된 채** 파일에는 효과가 없던 것들:
  표시 형식=General 그대로 · 배경만 칠하고 굵게는 안 됨 · 병합 안 됨 · 찾을 글자가 그대로 남음.
사후조건은 "계획대로 썼는가"가 아니라 "효과가 남았는가"를 본다 — 계획이 옳아도 엔진이 삼키면 잡는다.
"""

from __future__ import annotations

import pytest

from office_claw_sidecar.services.excel_result_verifier import verify_effect


class FakeService:
    """서식 스냅샷과 값만 주는 최소 서비스."""

    def __init__(self, snapshot=None, values=None, raise_on_snapshot=False):
        self._snapshot = snapshot or {}
        self._values = values or []
        self._raise = raise_on_snapshot

    def get_format_snapshot(self, workbook_id, sheet_name, range_ref):
        if self._raise:
            raise RuntimeError("못 읽음")
        return self._snapshot

    def read_range(self, workbook_id, sheet_name, range_ref):
        return {"values": self._values, "address": range_ref}


def check(action, params, service, result=None):
    return verify_effect(
        action=action,
        params=params,
        result=result or {},
        service=service,
        workbook_id="wb",
        sheet_name="S",
    )


class TestNumberFormat:
    def test_format_not_applied_is_caught(self):
        svc = FakeService({"number_formats": [["General", "General"]]})
        ok, detail = check("excel_live.set_number_format", {"target_range": "B2:C2", "format_code": "#,##0"}, svc)
        assert ok is False and "number_format_not_applied" in detail

    def test_a_different_code_is_caught(self):
        svc = FakeService({"number_formats": [["000", "000"]]})
        ok, _ = check("excel_live.set_number_format", {"target_range": "B2:C2", "format_code": "#,##0"}, svc)
        assert ok is False

    def test_the_requested_code_passes(self):
        svc = FakeService({"number_formats": [["#,##0", "General"]]})
        ok, _ = check("excel_live.set_number_format", {"target_range": "B2:C2", "format_code": "#,##0"}, svc)
        assert ok is True


class TestFillAndFont:
    def test_fill_missing_is_caught(self):
        svc = FakeService({"fills": [[None, None]]})
        ok, detail = check("excel_live.fill_range", {"target_range": "A1:B1", "fill_color": "#002060"}, svc)
        assert ok is False and "fill_not_applied" in detail

    def test_fill_matches_regardless_of_hash_and_alpha(self):
        svc = FakeService({"fills": [["FF002060", None]]})
        ok, _ = check(
            "excel_live.fill_range", {"target_range": "A1:B1", "fill_color": "#002060"}, svc, result={"changed_cells": 2}
        )
        assert ok is True

    def test_bold_requested_but_not_applied_is_caught(self):
        # "남색 배경에 흰 글씨 굵게"가 배경만 칠하고 굵게는 빠진 채 성공으로 보고됐다.
        svc = FakeService({"bold": [[False, False]], "font_colors": [["FFFFFFFF", None]]})
        ok, detail = check("excel_live.set_font", {"target_range": "A1:B1", "bold": True}, svc)
        assert ok is False and "font_bold_not_applied" in detail

    def test_font_color_requested_but_not_applied_is_caught(self):
        svc = FakeService({"bold": [[True, False]], "font_colors": [[None, None]]})
        ok, detail = check("excel_live.set_font", {"target_range": "A1:B1", "bold": True, "color": "#FFFFFF"}, svc)
        assert ok is False and "font_color_not_applied" in detail

    def test_both_applied_passes(self):
        svc = FakeService({"bold": [[True, False]], "font_colors": [["FFFFFFFF", None]]})
        ok, _ = check(
            "excel_live.set_font",
            {"target_range": "A1:B1", "bold": True, "color": "#FFFFFF"},
            svc,
            result={"changed_cells": 2},
        )
        assert ok is True


class TestStructure:
    def test_border_missing_is_caught(self):
        svc = FakeService({"borders": [[False, False]]})
        ok, detail = check(
            "excel_live.apply_border", {"target_range": "A1:B1", "line_style": "continuous"}, svc
        )
        assert ok is False and "border_not_applied" in detail

    def test_border_removal_is_not_verified_as_an_addition(self):
        # "테두리 지워줘"·"서식 초기화"는 line_style='none'으로 온다. 없어야 정상인데 있는지 확인하면
        # 초기화가 실패로 판정돼 되돌려진다(2026-08-19 회귀로 실측).
        svc = FakeService({"borders": [[False, False]]})
        ok, _ = check(
            "excel_live.apply_border",
            {"target_range": "A1:B1", "line_style": "none"},
            svc,
            result={"changed_cells": 2},
        )
        assert ok is True

    def test_resetting_a_number_format_to_general_is_not_second_guessed(self):
        svc = FakeService({"number_formats": [["General", "General"]]})
        ok, _ = check(
            "excel_live.set_number_format",
            {"target_range": "A1:B1", "format_code": "General"},
            svc,
            result={"changed_cells": 2},
        )
        assert ok is True

    def test_merge_missing_is_caught(self):
        svc = FakeService({"merged": ["A9:C9"]})
        ok, detail = check("excel_live.merge_cells", {"target_range": "A1:F1"}, svc)
        assert ok is False and "merge_not_applied" in detail

    def test_merge_present_passes(self):
        svc = FakeService({"merged": ["A1:F1"]})
        ok, _ = check("excel_live.merge_cells", {"target_range": "A1:F1"}, svc)
        assert ok is True

    def test_freeze_not_applied_is_caught(self):
        svc = FakeService({"freeze_panes": ""})
        ok, detail = check("excel_live.freeze_panes", {"freeze_at": "A2"}, svc)
        assert ok is False and "freeze_not_applied" in detail

    def test_freeze_release_is_verified_too(self):
        svc = FakeService({"freeze_panes": "A2"})
        ok, detail = check("excel_live.freeze_panes", {"freeze_at": "해제"}, svc)
        assert ok is False and "freeze_not_released" in detail


class TestFindReplace:
    def test_the_find_text_still_present_is_caught(self):
        svc = FakeService(values=[["수도권", 10], ["충청권", 20]])
        ok, detail = check(
            "excel_live.find_replace",
            {"target_range": "A1:B2", "find_text": "수도권", "replace_text": "서울권"},
            svc,
        )
        assert ok is False and "replace_not_applied" in detail

    def test_a_completed_replace_passes(self):
        svc = FakeService(values=[["서울권", 10], ["충청권", 20]])
        ok, _ = check(
            "excel_live.find_replace",
            {"target_range": "A1:B2", "find_text": "수도권", "replace_text": "서울권"},
            svc,
        )
        assert ok is True


class TestFailSafe:
    @pytest.mark.parametrize(
        "action, params",
        [
            ("excel_live.set_number_format", {"target_range": "A1", "format_code": "#,##0"}),
            ("excel_live.fill_range", {"target_range": "A1", "fill_color": "#FF0000"}),
            ("excel_live.merge_cells", {"target_range": "A1:B1"}),
        ],
    )
    def test_an_unreadable_snapshot_passes_rather_than_blocking(self, action, params):
        # 못 봤다는 이유로 성공한 작업을 되돌리면 더 큰 손해다(기존 검증기와 같은 원칙).
        # result는 실행기가 준 값을 흉내낸다 — 기존 검사(changed_cells)까지 통과해야 이 검사만 남는다.
        ok, _ = check(
            action,
            params,
            FakeService(raise_on_snapshot=True),
            result={"changed_cells": 2, "merged": True, "address": params.get("target_range", "A1")},
        )
        assert ok is True

    def test_clearing_a_fill_is_not_second_guessed(self):
        ok, _ = check(
            "excel_live.fill_range",
            {"target_range": "A1", "fill_color": "none"},
            FakeService({"fills": [[None]]}),
            result={"changed_cells": 1},
        )
        assert ok is True
