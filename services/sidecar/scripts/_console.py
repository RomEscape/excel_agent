# -*- coding: utf-8 -*-
"""콘솔 출력을 UTF-8로 못박는다 — 호출자가 `PYTHONUTF8=1`을 안 붙여도 살아남게.

2026-09-07 실측: `PYTHONUTF8=1` 없이 `show_turns.py -n 1` 을 돌리면 한국어를
출력하는 첫 `print()` 에서 **UnicodeEncodeError 로 죽는다**(cp949 콘솔). 사람 눈에는
"로그가 중간에 잘리고 글자가 깨진다"로 보인다 — 정작 `chat_log.jsonl` 자체는 멀쩡했다
(전수 검사: 잘린 필드 0건, U+FFFD 0건).

진단 도구가 **호출자의 환경 설정에 기대면 안 된다.** 로그를 보러 온 사람은 이미
무언가 잘못돼서 온 사람이고, 거기서 도구까지 죽으면 원인 찾기가 두 배로 어려워진다.
"""

from __future__ import annotations

import sys


def force_utf8() -> None:
    """stdout/stderr 를 UTF-8 로 재설정한다. 이미 UTF-8이면 아무 일도 하지 않는다.

    `errors="replace"` 는 마지막 방어선이다 — 인코딩할 수 없는 글자가 하나 있다고
    진단 출력 전체가 사라지는 것보다 그 글자만 대체 문자로 보이는 편이 낫다.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue  # 파이프로 감싸인 스트림 등 — 건드리지 않는다
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            # 재설정이 안 되는 환경에서도 스크립트는 계속 돌아야 한다.
            pass
