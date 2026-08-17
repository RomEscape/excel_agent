"""시트 이름의 끝 글자를 조사로 오인해 잘라내면 안 된다.

2026-08-17 실측(example_4 전체 레이아웃 92턴): 시트 이름이 "추이"였는데
`_strip_josa`가 끝 글자 "이"를 주격 조사로 보고 떼어 "추"를 만들었다.

    '추' 시트를 찾을 수 없습니다. 어느 시트에 작업할까요?
    현재 시트: Sheet, 에너지_상세, 추이     ← 있다고 스스로 적어 놓고 못 찾는다

데이터 입력 8턴 + 라인 차트 2턴, **실패 10건 전부가 이것 하나**였다.
같은 이름의 시트 생성("추이 시트 만들어줘")은 성공해서 더 헷갈렸다.

조사인지 이름의 일부인지는 문장만 봐서 알 수 없다. 그러니 정하지 말고 두 형태를
다 내놓고, 실제 시트 목록과 대조하는 쪽이 고른다. 원문형이 우선이다.
"""

from __future__ import annotations

import pytest

from office_claw_sidecar.services.excel_param_binder import (
    explicit_sheet_mention_variants,
    explicit_sheet_mentions,
    resolve_sheet_from_message,
)

DIGEST = {
    "active_sheet": "Sheet",
    "sheets": [
        {"name": "Sheet"},
        {"name": "추이"},
        {"name": "구성비"},
        {"name": "매출"},
        {"name": "단가"},
        {"name": "수量"},
    ],
}


def _resolve(message: str) -> str | None:
    return resolve_sheet_from_message(message, DIGEST, default="Sheet")


class TestNamesEndingInAParticle:
    @pytest.mark.parametrize(
        "sheet",
        ["추이", "단가"],  # 끝 글자가 각각 '이', '가' — 둘 다 주격 조사와 같다
    )
    def test_the_full_name_wins_when_it_is_a_real_sheet(self, sheet):
        assert _resolve(f"{sheet} 시트 A1:C1에 날짜 입력") == sheet

    def test_variants_are_grouped_per_mention(self):
        """후보는 **묶음**으로 준다 — 평평하게 펴면 가드가 오작동한다.

        평평한 목록으로 준 첫 수정에서, "언급했는데 없는 시트"를 찾는 가드가
        변형형 "추"를 없는 시트로 오인해 다시 "'추' 시트를 찾을 수 없습니다"라고
        되물었다. 원래 이름 "추이"는 멀쩡히 있는데도 그랬다 — 내가 낸 회귀였다.
        한 지목의 후보 중 하나라도 실재하면 그 지목은 해결된 것이다.
        """
        assert explicit_sheet_mention_variants("추이 시트 A1:C1에 날짜 입력") == [["추이", "추"]]

    def test_the_flat_list_keeps_only_the_verbatim_form(self):
        assert explicit_sheet_mentions("추이 시트 A1:C1에 날짜 입력") == ["추이"]

    def test_creating_and_writing_agree_on_the_name(self):
        # 생성은 되는데 쓰기가 안 되면 사용자는 원인을 못 찾는다 — 실제로 그랬다.
        assert _resolve("추이 시트 만들어줘") == "추이"
        assert _resolve("추이 시트 A2:C2에 05-26,1450,600 입력") == "추이"

    def test_a_chart_command_on_that_sheet_also_resolves(self):
        assert _resolve("추이 시트 A1:B8으로 선 그래프 만들어줘") == "추이"


class TestOrdinaryNamesUnaffected:
    @pytest.mark.parametrize("sheet", ["구성비", "매출", "Sheet"])
    def test_names_without_a_trailing_particle_are_unchanged(self, sheet):
        assert _resolve(f"{sheet} 시트 A1:B1에 값 입력") == sheet
        assert explicit_sheet_mentions(f"{sheet} 시트 A1에 값 입력") == [sheet]


class TestRealParticlesStillStripped:
    def test_a_particle_before_the_word_sheet_is_still_handled(self):
        # "매출의 시트" — '의'는 진짜 조사다. 묶음에 제거형이 들어 있어야
        # 실제 시트 목록과 대조할 때 걸린다.
        assert explicit_sheet_mention_variants("매출의 시트에 써줘") == [["매출의", "매출"]]

    def test_the_real_sheet_wins_when_a_particle_was_attached(self):
        assert _resolve("매출의 시트에 써줘") == "매출"

    def test_an_unknown_name_keeps_both_forms(self):
        # 아직 없는 시트를 지목한 경우다. 존재 판정은 호출부가 하므로 둘 다 남긴다.
        assert explicit_sheet_mention_variants("신규이 시트 만들어줘") == [["신규이", "신규"]]
