"""
민감 데이터 자동 감지 및 마스킹 서비스.

LLM에 메시지를 전달하기 전에 아래 유형을 자동 마스킹한다:
  - 주민등록번호
  - 카드번호 (Luhn 검증 포함)
  - 이메일 주소
  - 한국 전화번호
  - 계좌번호 (은행명 인접 시만)
  - 여권번호

설계 원칙:
  - 마스킹은 단방향: 응답에는 마스킹된 텍스트만 표시
  - 원본은 세션 메모리에만 임시 보관 (디스크 저장 안 함)
  - 마스킹 발생 시 감사 로그에 기록
"""

from __future__ import annotations

import re
import logging
from typing import Final

from office_claw_sidecar.models.masking import Detection, MaskResult

logger = logging.getLogger(__name__)

# ── 은행명 키워드 (계좌번호 false positive 방지) ──────────────────────────────

_BANK_KEYWORDS: Final[str] = (
    r"(?:국민|신한|우리|하나|농협|기업|산업|외환|씨티|카카오|토스|케이|광주|전북|경남|부산|대구|수협|저축"
    r"|KB|SC제일|BNK|DGB|JB|IBK|KEB|NH|SH수협)"
)

# ── 정규식 패턴 정의 ──────────────────────────────────────────────────────────

# 주민등록번호: 6자리-7자리, 뒷자리 첫 숫자는 1~8
# 앞에 날짜처럼 보이는 4자리 숫자가 있는 경우 (2024-01-01) 제외를 위해
# lookahead로 4자리 숫자 직후는 제외
_RRN_PATTERN = re.compile(
    r"(?<!\d)"              # 앞에 숫자 없음 (날짜 일부 방지)
    r"(\d{6})"              # 생년월일 6자리
    r"[-\s]?"               # 선택적 구분자
    r"([1-8]\d{6})"         # 뒷자리: 1~8로 시작 + 6자리
    r"(?!\d)"               # 뒤에 숫자 없음
)

# 카드번호: 4-4-4-4 형식 (Visa=4, MC=5, Amex=3, 국내=9 등)
_CARD_PATTERN = re.compile(
    r"(?<!\d)"
    r"([3459]\d{3})"        # 첫 블록: 주요 카드 브랜드 첫 자리
    r"[-\s]?"
    r"(\d{4})"
    r"[-\s]?"
    r"(\d{4})"
    r"[-\s]?"
    r"(\d{4})"
    r"(?!\d)"
)

# 이메일 주소 (RFC 5322 간소화)
_EMAIL_PATTERN = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"
)

# 한국 전화번호: 010/011/016/017/018/019 - 3~4자리 - 4자리
_PHONE_PATTERN = re.compile(
    r"(?<!\d)"
    r"(01[016789])"
    r"[-\s]?"
    r"(\d{3,4})"
    r"[-\s]?"
    r"(\d{4})"
    r"(?!\d)"
)

# 계좌번호: N-N-N 형식 (은행명 인접 시만 매칭, lookahead/behind)
_ACCOUNT_PATTERN = re.compile(
    r"(?:" + _BANK_KEYWORDS + r"\s{0,4})"  # 앞에 은행명
    r"(\d{2,6}[-\s]\d{2,6}[-\s]\d{2,6}(?:[-\s]\d{2,3})?)"
)

# 여권번호: 1~2 알파벳 + 7~8 숫자
_PASSPORT_PATTERN = re.compile(
    r"(?<![A-Za-z])"
    r"([A-Z]{1,2}\d{7,8})"
    r"(?!\d)"
)


# ── Luhn 알고리즘 ────────────────────────────────────────────────────────────

def _luhn_check(number: str) -> bool:
    """카드번호 Luhn 알고리즘 검증. 유효하면 True."""
    digits = [int(c) for c in number if c.isdigit()]
    if len(digits) < 13:
        return False
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


# ── 마스킹 서비스 ────────────────────────────────────────────────────────────

