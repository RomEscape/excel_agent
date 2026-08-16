"""매크로 분해기 단위 테스트.

가장 큰 위험은 휴리스틱 오탐이다. 단순 명령이 매크로로 새면 멀쩡히 돌던 경로에
승인 화면이 끼어들어 예전만 못한 앱이 된다. 그래서 판별 테스트를 먼저 둔다.
"""

from __future__ import annotations

import pytest

from office_claw_sidecar.services.excel_macro_planner import (
    MAX_MACRO_STEPS,
    build_macro_prompt,
    looks_like_macro_request,
    validate_macro_steps,
)

MACRO_MESSAGES = [
    "대시보드 만들어줘",
    "Sales_Data로 매출 대시보드 만들어줘",
    "월간 보고서 작성해줘",
    "지역별 집계표 만들어줘",
    "판매 현황판 구축해줘",
]

SIMPLE_MESSAGES = [
    "A1에 안녕 입력",
    "B2:B10 합계 구해줘",
    "표 만들어줘",
    "Dashboard 시트 A1에 총매출 입력",
    "Sales_Data 시트 J2:J61에 수식 =E2*F2 적용",
    "3행 지워줘",
    "저장해줘",
    "",
]


@pytest.mark.parametrize("message", MACRO_MESSAGES)
def test_composite_artifact_requests_are_macro_sized(message):
    assert looks_like_macro_request(message) is True


@pytest.mark.parametrize("message", SIMPLE_MESSAGES)
def test_ordinary_commands_stay_on_the_single_command_path(message):
    assert looks_like_macro_request(message) is False


def test_cell_reference_pins_the_request_to_one_command():
    """셀을 콕 집었으면 이미 구체적인 지시다 — 대시보드라는 말이 있어도 매크로가 아니다."""
    assert looks_like_macro_request("A1에 대시보드 제목 작성해줘") is False


@pytest.mark.parametrize(
    "message",
    [
        "대시보드처럼 한눈에 정리해줘",
        "보고서 같이 보기 좋게 정리해줘",
        "대시보드 느낌으로 꾸며줘",
    ],
)
def test_comparisons_are_not_build_requests(message):
    """"대시보드처럼"은 비유다 — 대시보드를 만들어 달라는 말이 아니다."""
    assert looks_like_macro_request(message) is False


@pytest.mark.parametrize(
    "message",
    [
        "Dashboard 시트 만들어줘",
        "대시보드 시트 만들어줘",
        "보고서 시트를 만들어줘",
        "집계표 시트 만들어줘",
        "report 탭 만들어줘",
    ],
)
def test_naming_a_sheet_is_not_a_macro_request(message):
    """"Dashboard 시트 만들어줘"는 그 이름의 시트 하나를 만들어 달라는 말이다.

    이걸 매크로로 넘기면 create_sheet 한 번이면 될 일이 17단계 승인 화면이 되고,
    사용자가 그 화면을 지나치면 시트가 아예 생기지 않는다. 2026-08-16 실측에서
    Dashboard 시트가 끝내 안 만들어졌고, 뒤따르는 "Dashboard 시트 A4에 ~" 명령들이
    활성 시트(Lookup)를 덮어썼다.
    """
    assert looks_like_macro_request(message) is False


def test_building_a_dashboard_is_still_a_macro_request():
    """시트 이름 예외가 진짜 대시보드 요청까지 삼키면 안 된다."""
    assert looks_like_macro_request("Sales_Data로 매출 대시보드 만들어줘") is True
    assert looks_like_macro_request("대시보드 만들어줘") is True


def test_prompt_carries_the_measured_dashboard_example():
    """검증된 명령 목록이 few-shot에서 빠지면 분해 품질이 그만큼 떨어진다.

    2026-08-16: 제목 행을 1행 단독으로 빼고 KPI를 3행부터 두도록 예시를 다시 짰다.
    값이 든 칸 위에서 병합하면 그 값이 사라지기 때문이다(A1:C1 병합이 B1의 총매출을 삼켰다).
    그래서 지역별 표가 A6:A11 -> A8:A13으로 내려갔다.
    """
    prompt = build_macro_prompt("대시보드 만들어줘", digest_text="시트 Sales_Data")
    assert "=SUMIF(Sales_Data!$C$2:$C$61,A8,Sales_Data!$J$2:$J$61)" in prompt
    assert "시트 Sales_Data" in prompt
    assert str(MAX_MACRO_STEPS) in prompt


def test_prompt_allows_the_formatting_pass():
    """규칙 5가 서식을 금지하면 대시보드가 민무늬로 나온다.

    2026-08-16 실측: 금지 상태에서 Dashboard의 굵게·배경색·경계선·병합·숫자서식이 전부 0칸이었다.
    액션은 전부 구현돼 있는데 프롬프트만 "만들 수 없다"고 적혀 있었다.
    """
    prompt = build_macro_prompt("대시보드 만들어줘", digest_text="시트 Sales_Data")
    for phrase in ("병합해줘", "배경색", "글자 굵게", "경계선 적용", "숫자 형식"):
        assert phrase in prompt, phrase
    # 아직 반영되지 않는 것은 계속 막아 둔다.
    assert "글자 크기" in prompt and "넣지 않는 것" in prompt


