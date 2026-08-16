"""편집 사전 점검 (F-08).

2026-08-16 조사: 사이드카에 워크북/시트의 읽기 전용·보호 상태를 실제로 조회하는
코드가 0건이었다. openpyxl(file 엔진)은 시트 보호를 무시하고 보호된 시트에 써서
저장까지 성공한다 — 사전 점검이 없으면 방어선이 없다.

가장 비싼 실패는 **과잉 차단**이다. 읽기 전용 파일에서 PDF 내보내기나 시트 보호
해제까지 반려되면 정상 작업이 죽는다.
"""

from __future__ import annotations

import pytest

from office_claw_sidecar.services.excel_edit_precheck import (
    evaluate_write_block,
    read_protection_flags,
)

WRITE = "excel_live.write_range"


def _block(flags, action=WRITE, is_edit=True):
    return evaluate_write_block(action=action, flags=flags, is_edit_action=is_edit)


class TestUnknownStateNeverBlocks:
    @pytest.mark.parametrize("flags", [None, {}])
    def test_no_flags_means_no_block(self, flags):
        # 모르는 것을 결함으로 단정하면 멀쩡한 편집이 전부 반려된다.
        assert _block(flags).ok

    def test_a_read_action_is_never_blocked(self):
        assert _block({"workbook_read_only": True}, action="excel_live.read_range",
                      is_edit=False).ok


class TestReadOnlyWorkbook:
    def test_editing_a_read_only_workbook_is_blocked(self):
        v = _block({"workbook_read_only": True})
        assert v.blocked
        assert v.code == "workbook_read_only"
        assert "읽기 전용" in v.reason

    @pytest.mark.parametrize(
        "action",
        ["excel_live.export_pdf", "excel_live.recalculate", "excel_live.save_workbook"],
    )
    def test_harmless_actions_still_run_on_a_read_only_workbook(self, action):
        # PDF 내보내기는 별도 파일만 만든다. 여기서 막으면 정상 요청이 죽는다.
        assert _block({"workbook_read_only": True}, action=action).ok


class TestMarkedFinal:
    def test_marked_final_blocks_edits(self):
        v = _block({"marked_final": True})
        assert v.code == "marked_final"
        assert "계속 편집" in v.reason

    def test_marked_final_still_allows_pdf(self):
        assert _block({"marked_final": True}, action="excel_live.export_pdf").ok


class TestSheetProtection:
    def test_a_protected_sheet_blocks_cell_writes(self):
        v = _block({"sheet_protected": True, "sheet_name": "급여"})
        assert v.code == "sheet_protected"
        assert "'급여' 시트는" in v.reason

    def test_the_message_works_without_a_sheet_name(self):
        v = _block({"sheet_protected": True})
        assert "이 시트는" in v.reason

    def test_unprotecting_is_exempt(self):
        # protect_sheet는 보호를 걸기 전에 먼저 Unprotect를 부른다.
        # 여기서 막으면 "보호 풀어줘"가 영원히 안 된다.
        assert _block({"sheet_protected": True}, action="excel_live.protect_sheet").ok

    @pytest.mark.parametrize(
        "action",
        ["excel_live.export_pdf", "excel_live.recalculate", "excel_live.save_workbook"],
    )
    def test_harmless_actions_survive_sheet_protection_too(self, action):
        # 2026-08-16 실제 파일 검증에서 잡힌 회귀: 시트 보호 분기에만 면제 목록을
        # 안 걸어 보호된 시트에서 PDF 내보내기가 막혔다. 단위 테스트는 통과했었다.
        assert _block({"sheet_protected": True, "sheet_name": "급여"}, action=action).ok


class TestStructureProtection:
    @pytest.mark.parametrize(
        "action",
        [
            "excel_live.create_sheet",
            "excel_live.delete_sheet",
            "excel_live.rename_sheet",
            "excel_live.pivot_table",
        ],
    )
    def test_structure_actions_are_blocked(self, action):
        v = _block({"structure_protected": True}, action=action)
        assert v.code == "structure_protected"

    def test_a_plain_cell_write_is_not_blocked_by_structure_protection(self):
        # 구조 보호는 시트 추가/삭제를 막지 셀 편집을 막지 않는다.
        assert _block({"structure_protected": True}, action=WRITE).ok


class TestFlagReading:
    def test_a_service_without_the_method_yields_none(self):
        # 36개 테스트 파일의 가짜 서비스는 이 메서드를 갖고 있지 않다.
        class OldFake:
            pass

        assert read_protection_flags(OldFake(), workbook_id=None, sheet_name=None) is None

    def test_a_raising_service_yields_none_not_an_exception(self):
        class Boom:
            def get_write_protection(self, _wb, _sheet):
                raise RuntimeError("COM 실패")

        assert read_protection_flags(Boom(), workbook_id="X", sheet_name="S") is None

    def test_flags_are_passed_through(self):
        class Fake:
            def get_write_protection(self, wb, sheet):
                return {"workbook_read_only": True, "sheet_name": sheet}

        flags = read_protection_flags(Fake(), workbook_id="X", sheet_name="급여")
        assert flags == {"workbook_read_only": True, "sheet_name": "급여"}

    def test_a_non_dict_return_is_ignored(self):
        class Weird:
            def get_write_protection(self, _wb, _sheet):
                return "읽기전용"

        assert read_protection_flags(Weird(), workbook_id="X", sheet_name=None) is None
