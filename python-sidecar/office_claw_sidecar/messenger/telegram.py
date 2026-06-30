"""
messenger/telegram.py — Phase 1 (officeclaw).

역할: 자연어 패턴 인식 유틸 + TelegramService의 thin proxy adapter.

실제 봇 폴링 로직과 워크스페이스 명령 처리는 모두
services/telegram_service.py (TelegramService)에 위치한다.
이 모듈은 아래 두 가지만 담당한다:

1. 자연어 패턴 정규식 (_PATTERN_LIST)
   - telegram_service.py의 handle_text에서 import하여 사용
2. 파일명 추출 유틸 (_extract_filename, _parse_write_command)
   - 확장자 없는 파일명(readme, Makefile, .gitignore 등)도 지원

봇 폴링·명령 처리는 services/telegram_service.py의 TelegramService가 단독 담당한다.
routers/telegram.py의 telegram_svc 인스턴스를 사용할 것.
"""

from __future__ import annotations

import re

# ── 자연어 패턴 ──────────────────────────────────────────────────────────────

_PATTERN_LIST = re.compile(
    r"(?:파일\s*목록|목록\s*보여|list\s*files?|ls\b)", re.IGNORECASE
)

# ── 파일명 추출 패턴 (개선) ────────────────────────────────────────────────
# 1순위: 따옴표로 감싼 이름  → 'filename' 또는 "filename"
# 2순위: 확장자 포함 단어    → word.ext (마침표 + 1자 이상 알파숫자)
# 3순위: 점으로 시작하는 파일 → .gitignore, .env
# 4순위: 대문자로만 이루어진 단어 → Makefile, Dockerfile, README
# 5순위: 공백/특수문자로 구분된 마지막 의미 있는 단어 (동사형 제외)
_PATTERN_FILENAME = re.compile(
    r"['\"]([^'\"]+)['\"]"             # 1: 따옴표 감싼 이름
    r"|(\S+\.\w+)"                     # 2: 확장자 포함 단어
    r"|(\.\w+)"                        # 3: 점으로 시작하는 파일 (.gitignore)
    r"|([A-Z][a-z]*(?:[A-Z][a-z]*)*file\b)"  # 4: Makefile, Dockerfile 등
)

# 워크스페이스 명령 동사 — 파일명 추출 시 제외할 단어
_COMMAND_VERBS = frozenset({
    "읽어줘", "읽어", "읽기", "내용", "보여줘", "보여", "써줘", "쓰기", "저장",
    "read", "cat", "write", "save", "ls", "list", "files",
    "파일", "목록", "확인", "알려줘", "알려",
})


def _extract_filename(text: str) -> str | None:
    """
    텍스트에서 파일명을 추출한다.

    개선된 로직:
    - 따옴표로 감싼 이름 최우선
    - 확장자 포함 파일명
    - 점으로 시작하는 숨김 파일 (.gitignore)
    - Makefile, Dockerfile 류 대소문자 혼합 단어
    - 위 모두 실패 시: 공백으로 구분된 토큰 중 명령 동사가 아닌 마지막 단어
    """
    m = _PATTERN_FILENAME.search(text)
    if m:
        return m.group(1) or m.group(2) or m.group(3) or m.group(4)

    # fallback: 공백/특수문자로 구분된 토큰 중 동사가 아닌 마지막 의미 단어
    tokens = re.split(r"[\s,:!?]+", text.strip())
    # 뒤에서부터 탐색 — 명령 동사를 제외한 첫 번째 유효 토큰
    for token in reversed(tokens):
        clean = token.strip("'\".").lower()
        if clean and clean not in _COMMAND_VERBS and len(clean) >= 2:
            # 원본 대소문자 유지
            return token.strip("'\".")
    return None


def _parse_write_command(text: str) -> tuple[str | None, str | None]:
    """
    "파일명.txt 써줘: 내용" 또는 "파일명 써줘: 내용" 형태에서 파일명과 내용을 추출한다.

    개선된 로직:
    - 확장자 없는 파일명도 처리 ("readme 써줘: 내용")
    Returns (filename, content) 또는 (None, None).
    """
    # 패턴 1: "파일명.ext 써줘: 내용" — 확장자 있음
    m = re.search(
        r"(\S+\.\w+)\s+(?:써줘|쓰기|저장|write|save)[:]?\s*(.*)",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if m:
        return m.group(1).strip(), m.group(2).strip()

    # 패턴 2: "써줘 filename.ext: 내용" — 명령어 먼저, 파일명 뒤
    m2 = re.search(
        r"(?:써줘|쓰기|저장|write|save)\s+(\S+\.\w+)[:]?\s*(.*)",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if m2:
        return m2.group(1).strip(), m2.group(2).strip()

    # 패턴 3: "파일명 써줘: 내용" — 확장자 없는 파일명
    m3 = re.search(
        r"^(\S+)\s+(?:써줘|쓰기|저장|write|save)[:]?\s*(.*)",
        text.strip(),
        re.IGNORECASE | re.DOTALL,
    )
    if m3:
        candidate = m3.group(1).strip()
        # 명령 동사 자체인 경우 제외
        if candidate.lower() not in _COMMAND_VERBS:
            return candidate, m3.group(2).strip()

    # 패턴 4: "써줘: 파일명\n내용" — 콜론 이후에 파일명
    m4 = re.search(
        r"(?:써줘|쓰기|저장|write|save)[:]?\s*(\S+)\s+(.*)",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if m4:
        candidate = m4.group(1).strip()
        if candidate.lower() not in _COMMAND_VERBS:
            return candidate, m4.group(2).strip()

    return None, None
