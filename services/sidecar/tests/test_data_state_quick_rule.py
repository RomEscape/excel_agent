"""대상이 '값의 상태'로 정의된 문장을 규칙이 놓게 하는 계약.

2026-08-11 armA-current 실측: "지역이 비어 있는 행은 삭제해줘"가 `quick_rule:hit`으로
LLM에 가지도 않고 `clear_range`가 되어, 빈 행 3개가 아니라 **멀쩡한 46행**이 지워졌다.
지운 셀이 있으니 검증기는 통과시키고 응답은 성공이었다(3/3회).

규칙은 동사 하나만 보고 액션을 정하므로 "빈 칸인", "이상치인" 같은 조건을 표현할 수
없다. 표현할 수 없으면 놓아야 한다.
"""

from __future__ import annotations

import pytest

from office_claw_sidecar.routers.excel_live import _quick_plan_underfits_message

DESTRUCTIVE = "excel_live.clear_range"
PAINT = "excel_live.fill_range"


class TestDataStateConditionsAreHandedToThePlanner:
    @pytest.mark.parametrize(
        ("action", "message"),
        [
            (DESTRUCTIVE, "지역이 비어 있는 행은 삭제해줘"),
            (DESTRUCTIVE, "빈 칸이 있는 행만 지워줘"),
            (DESTRUCTIVE, "금액이 비정상적으로 큰 이상치 행은 삭제해줘"),
            (PAINT, "금액에 유난히 큰 이상치가 있으면 빨간색으로 표시해줘"),
            (PAINT, "문자열로 들어간 숫자를 노란색으로 칠해줘"),
            (PAINT, "형식이 다른 값을 표시해줘"),
            ("excel_live.write_range", "누락된 값을 0으로 채워줘"),
        ],
    )
    def test_the_rule_lets_go(self, action, message):
        assert _quick_plan_underfits_message(action, message) is True


class TestPlainCommandsStayOnTheFastPath:
    @pytest.mark.parametrize(
        ("action", "message"),
        [
            (DESTRUCTIVE, "A1:B2 지워줘"),
            (DESTRUCTIVE, "전체 지우기"),
            (PAINT, "A1:D1 노란색으로 칠해줘"),
        ],
    )
    def test_the_rule_still_handles_them(self, action, message):
        # 규칙 경로는 70ms, 플래너 경로는 3초다. 조건이 없는 문장까지 넘기면
        # 얻는 것 없이 모든 단순 명령이 40배 느려진다.
        assert _quick_plan_underfits_message(action, message) is False
