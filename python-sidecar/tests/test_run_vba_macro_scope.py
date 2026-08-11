"""매크로는 승인한 통합문서 안에서만 돌아야 한다 (Task 9).

`Application.Run("매크로명")`은 이름을 ActiveWorkbook·PERSONAL.XLSB 등 **열려 있는
아무 통합문서**에서 해석한다. 사용자가 "A 파일에서 실행"을 승인했는데 B 파일의
동명 매크로가 도는 일이 가능했다. 승인한 대상과 실행된 대상이 다르면 승인 절차
자체가 의미를 잃는다.

COM이 없는 환경에서도 돌도록 xlwings 계층을 페이크로 세운다.
"""

from __future__ import annotations

import pytest

from office_claw_sidecar.services.excel_live_service import (
    ExcelLiveError,
    ExcelLiveService,
    _validate_macro_name,
)


class FakeCodeModule:
    def __init__(self, source: str):
        self._source = source

    @property
    def CountOfLines(self) -> int:
        return len(self._source.splitlines())

    def Lines(self, start: int, count: int) -> str:
        return "\n".join(self._source.splitlines()[start - 1 : start - 1 + count])


class FakeComponent:
    def __init__(self, name: str, source: str):
        self.Name = name
        self.CodeModule = FakeCodeModule(source)


class FakeWorkbookApi:
    def __init__(self, components):
        self.VBProject = type("VBProject", (), {"VBComponents": components})()


class FakeWorkbook:
    def __init__(self, name: str, source: str = "Sub MonthlyClose()\nEnd Sub\n"):
        self.name = name
        self.api = FakeWorkbookApi([FakeComponent("Module1", source)])


class FakeApp:
    def __init__(self):
        self.calls: list[tuple] = []
        api = self

        class Api:
            def Run(self, macro, *args):
                api.calls.append((macro, args))

        self.api = Api()


@pytest.fixture
def service_with(monkeypatch):
    def _build(workbook: FakeWorkbook) -> tuple[ExcelLiveService, FakeApp]:
        service = ExcelLiveService()
        app = FakeApp()
        monkeypatch.setattr(service, "_find_workbook", lambda _id: workbook)
        monkeypatch.setattr(service, "_app", lambda: app)
        return service, app

    return _build


class TestTheTargetWorkbookIsPinned:
    def test_the_workbook_name_is_part_of_the_macro_string(self, service_with):
        service, app = service_with(FakeWorkbook("결산.xlsm"))
        out = service.run_vba_macro("wb-1", macro_name="MonthlyClose")
        assert app.calls, "매크로가 실행되지 않았다"
        called, _args = app.calls[0]
        assert called.startswith("'결산.xlsm'!"), called
        assert out["workbook_name"] == "결산.xlsm"

    def test_a_name_with_spaces_stays_one_token(self, service_with):
        service, app = service_with(FakeWorkbook("2026 결산 최종.xlsm"))
        service.run_vba_macro("wb-1", macro_name="MonthlyClose")
        assert app.calls[0][0] == "'2026 결산 최종.xlsm'!MonthlyClose"

    def test_arguments_are_passed_through(self, service_with):
        service, app = service_with(FakeWorkbook("결산.xlsm"))
        service.run_vba_macro("wb-1", macro_name="MonthlyClose", args=[1, "x"])
        assert app.calls[0][1] == (1, "x")


class TestMacroNameValidation:
    @pytest.mark.parametrize(
        "bad",
        [
            "'다른파일.xlsm'!Evil",  # 대상 한정을 빠져나간다
            "C:\\payload.xlsm!Evil",
            "../other.xlsm!Evil",
            "Macro'!Other",
            "",
            "   ",
            "1Macro",  # VBA 식별자는 숫자로 시작할 수 없다
        ],
    )
    def test_it_rejects(self, bad):
        with pytest.raises(ExcelLiveError):
            _validate_macro_name(bad)

    @pytest.mark.parametrize("good", ["MonthlyClose", "_private", "Module1.MonthlyClose", "M2"])
    def test_it_accepts(self, good):
        assert _validate_macro_name(good) == good

    def test_a_rejected_name_never_reaches_excel(self, service_with):
        service, app = service_with(FakeWorkbook("결산.xlsm"))
        with pytest.raises(ExcelLiveError):
            service.run_vba_macro("wb-1", macro_name="'다른파일.xlsm'!Evil")
        assert app.calls == []


class TestMacroExistenceCheck:
    def test_a_missing_macro_fails_before_running_anything(self, service_with):
        service, app = service_with(FakeWorkbook("결산.xlsm", source="Sub Other()\nEnd Sub\n"))
        with pytest.raises(ExcelLiveError) as caught:
            service.run_vba_macro("wb-1", macro_name="MonthlyClose")
        assert "없습니다" in str(caught.value)
        assert app.calls == []

    def test_it_finds_a_function_too(self, service_with):
        service, app = service_with(
            FakeWorkbook("결산.xlsm", source="Public Function MonthlyClose()\nEnd Function\n")
        )
        service.run_vba_macro("wb-1", macro_name="MonthlyClose")
        assert app.calls

    def test_a_locked_vba_project_does_not_block_execution(self, service_with):
        # VBA 프로젝트 접근이 막힌 환경에서 여기서 막으면 정상 매크로도 못 돌린다.
        # 대상 한정은 호출 문자열이 이미 보장한다.
        workbook = FakeWorkbook("결산.xlsm")

        class Blocked:
            @property
            def VBProject(self):
                raise RuntimeError("프로그래밍 방식 액세스가 신뢰되지 않습니다")

        workbook.api = Blocked()
        service, app = service_with(workbook)
        service.run_vba_macro("wb-1", macro_name="MonthlyClose")
        assert app.calls[0][0] == "'결산.xlsm'!MonthlyClose"