class MaskingService:
    """
    민감 데이터 자동 감지 및 마스킹.

    사용 예:
        svc = MaskingService()
        result = svc.mask("주민번호는 880101-1234567입니다")
        # result.masked_text == "주민번호는 [주민번호 마스킹됨]입니다"
        # result.was_modified == True

    옵션:
        mask_email: 이메일 주소 마스킹 여부 (기본 False — 업무 맥락에서 이메일은 식별자로 빈번히 사용됨)
        mask_phone: 전화번호 마스킹 여부 (기본 False — 업무 연락처는 의도적으로 공유되는 경우가 많음)
    """

    def __init__(self, mask_email: bool = False, mask_phone: bool = False) -> None:
        self.mask_email = mask_email
        self.mask_phone = mask_phone

    def mask(self, text: str) -> MaskResult:
        """
        텍스트에서 민감 데이터를 찾아 플레이스홀더로 치환한다.

        여러 유형이 겹치는 경우 앞쪽 유형이 우선한다.
        이미 처리된 위치는 재처리하지 않는다.
        """
        if not text:
            return MaskResult(masked_text=text, detections=[], was_modified=False)

        # (start, end, placeholder, type_name) 목록 수집
        replacements: list[tuple[int, int, str, str]] = []

        self._collect_rrn(text, replacements)
        self._collect_card(text, replacements)
        if self.mask_email:
            self._collect_email(text, replacements)
        if self.mask_phone:
            self._collect_phone(text, replacements)
        self._collect_account(text, replacements)
        self._collect_passport(text, replacements)

        if not replacements:
            return MaskResult(masked_text=text, detections=[], was_modified=False)

        # 겹치는 범위 제거 후 위치 순 정렬
        replacements = _deduplicate(replacements)
        replacements.sort(key=lambda x: x[0])

        # 텍스트 재조합
        masked_parts: list[str] = []
        detections: list[Detection] = []
        prev_end = 0

        for start, end, placeholder, type_name in replacements:
            masked_parts.append(text[prev_end:start])
            masked_parts.append(placeholder)
            detections.append(Detection(
                type=type_name,
                placeholder=placeholder,
                start=start,
                end=end,
            ))
            prev_end = end

        masked_parts.append(text[prev_end:])
        masked_text = "".join(masked_parts)

        return MaskResult(
            masked_text=masked_text,
            detections=detections,
            was_modified=True,
        )

    # ── 유형별 수집 메서드 ───────────────────────────────────────────────────

    def _collect_rrn(
        self, text: str, out: list[tuple[int, int, str, str]]
    ) -> None:
        for m in _RRN_PATTERN.finditer(text):
            out.append((m.start(), m.end(), "[주민번호 마스킹됨]", "주민등록번호"))

    def _collect_card(
        self, text: str, out: list[tuple[int, int, str, str]]
    ) -> None:
        for m in _CARD_PATTERN.finditer(text):
            raw_digits = re.sub(r"[-\s]", "", m.group(0))
            if _luhn_check(raw_digits):
                out.append((m.start(), m.end(), "[카드번호 마스킹됨]", "카드번호"))

    def _collect_email(
        self, text: str, out: list[tuple[int, int, str, str]]
    ) -> None:
        for m in _EMAIL_PATTERN.finditer(text):
            out.append((m.start(), m.end(), "[이메일 마스킹됨]", "이메일 주소"))

    def _collect_phone(
        self, text: str, out: list[tuple[int, int, str, str]]
    ) -> None:
        for m in _PHONE_PATTERN.finditer(text):
            out.append((m.start(), m.end(), "[전화번호 마스킹됨]", "전화번호"))

    def _collect_account(
        self, text: str, out: list[tuple[int, int, str, str]]
    ) -> None:
        for m in _ACCOUNT_PATTERN.finditer(text):
            # 계좌번호는 그룹 1이 실제 번호 부분
            # 전체 매치(은행명 포함)를 치환 대상으로
            out.append((m.start(), m.end(), "[계좌번호 마스킹됨]", "계좌번호"))

    def _collect_passport(
        self, text: str, out: list[tuple[int, int, str, str]]
    ) -> None:
        for m in _PASSPORT_PATTERN.finditer(text):
            out.append((m.start(), m.end(), "[여권번호 마스킹됨]", "여권번호"))


# ── 헬퍼 ─────────────────────────────────────────────────────────────────────

def _deduplicate(
    replacements: list[tuple[int, int, str, str]],
) -> list[tuple[int, int, str, str]]:
    """
    겹치는 범위를 제거한다. 먼저 추가된 항목(선언 순서 = 우선순위)을 유지한다.
    O(n log n).
    """
    # 시작 위치 기준 정렬
    sorted_reps = sorted(replacements, key=lambda x: (x[0], -(x[1] - x[0])))
    result: list[tuple[int, int, str, str]] = []
    last_end = -1
    for rep in sorted_reps:
        start, end = rep[0], rep[1]
        if start >= last_end:
            result.append(rep)
            last_end = end
    return result


# ── 싱글톤 ──────────────────────────────────────────────────────────────────

_service: MaskingService | None = None


def get_masking_service() -> MaskingService:
    """MaskingService 싱글톤을 반환한다.

    설정 변경 시 reset_masking_service()를 호출한 후 재조회해야 한다.
    """
    global _service
    if _service is None:
        _service = MaskingService()
    return _service


def reset_masking_service(mask_email: bool = False, mask_phone: bool = False) -> MaskingService:
    """설정을 반영한 새 MaskingService 싱글톤을 생성·반환한다."""
    global _service
    _service = MaskingService(mask_email=mask_email, mask_phone=mask_phone)
    return _service