def test_validation_numbers_and_trims_commands():
    steps = validate_macro_steps(["  첫 명령  ", "둘째 명령"])

    assert [step.index for step in steps] == [1, 2]
    assert steps[0].command == "첫 명령"


def test_validation_drops_duplicates_and_blanks():
    """같은 명령을 두 번 실행하면 값이 덮이거나 시트가 중복 생성된다."""
    steps = validate_macro_steps(["A 명령", "", "A 명령", "   ", "B 명령"])

    assert [step.command for step in steps] == ["A 명령", "B 명령"]


def test_validation_caps_total_steps():
    steps = validate_macro_steps([f"{i}번 명령" for i in range(MAX_MACRO_STEPS + 10)])

    assert len(steps) == MAX_MACRO_STEPS


def test_validation_rejects_empty_result():
    with pytest.raises(ValueError):
        validate_macro_steps(["", "   "])


def test_validation_rejects_non_list():
    with pytest.raises(TypeError):
        validate_macro_steps({"steps": ["A"]})


def test_validation_flags_destructive_commands():
    steps = validate_macro_steps(["Sheet1 A1:B2 값 지워줘", "Sheet1 A1에 매출 입력"])

    assert steps[0].destructive is True
    assert steps[1].destructive is False


def test_validation_warns_about_sheets_missing_from_the_digest():
    digest = {"sheets": [{"name": "Sales_Data"}]}
    steps = validate_macro_steps(
        ["Sales_Data 시트 A1에 매출 입력", "Unknown 시트 A1에 매출 입력"],
        digest=digest,
    )

    assert steps[0].warnings == []
    assert steps[1].warnings and "Unknown" in steps[1].warnings[0]


def test_measured_dashboard_plan_survives_validation_intact():
    """골드 시나리오 — 실측 18개 명령(18/18 · 14/14 통과분)을 그대로 통과시켜야 한다.

    검증이 이 계획을 깎아내면(중복 제거·상한·경고) 모델이 정답을 내도 결과물이 줄어든다.
    """
    commands = [
        "Sales_Data 시트 J1에 매출 입력",
        "Sales_Data 시트 J2:J61에 수식 =E2*F2*(1-G2) 적용",
        "Sales_Data 시트 K1에 이익 입력",
        "Sales_Data 시트 K2:K61에 수식 =J2-(H2*F2) 적용",
        "Dashboard 시트 만들어줘",
        "Dashboard 시트 A1:A3에 총매출,총이익,평균주문금액 입력",
        "Dashboard 시트 B1에 수식 =SUM(Sales_Data!J2:J61) 적용",
        "Dashboard 시트 B2에 수식 =SUM(Sales_Data!K2:K61) 적용",
        "Dashboard 시트 B3에 수식 =AVERAGE(Sales_Data!J2:J61) 적용",
        "Dashboard 시트 A5:C5에 지역,매출,이익 입력",
        "Dashboard 시트 A6:A11에 서울,경기,충청,영남,호남,강원 입력",
        "Dashboard 시트 B6:B11에 수식 =SUMIF(Sales_Data!$C$2:$C$61,A6,Sales_Data!$J$2:$J$61) 적용",
        "Dashboard 시트 C6:C11에 수식 =SUMIF(Sales_Data!$C$2:$C$61,A6,Sales_Data!$K$2:$K$61) 적용",
        "Dashboard 시트 A5:C11로 막대 차트 만들어줘",
        "Sales_Data 시트 J2:J61에 데이터 막대 적용해줘",
        "Sales_Data 시트 틀 고정해줘",
        "Dashboard 시트 열 너비 자동 맞춤",
        "Sales_Data 시트 J2:K61에 숫자 형식 #,##0 적용",
    ]
    digest = {"sheets": [{"name": "Sales_Data"}]}

    steps = validate_macro_steps(commands, digest=digest)

    assert len(steps) == len(commands)
    assert [step.command for step in steps] == commands
    assert [step.index for step in steps] == list(range(1, len(commands) + 1))
    # 값을 쌓기만 하는 계획이다 — 삭제 경고가 붙으면 사용자가 승인을 망설인다.
    assert [step.command for step in steps if step.destructive] == []
    # Dashboard는 계획 안에서 만들어진다. "없는 시트" 경고를 붙이면 오히려 오해를 준다.
    assert [step.command for step in steps if step.warnings] == []


def test_sheet_creation_is_not_warned_about():
    """새로 만드는 시트는 아직 통합문서에 없는 게 정상이다."""
    digest = {"sheets": [{"name": "Sales_Data"}]}
    steps = validate_macro_steps(["Dashboard 시트 만들어줘"], digest=digest)

    assert steps[0].warnings == []
