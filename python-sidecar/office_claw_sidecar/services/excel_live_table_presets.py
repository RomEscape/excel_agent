"""Excel 표 생성용 업무 템플릿 프리셋.

2026-08-17 GUI 실측이 드러낸 설계 결함: 질문("일별/월별 중 어떤 형식으로?")은
던지는데 **그 답을 해석하는 코드가 없었다.** "일별"이라고 정확히 답해도 긍정어
("응/네")가 아니라서 같은 질문을 또 하고, 되묻기 한도가 차면 프리셋 헤더도 버린 채
5×5 빈 표로 떨어졌다. 사용자: "왜 출석부 만들어달라니까 근태표를 만드는 작업을 하지?"

그래서 프리셋이 선택지(`variants`)를 데이터로 갖고, 라우터가 답에서 선택지를
찾아 그 헤더를 쓴다. 선택지를 못 찾은 답이라도 **질문은 한 번만** — 답이 뭐든
기본형으로 진행한다. 질문만 하고 해석 못 하는 선택지는 여기 넣지 않는다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class TablePreset:
    key: str
    keywords: tuple[str, ...]
    headers: tuple[str, ...]
    default_rows: int
    # "{name}"이 있으면 사용자가 실제로 쓴 낱말로 채워진다 — "출석부"라고 했는데
    # "근태표는…"이라고 물으면 다른 걸 만드는 것처럼 들린다(2026-08-17 실측).
    follow_up_question: str
    # (선택지 낱말, 그 선택지의 헤더). 답 문장 어디에 있어도 잡는다("일별로 만들어줘").
    variants: tuple[tuple[str, tuple[str, ...]], ...] = field(default=())

    @property
    def default_cols(self) -> int:
        return max(1, len(self.headers))


TABLE_PRESETS: tuple[TablePreset, ...] = (
    TablePreset(
        key="meeting_notes",
        keywords=("회의록",),
        headers=("날짜", "참석자", "안건", "결정사항", "담당자", "기한"),
        default_rows=20,
        follow_up_question=(
            "회의록 표에 날짜/참석자/안건/결정사항/담당자/기한 항목으로 만들까요?"
        ),
    ),
    TablePreset(
        key="attendance",
        keywords=("근태", "출석부"),
        headers=("이름", "부서", "날짜", "출근", "지각", "결근", "휴가", "비고"),
        default_rows=32,
        follow_up_question="{name}는 일별/월별 중 어떤 형식으로 만들까요? (기본: 월별)",
        variants=(
            ("일별", ("날짜", "이름", "출근 시간", "퇴근 시간", "지각", "결근", "비고")),
            ("월별", ("이름", "부서", "날짜", "출근", "지각", "결근", "휴가", "비고")),
        ),
    ),
    TablePreset(
        key="sales",
        keywords=("매출 정리", "매출표", "매출 관리"),
        headers=("월", "상품명", "판매수량", "단가", "총매출"),
        default_rows=24,
        follow_up_question="매출표는 월별/상품별 중 어떤 기준으로 정리할까요?",
        variants=(
            ("월별", ("월", "상품명", "판매수량", "단가", "총매출")),
            ("상품별", ("상품명", "월", "판매수량", "단가", "총매출")),
        ),
    ),
    TablePreset(
        key="checklist",
        keywords=("체크리스트",),
        headers=("작업명", "담당자", "상태", "우선순위", "마감일", "비고"),
        default_rows=30,
        follow_up_question="체크리스트 용도를 알려주세요. (예: 프로젝트 진행 상황 확인용)",
    ),
    TablePreset(
        key="budget",
        keywords=("예산 관리", "예산표"),
        headers=("항목", "예산", "실제 지출", "차이", "사용률", "비고"),
        default_rows=24,
        follow_up_question="예산표는 개인/프로젝트/부서 중 어떤 용도인가요?",
    ),
    TablePreset(
        key="task_tracker",
        keywords=("할 일 관리", "일정표", "프로젝트 일정"),
        headers=("업무명", "담당자", "우선순위", "상태", "시작일", "마감일", "진행률"),
        default_rows=30,
        follow_up_question="할 일 관리표는 개인용인가요, 팀 공유용인가요?",
    ),
    TablePreset(
        key="inventory",
        keywords=("재고 관리",),
        headers=("상품명", "초기재고", "입고", "출고", "현재재고", "안전재고", "상태"),
        default_rows=30,
        follow_up_question="재고표에 입고/출고까지 함께 관리할까요?",
    ),
    TablePreset(
        key="score",
        keywords=("성적표",),
        headers=("학생명", "과목1", "과목2", "과목3", "총점", "평균", "등급"),
        default_rows=40,
        follow_up_question="성적표에 등급 자동 계산까지 넣을까요?",
    ),
    TablePreset(
        key="customer",
        keywords=("고객 관리", "고객표"),
        headers=("고객명", "연락처", "이메일", "최근 상담일", "상담 내용", "다음 연락일", "상태"),
        default_rows=40,
        follow_up_question="고객 관리표에 상담 이력까지 포함할까요?",
    ),
)


def match_table_preset(message: str) -> TablePreset | None:
    lowered = str(message or "").lower()
    # "A1:F9에 매출표라는 이름 정의해줘"는 범위에 **이름을 붙이는** 요청이지 매출표를
    # 만드는 게 아니다 — 키워드 부분일치가 이걸 프리셋 인터뷰로 끌고 가 "월별/상품별
    # 기준?"을 되물었다(2026-08-26 커버리지 0845 실측). 이름-정의 문형이면 물러난다.
    if re.search(r"(?:이?라는|이?란|으로|로)?\s*이름\s*(?:을|이|은)?\s*(?:정의|붙|지어|달아)", lowered):
        return None
    for preset in TABLE_PRESETS:
        if any(keyword in lowered for keyword in preset.keywords):
            return preset
    return None


# 되묻기 문구에 쓸 표시 이름. 키워드가 접두("근태")면 자연스러운 낱말로 바꾼다.
_DISPLAY_NAMES = {"근태": "근태표"}


def preset_follow_up(preset: TablePreset, message: str) -> str:
    """사용자가 실제로 쓴 낱말로 되묻기 문구를 만든다.

    "출석부 만들어줘"에 "근태표는 …?"라고 물으면 다른 걸 만드는 것처럼 들린다
    (2026-08-17 실측 — 사용자가 정확히 그 지점을 지적했다).
    """
    lowered = str(message or "").lower()
    matched = next((k for k in preset.keywords if k in lowered), preset.keywords[0])
    name = _DISPLAY_NAMES.get(matched, matched)
    return preset.follow_up_question.replace("{name}", name)


def find_variant(preset: TablePreset | None, message: str) -> tuple[str, tuple[str, ...]] | None:
    """답 문장에서 선택지를 찾는다. "일별로 만들어줘"처럼 조사·동사가 붙어도 잡는다."""
    if preset is None:
        return None
    lowered = str(message or "").lower()
    for name, headers in preset.variants:
        if name in lowered:
            return name, headers
    return None


def get_table_preset(key: str | None) -> TablePreset | None:
    target = str(key or "").strip().lower()
    if not target:
        return None
    for preset in TABLE_PRESETS:
        if preset.key == target:
            return preset
    return None

