"""Excel 표 생성용 업무 템플릿 프리셋."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TablePreset:
    key: str
    keywords: tuple[str, ...]
    headers: tuple[str, ...]
    default_rows: int
    follow_up_question: str

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
        follow_up_question="근태표는 일별/월별 중 어떤 형식으로 만들까요? (기본: 월별)",
    ),
    TablePreset(
        key="sales",
        keywords=("매출 정리", "매출표", "매출 관리"),
        headers=("월", "상품명", "판매수량", "단가", "총매출"),
        default_rows=24,
        follow_up_question="매출표는 월별/상품별 중 어떤 기준으로 정리할까요?",
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
    for preset in TABLE_PRESETS:
        if any(keyword in lowered for keyword in preset.keywords):
            return preset
    return None


def get_table_preset(key: str | None) -> TablePreset | None:
    target = str(key or "").strip().lower()
    if not target:
        return None
    for preset in TABLE_PRESETS:
        if preset.key == target:
            return preset
    return None

