"""매크로 커버리지의 시트 귀속 — 오탐 수정이 진짜 경고까지 죽이지 않았는가.

2026-08-17 실측: "제품_리포트 A3:A7에 …"처럼 '시트' 낱말 없이 시트명으로 시작하는
단계가 활성 시트로 오귀속돼 "기존 데이터를 덮어씁니다" 오탐 5건. 같은 날 두 번째
발견: 설정의 model이 ax7bplanner-v3라 매크로 분해가 플래너로 돌고 있었다
(분해 JSON 실패 실측). 수정 후 5케이스 139단계 경고 0.

0이 "검사기 사망"이 아님을 여기서 못박는다 — 진짜 덮어쓰기는 여전히 잡혀야 한다.
"""

from __future__ import annotations

from office_claw_sidecar.routers.excel_live import _build_quick_action_plan  # noqa: F401  (임포트 회귀 감시)
from office_claw_sidecar.services.excel_macro_planner import validate_macro_steps
from office_claw_sidecar.services.llm_service import get_macro_model_name

DIGEST = {
    "active_sheet": "Sales_Data",
    "sheets": [{"name": "Sales_Data", "used_range": "A1:H61"}],
}


def _warnings(steps):
    return [w for s in steps for w in s.warnings]


class TestRealOverwritesAreStillCaught:
    def test_writing_over_existing_data_warns(self):
        steps = validate_macro_steps(
            ["Sales_Data 시트 A1:B5에 요약 값 입력"], digest=DIGEST
        )
        assert any("덮어씁니다" in w for w in _warnings(steps)), _warnings(steps)

    def test_merging_over_existing_data_warns(self):
        steps = validate_macro_steps(["Sales_Data 시트 A1:F2 병합해줘"], digest=DIGEST)
        assert any("사라집니다" in w for w in _warnings(steps)), _warnings(steps)


class TestPlanCreatedSheetsAreNotMisattributed:
    def test_a_bare_sheet_name_prefix_is_not_the_active_sheet(self):
        # '시트' 낱말 없이 시트명으로 시작 — 활성 시트의 기존 데이터와 무관하다.
        steps = validate_macro_steps(
            [
                "제품_리포트 시트 만들어줘",
                "제품_리포트 A3:A7에 제품 분류 입력 (노트북, 모니터, 서버, 주변기기)",
                "제품_리포트 B3:B7에 수식 =SUMIF(Sales_Data!$D$2:$D$61,A3,Sales_Data!$E$2:$E$61) 적용",
            ],
            digest=DIGEST,
        )
        assert not any("덮어씁니다" in w for w in _warnings(steps)), _warnings(steps)

    def test_new_columns_beyond_used_range_stay_clean(self):
        steps = validate_macro_steps(
            ["Sales_Data 시트 J1에 매출 입력", "Sales_Data 시트 J2:J61에 수식 =E2*F2 적용"],
            digest=DIGEST,
        )
        assert not _warnings(steps), _warnings(steps)


class TestMacroModelGuard:
    """설정이 또 플래너로 틀어져도 분해 태스크로는 새지 않는다."""

    def test_a_planner_model_in_config_falls_back(self, monkeypatch):
        from office_claw_sidecar.services import llm_service

        monkeypatch.setattr(
            llm_service,
            "load_llm_config",
            lambda: {"provider": "ollama", "model": "ax7bplanner-v3:latest"},
        )
        assert not get_macro_model_name().lower().startswith("ax7bplanner")

    def test_a_general_model_in_config_is_used(self, monkeypatch):
        from office_claw_sidecar.services import llm_service

        monkeypatch.setattr(
            llm_service,
            "load_llm_config",
            lambda: {"provider": "ollama", "model": "ax4-light:latest"},
        )
        assert get_macro_model_name() == "ax4-light:latest"
