"""돌고 있는 사이드카가 **자기가 누구인지** 밝힌다 — 낡은 사이드카 감지용.

## 왜 사이드카가 스스로 판정하는가

앱(Rust)이 판정하려면 사이드카 소스가 어디 있는지 알아야 하는데, 그건 환경마다
다르다(소스 트리 실행 vs 설치본). 워크스페이스 경로 비교도 같은 이유로 못 쓴다
(`엑셀 작업 폴더` vs `<data_dir>/Workspace`). **자기 소스 위치를 아는 건 자기뿐**이라
판정을 여기서 하고 앱은 결과만 받는다.

## 판정 기준: 소스 수정 시각 > 프로세스 기동 시각

파이썬은 임포트 시점의 코드를 메모리에 들고 돈다. 그러니 기동 뒤에 `.py` 가 바뀌었으면
**돌고 있는 것은 그 변경 이전 코드**다. 2026-09-08 실측 사례가 정확히 이것이었다 —
워크스페이스 경로를 바꾼 커밋 이전에 뜬 사이드카가 살아남아, 앱이 옛 폴더를 보고
"파일을 찾을 수 없습니다"를 냈다.

`sys.frozen`(PyInstaller 배포본)은 소스 트리가 없으므로 검사 대상이 아니다.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

#: 프로세스가 임포트를 마친 시각. 모듈 최초 임포트 때 한 번만 잡힌다.
_STARTED_AT = time.time()

#: 기동 도중 건드려진 파일을 낡음으로 오판하지 않기 위한 여유(초).
#: 임포트가 끝난 뒤에도 에디터의 저장이 몇 초 늦게 반영되는 경우가 있다.
_STALE_MARGIN_S = 5.0


def _package_root() -> Path:
    return Path(__file__).resolve().parent


def newest_source_mtime() -> float:
    """패키지 안 `.py` 중 가장 최근 수정 시각. 못 읽으면 0.0."""
    newest = 0.0
    root = _package_root()
    for path in root.rglob("*.py"):
        # `__pycache__` 는 기동 뒤에 쓰이므로 본다면 항상 낡음이 된다.
        if "__pycache__" in path.parts:
            continue
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if mtime > newest:
            newest = mtime
    return newest


def describe_running_sidecar() -> dict:
    """앱이 대조할 수 있게 자기 신원과 낡음 여부를 낸다."""
    frozen = bool(getattr(sys, "frozen", False))
    newest = 0.0 if frozen else newest_source_mtime()
    stale = bool(newest and newest > _STARTED_AT + _STALE_MARGIN_S)

    return {
        "pid": os.getpid(),
        "started_at": _STARTED_AT,
        "newest_source_mtime": newest,
        "frozen": frozen,
        "code_stale": stale,
        # 워크스페이스는 낡음 판정에 쓰지 않는다(환경마다 다름) — 사람이 눈으로
        # 확인할 수 있게 **보여 주기만** 한다. 옛 사이드카는 여기에 옛 경로가 뜬다.
        "workspace_dir": _workspace_dir(),
    }


def _workspace_dir() -> str:
    try:
        from office_claw_sidecar.config import get_workspace_root

        return str(get_workspace_root())
    except Exception:
        return ""
