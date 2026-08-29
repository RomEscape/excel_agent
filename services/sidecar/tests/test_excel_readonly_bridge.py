"""읽기 전용 통합문서를 실제로 편집 가능하게 만드는 브리지.

2026-08-16 실측 경위:
    이 PC의 Excel은 정품 인증이 안 된 무료 버전이라 여는 파일이 전부 읽기 전용이다.
    편집하면 "파일이 읽기 전용인 경우에는 이 작업을 수행할 수 없습니다"로 죽는다.

    "그럼 openpyxl로 쓰면 되지 않나?" — 안 된다. Excel이 배타적 잠금을 걸어
    `open(path, 'r+b')`조차 PermissionError다. Excel이 붙들고 있는 한 어떤 경로로도
    편집이 불가능하다.

    읽기 전용에는 저장되지 않은 변경이 있을 수 없으므로 닫아도 잃을 게 없다.
    닫고 → 파일을 직접 편집하고 → 다시 연다.

여기서는 판정과 흐름만 검증한다(Excel 없이 돈다). 실제 Excel 검증은 개발일지 참조.
"""

from __future__ import annotations

import pytest

from office_claw_sidecar.services.excel_readonly_bridge import (
    can_bridge,
    release_workbook,
    restore_workbook,
)


class TestCanBridge:
    def test_a_read_only_workbook_can_be_bridged(self):
        assert can_bridge({"workbook_read_only": True}) is True

    @pytest.mark.parametrize("flags", [None, {}, {"workbook_read_only": False}])
    def test_a_writable_workbook_is_left_alone(self, flags):
        # 편집 가능한 통합문서를 닫으면 작업 중이던 내용이 사라진다.
        assert can_bridge(flags) is False

    @pytest.mark.parametrize(
        "extra", [{"sheet_protected": True}, {"structure_protected": True}]
    )
    def test_protection_is_not_bridgeable(self, extra):
        # 보호 정보는 파일 안에 있다. 닫아도 그대로라 창만 닫게 된다.
        assert can_bridge({"workbook_read_only": True, **extra}) is False


class TestRelease:
    def test_it_returns_the_closed_path(self):
        class Svc:
            def close_workbook_without_saving(self, _wb):
                return r"C:\\books\\a.xlsx"

        out = release_workbook(Svc(), workbook_id=None)
        assert out.released is True
        assert out.path == r"C:\\books\\a.xlsx"

    def test_an_engine_that_cannot_close_is_reported(self):
        out = release_workbook(object(), workbook_id=None)
        assert out.released is False
        assert out.note

    def test_a_failure_to_close_does_not_raise(self):
        class Boom:
            def close_workbook_without_saving(self, _wb):
                raise RuntimeError("COM 실패")

        out = release_workbook(Boom(), workbook_id=None)
        assert out.released is False
        assert "닫지 못했" in out.note

    def test_an_unknown_path_is_not_a_release(self):
        # 경로를 모르면 file 엔진이 어느 파일을 편집할지 알 수 없다 —
        # 실측에서 이 경우 엉뚱한 파일을 잡아 MergedCell 오류로 죽었다.
        class NoPath:
            def close_workbook_without_saving(self, _wb):
                return ""

        assert release_workbook(NoPath(), workbook_id=None).released is False


class TestRestore:
    def test_it_reopens_the_edited_file(self):
        seen: list[str] = []

        class Svc:
            def open_workbook_in_excel(self, path):
                seen.append(path)
                return True

        assert restore_workbook(Svc(), r"C:\\books\\a.xlsx") is True
        assert seen == [r"C:\\books\\a.xlsx"]

    def test_a_failed_reopen_is_not_fatal(self):
        # 편집은 이미 파일에 저장돼 있다. 창이 안 열려도 결과는 남는다.
        class Boom:
            def open_workbook_in_excel(self, _p):
                raise RuntimeError("Excel 없음")

        assert restore_workbook(Boom(), "x.xlsx") is False

    def test_an_empty_path_is_ignored(self):
        class Svc:
            def open_workbook_in_excel(self, _p):
                raise AssertionError("빈 경로로 부르면 안 된다")

        assert restore_workbook(Svc(), "") is False
