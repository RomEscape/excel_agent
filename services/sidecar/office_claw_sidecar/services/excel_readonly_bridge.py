"""읽기 전용 통합문서를 실제로 편집 가능하게 만든다.

문제 (2026-08-16 실측):
    이 PC의 Excel은 정품 인증이 안 된 무료 버전이라 여는 파일이 **전부 읽기 전용**이다.
    그 상태에서 편집을 시도하면 이렇게 죽는다:

        (-2147352567, '예외가 발생했습니다.', (0, 'Microsoft Excel',
         '파일이 읽기 전용인 경우에는 이 작업을 수행할 수 없습니다.', …))

    "그러면 file 엔진(openpyxl)으로 쓰면 되지 않나?" — 안 된다. Excel이 파일에
    **배타적 잠금**을 걸고 있어 openpyxl도 PermissionError로 막힌다:

        open(path, 'r+b') → PermissionError [Errno 13]
        ~$엑셀테스트_마지막.xlsx (Excel 잠금 파일) 존재

    즉 Excel이 붙들고 있는 한 어떤 경로로도 편집이 불가능하다.

해법:
    읽기 전용이면 **저장되지 않은 변경이 있을 수 없다.** 그러니 닫아도 사용자가
    잃는 것이 없다. 닫고 → 파일을 직접 편집하고 → 다시 연다.

    사용자에게 "Excel을 닫아 주세요"라고 부탁하는 대신 우리가 처리한다. 안내만
    하면 사용자는 매 명령마다 창을 닫았다 열어야 한다.

안전장치:
    - **읽기 전용일 때만** 닫는다. 편집 가능한 통합문서를 닫으면 작업 중이던
      내용이 사라진다. 호출부가 상태를 확인하고 부른다.
    - 닫기에 실패하면 아무것도 안 한 것과 같다(원래 차단 메시지로 돌아간다).
"""

from __future__ import annotations

import sys

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class BridgeResult:
    """브리지 시도의 결과."""

    released: bool
    path: str = ""
    note: str = ""


def can_bridge(flags: dict[str, Any] | None) -> bool:
    """이 상태를 브리지로 풀 수 있는가.

    읽기 전용만 해당한다. 시트 보호·구조 보호는 파일을 닫아도 안 풀리므로
    (보호 정보가 파일 안에 있다) 브리지 대상이 아니다.
    """
    if not flags:
        return False
    if not flags.get("workbook_read_only"):
        return False
    # 보호가 걸린 건 닫아도 그대로다. 괜히 창만 닫게 된다.
    return not (flags.get("sheet_protected") or flags.get("structure_protected"))


def release_workbook(service: Any, *, workbook_id: str | None) -> BridgeResult:
    """Excel에서 통합문서를 닫아 파일 잠금을 푼다.

    호출 전에 `can_bridge(flags)`로 읽기 전용임을 확인해야 한다.
    """
    closer = getattr(service, "close_workbook_without_saving", None)
    if not callable(closer):
        return BridgeResult(released=False, note="이 엔진은 통합문서를 닫을 수 없습니다.")
    try:
        path = str(closer(workbook_id) or "")
    except Exception as exc:
        return BridgeResult(released=False, note=f"통합문서를 닫지 못했습니다: {exc}")
    if not path:
        return BridgeResult(released=False, note="통합문서 경로를 알 수 없습니다.")
    return BridgeResult(released=True, path=path)


def restore_workbook(service: Any, path: str) -> bool:
    """편집이 끝난 파일을 Excel에서 다시 연다.

    실패해도 편집 자체는 이미 파일에 저장돼 있다 — 사용자가 직접 열면 보인다.
    """
    opener = getattr(service, "open_workbook_in_excel", None)
    if not callable(opener) or not path:
        return False
    try:
        return bool(opener(path))
    except Exception:
        return False

