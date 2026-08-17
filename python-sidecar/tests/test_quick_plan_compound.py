"""한 문장에 두 가지를 시키면 둘 다 해야 한다 + 도넛 어휘.

2026-08-17 실측(example_4 24턴 재현):

    "배경색 #1E6B4F로 칠하고 글자 흰색 굵게 해줘"
      → set_font만 실행. 배경은 그대로인데 **성공으로 보고됐다.**
      → 같은 문장을 둘로 쪼개면 되던 터라 원인이 더 안 보였다.

    "도넛 차트 만들어줘"
      → "차트 종류를 선택해 주세요. 예: 선 그래프 / 막대 그래프 / 원형 차트"
      → `_CHART_KIND_WORDS`는 도넛을 아는데 `_CHART_TYPE_MENTION`에만 빠져 있었다.
        두 목록이 어긋나면 종류를 말해도 안 말한 것이 된다.
"""

from __future__ import annotations

import pytest

from office_claw_sidecar.routers.excel_live import (
    _CHART_TYPE_MENTION,
    _background_fill_hex,
    _build_quick_action_plan,
)


def _plan(message: str):
    return [
        (step["action"], step["params"])
        for step in (_build_quick_action_plan(message, None) or [])
    ]


class TestCompoundFillAndFont:
    def test_fill_and_font_in_one_sentence_produce_both_steps(self):
        steps = _plan("에너지_상세 시트 A1:F1 배경색 #1E6B4F로 칠하고 글자 흰색 굵게 해줘")
        actions = [a for a, _ in steps]
        assert "excel_live.fill_range" in actions, "배경색이 통째로 사라졌다"
        assert "excel_live.set_font" in actions

    def test_the_background_color_is_not_the_font_color(self):
        steps = dict(_plan("A1:F1 배경색 #1E6B4F로 칠하고 글자 흰색 굵게"))
        assert steps["excel_live.fill_range"]["fill_color"] == "#1E6B4F"
        assert steps["excel_live.set_font"].get("color") == "#FFFFFF"

    def test_fill_comes_before_font(self):
        # 배경을 나중에 칠해도 글꼴은 안 지워지지만, 사람이 읽는 순서와 맞춘다.
        actions = [a for a, _ in _plan("A1:D1 배경색 #DDEBF7로 칠하고 글자 굵게")]
        assert actions.index("excel_live.fill_range") < actions.index("excel_live.set_font")

    def test_a_font_only_sentence_does_not_paint_a_background(self):
        # 가장 비싼 오탐: "글씨 흰색"이 배경 칠하기로 새면 제목이 하얗게 덮인다.
        actions = [a for a, _ in _plan("대시보드 시트 A1 글씨 흰색 크기 16 굵게")]
        assert actions == ["excel_live.set_font"]

    def test_a_fill_only_sentence_stays_a_single_step(self):
        actions = [a for a, _ in _plan("A1:F1 배경색 #1E6B4F로 칠해줘")]
        assert actions == ["excel_live.fill_range"]


class TestBackgroundColorSplitter:
    def test_it_skips_the_font_color(self):
        assert _background_fill_hex("배경색 #1e6b4f로 칠하고 글자 흰색", "#FFFFFF") == "#1E6B4F"

    def test_no_background_word_means_no_fill(self):
        assert _background_fill_hex("글자 흰색 굵게", "#FFFFFF") == ""

    def test_same_color_for_both_is_honored(self):
        assert _background_fill_hex("배경색 흰색으로 칠하고 글자 흰색", "#FFFFFF") == "#FFFFFF"

    def test_no_color_at_all(self):
        assert _background_fill_hex("배경 칠해줘", None) in ("", "#FFFF00")


class TestChartVocabulary:
    @pytest.mark.parametrize(
        "word", ["도넛", "도너츠", "donut", "doughnut", "링 차트"]
    )
    def test_doughnut_counts_as_naming_a_chart_type(self, word):
        # 여기 없으면 "도넛 차트 만들어줘"에 종류를 되묻는다.
        assert _CHART_TYPE_MENTION.search(f"{word} 차트 만들어줘")

    @pytest.mark.parametrize(
        "word", ["선 그래프", "막대", "원형", "파이", "영역", "분산", "line", "bar"]
    )
    def test_existing_types_still_match(self, word):
        assert _CHART_TYPE_MENTION.search(f"{word} 차트 만들어줘")

    def test_a_bare_chart_request_still_asks(self):
        # 종류를 안 말했으면 되묻는 게 맞다 — 결과물 성격이 달라진다.
        assert not _CHART_TYPE_MENTION.search("차트 만들어줘")
