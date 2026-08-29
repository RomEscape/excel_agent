"""드래그한 영역을 가리키는 말을 얼마나 넓게 알아듣는가.

사용자 지적(2026-08-17): "D2:D5 이런 식으로 정확한 좌표를 찍어서 알려달라고 하지는
않을거 같은데... 이 드래그 시스템에 좀 집중해야될거 같은데"

맞는 지적이다. 사람은 좌표를 타이핑하지 않는다 — 드래그해 놓고 "이쪽에",
"블록 잡은 데"라고 말한다. 여기서 놓치면 드래그 기능이 없는 것과 같아진다.

첫 실측에서 20가지 중 12가지만 잡았다.
"""

from __future__ import annotations

import pytest

from office_claw_sidecar.services.excel_selection_context import (
    decide_selection_source,
    mentions_selection,
)

# 사람이 실제로 쓸 법한 표현들. 놓치면 드래그가 무시된다.
POINTING = [
    "여기에 표 만들어줘",
    "여기다 표 만들어줘",
    "여기다가 표 만들어줘",
    "이쪽에 표 만들어줘",
    "저기에 표 만들어줘",
    "이 부분에 표 만들어줘",
    "이 영역에 표 만들어줘",
    "이 범위에 표 만들어줘",
    "이 칸들 색칠해줘",
    "지금 선택한 곳에 표 만들어줘",
    "선택한 영역에 표 만들어줘",
    "선택 영역에 표 만들어줘",
    "선택 범위 정렬해줘",
    "드래그한 곳에 표 만들어줘",
    "드래그한 영역에 표 만들어줘",
    "끌어 둔 곳에 표 만들어줘",
    "내가 잡은 데에 표 만들어줘",
    "방금 선택한 데에 표 만들어줘",
    "칠한 데에 표 만들어줘",
    "지정한 범위에 표 만들어줘",
    "블록 잡은 데 표 만들어줘",
    "파란 부분에 표 만들어줘",
]

# 선택을 가리키지 **않는** 문장. 여기서 오탐이 나면 엉뚱한 범위를 건드린다.
NOT_POINTING = [
    "A1:D5 정렬해줘",
    "매출 시트 전체 지워줘",
    "합계 구해줘",
    "이거 저장해줘",
    "새 시트 만들어줘",
    "금액 열 굵게 해줘",
]


class TestPointingPhrases:
    @pytest.mark.parametrize("message", POINTING)
    def test_it_recognizes_the_phrase(self, message):
        assert mentions_selection(message), f"드래그를 가리키는 말인데 못 알아들었다: {message}"

    @pytest.mark.parametrize("message", POINTING)
    def test_it_routes_to_the_live_selection(self, message):
        # 프론트가 준 옛 주소가 있어도 "여기"는 지금 끌어 둔 곳이다.
        assert decide_selection_source(message=message, context_range="B2:B9") == "selection"


class TestNonPointingPhrases:
    @pytest.mark.parametrize("message", NOT_POINTING)
    def test_it_does_not_claim_a_selection(self, message):
        assert not mentions_selection(message)

    def test_an_explicit_range_still_wins_over_a_pointing_word(self):
        # "여기 말고 A1:B2에" — 사용자가 좌표를 말했으면 그게 최우선이다.
        assert decide_selection_source(message="여기 말고 A1:B2에 써줘", context_range=None) == "message"