# Excel이 편집을 거부할 때 나오는 COM 오류들.
#   0x800A03EC (-2146827284) — "Application-defined or object-defined error".
#     정품 인증이 안 된 Excel에서 **모든 쓰기**가 이걸로 실패한다(2026-08-17 실측:
#     빈 셀 F1에 값 하나 쓰기도 실패). 상태 플래그는 ReadOnly=False라고 답한다.
#   0x800AC472 (-2146777988) — 다른 작업이 진행 중이라 거부(모달 대화상자 등).
_COM_WRITE_REFUSAL_CODES = frozenset({-2146827284, -2146777988})

# Excel 이 "지금은 못 받는다"고 되돌려보내는 COM 오류들 — 거부가 아니라 **사용 중**이다.
#   0x80010001 (-2147418111) RPC_E_CALL_REJECTED — 셀 편집 모드(커서가 셀 안에서 깜빡임),
#     모달 대화상자, 드래그 중 등. 사용자가 Esc 한 번 누르면 풀린다.
#   0x8001010A (-2147417846) RPC_E_SERVERCALL_RETRYLATER — 잠깐 뒤 다시 부르면 된다.
# 이걸 쓰기 거부로 오인해 파일 엔진 폴백을 타면 **사용자가 편집 중인 통합문서를 닫는다.**
# 2026-09-06 감사 전까지 코드 전체에 이 두 코드의 처리가 한 건도 없어, 화면에는
# "Excel Live 오류: (-2147418111, 'Call was rejected by callee.', None, None)" 이 그대로 떴다.
_COM_BUSY_CODES = frozenset({-2147418111, -2147417846})


def _has_com_code(exc: BaseException, codes: frozenset[int]) -> bool:
    args = getattr(exc, "args", ()) or ()
    for arg in args:
        if isinstance(arg, int) and arg in codes:
            return True
        if isinstance(arg, (tuple, list)):
            for inner in arg:
                if isinstance(inner, int) and inner in codes:
                    return True
    return False


def looks_like_com_busy(exc: BaseException) -> bool:
    """Excel 이 지금 응답할 수 없는 상태인가(셀 편집 중·대화상자 열림).

    거부(`looks_like_com_write_refusal`)와 반드시 갈라야 한다 — 거부는 파일 엔진으로
    우회하지만, 사용 중은 **기다렸다 다시 부르는 것**이 맞다. 우회하려고 통합문서를
    닫으면 사용자가 지금 치고 있던 내용이 사라진다.
    """
    return _has_com_code(exc, _COM_BUSY_CODES)


#: 사용 중일 때 사용자에게 보여 줄 말. 원인과 할 일을 같이 적는다.
COM_BUSY_MESSAGE = (
    "Excel이 지금 다른 작업 중이라 명령을 받지 못했습니다. "
    "셀을 편집 중이면 Enter나 Esc를 누르고, 열려 있는 대화상자를 닫은 뒤 다시 시도해 주세요."
)


def looks_like_com_write_refusal(exc: BaseException) -> bool:
    """이 예외가 "Excel이 쓰기를 거부했다"인가.

    플래그로는 알 수 없는 편집 불가 상태(정품 미인증 등)를 실제 실패로 판별한다.
    범위 오타 같은 진짜 오류까지 폴백시키면 원인이 가려지므로 코드로 좁힌다.
    """
    args = getattr(exc, "args", ()) or ()
    for arg in args:
        if isinstance(arg, int) and arg in _COM_WRITE_REFUSAL_CODES:
            return True
        if isinstance(arg, (tuple, list)):
            for item in arg:
                if isinstance(item, int) and item in _COM_WRITE_REFUSAL_CODES:
                    return True
    if sys.platform == "darwin":
        # appscript CommandError에는 COM HRESULT가 없다 — macOS에서 이 그물이
        # 한 번도 발동하지 않았다(2026-08-30 감사). 오류 문구로 좁혀 판별한다.
        text = str(exc).lower()
        if any(k in text for k in ("read only", "read-only", "읽기 전용", "protected", "locked")):
            return True
    return False
