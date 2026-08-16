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
