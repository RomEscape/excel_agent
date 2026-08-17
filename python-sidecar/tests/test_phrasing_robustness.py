"""표현이 달라도 같은 일을 해야 한다 — 강건성 회귀.

2026-08-17 실측(의도 10개 × 표현 4~6개, 46문장):

    45/46이 실행됐지만 4개 의도가 흔들렸다. 그중 하나는 **데이터를 부쉈다**:

        "매출 시트 D2:D5 천 단위 콤마 넣어줘"
          → write_range 로 해석돼 D5의 97000이 문자열 '천 단위 콤마'로 덮였다.

    표시 형식에는 빠른 규칙이 아예 없어 플래너가 문구를 값으로 써 버린 것이다.
    서식 요청이 데이터를 부수는 건 가장 나쁜 실패라 규칙으로 확정한다.
"""

from __future__ import annotations

import pytest

from office_claw_sidecar.routers.excel_live import (
    _CHART_TYPE_MENTION,
    _build_quick_action_plan,
    _has_border_style_context,
)


def _plan(message: str):
    return [(s["action"], s["params"]) for s in (_build_quick_action_plan(message, None) or [])]


class TestNumberFormatNeverWritesValues:
    @pytest.mark.parametrize(
        "message",
        [
            "매출 시트 D2:D5 천 단위 콤마 넣어줘",
            "매출 시트 D2:D5 세 자리마다 쉼표 찍어줘",
            "매출 시트 D2:D5 콤마 넣어줘",
            "매출 시트 D2:D5에 숫자 형식 #,##0 적용",
            "매출 시트 D2:D5 금액 표시 형식 바꿔줘 #,##0",
        ],
    )
    def test_comma_phrasings_set_a_format(self, message):
        steps = _plan(message)
        assert steps, f"규칙이 안 잡혔다 — 플래너로 새면 값이 덮인다: {message}"
        action, params = steps[0]
        assert action == "excel_live.set_number_format"
        assert params["format_code"] == "#,##0"

    def test_it_never_produces_a_write(self):
        # 이게 이 테스트의 핵심이다 — write_range가 나오면 데이터가 덮인다.
        for action, _ in _plan("매출 시트 D2:D5 천 단위 콤마 넣어줘"):
            assert action != "excel_live.write_range"

    @pytest.mark.parametrize(
        ("message", "code"),
        [
            ("D2:D5 퍼센트로 표시해줘", "0.0%"),
            ("D2:D5 백분율로 바꿔줘", "0.0%"),
            ("D2:D5 소수점 두 자리로 보여줘", "0.00"),
        ],
    )
    def test_other_formats_are_recognized(self, message, code):
        steps = _plan(message)
        assert steps and steps[0][1]["format_code"] == code

    def test_an_explicit_code_in_the_sentence_wins(self):
        steps = _plan("D2:D5 표시 형식 0.00%로 바꿔줘")
        assert steps and steps[0][1]["format_code"] == "0.00%"

    def test_a_plain_write_is_not_hijacked(self):
        # "형식"이라는 말이 없으면 서식 규칙이 끼어들면 안 된다.
        actions = [a for a, _ in _plan("매출 시트 D2에 97000 입력")]
        assert "excel_live.set_number_format" not in actions


class TestBorderVocabulary:
    @pytest.mark.parametrize("word", ["테두리", "경계선", "border", "격자", "모눈"])
    def test_border_words_are_recognized(self, word):
        # "격자 표시해줘"가 read_range로 샜다(실측).
        assert _has_border_style_context(f"A1:D5 {word} 넣어줘")

    def test_a_math_boundary_is_not_a_border(self):
        assert not _has_border_style_context("경계값 계산해줘")


class TestChartVocabulary:
    @pytest.mark.parametrize(
        "phrase", ["바 차트", "바차트", "바 그래프", "막대 차트", "도넛 차트", "선 그래프"]
    )
    def test_chart_kinds_count_as_named(self, phrase):
        # 종류를 말했는데 못 알아들으면 "차트 종류를 선택해 주세요"로 되묻는다.
        assert _CHART_TYPE_MENTION.search(f"{phrase} 만들어줘")

    def test_a_bare_chart_request_still_asks(self):
        assert not _CHART_TYPE_MENTION.search("차트 만들어줘")
