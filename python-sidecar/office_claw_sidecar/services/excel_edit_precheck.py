"""편집 사전 점검 — 쓰기 전에 대상이 쓸 수 있는 상태인지 본다.

`excel_result_verifier`가 "한 뒤에 됐는가"를 본다면 이 모듈은 "하기 전에 될 수
있는가"를 본다. 같은 계열의 세 번째 모듈이다.

왜 필요한가 (2026-08-16 조사):
    사이드카 전체에서 워크북·시트의 읽기 전용/보호 상태를 **실제로 조회하는 코드가
    0건**이었다. "읽기 전용"이라는 말은 사용자가 그 단어를 문장에 썼을 때만 켜지는
    힌트(`excel_live.py:3715`)와 되묻기 문장 한 줄로만 존재했다. 그래서 읽기 전용
    파일에서도 계획을 세우고 실행하다 죽었다.

    더 나쁜 쪽은 file 엔진이다 — openpyxl은 시트 보호를 통째로 무시하고 보호된
    시트에 값을 써서 **저장까지 성공한다**(실측). 사전 점검이 없으면 방어선이 없다.

설계:
    - 이 모듈은 **상태를 갖지 않는다.** 플래그를 받아 판정만 한다. 상태 조회는
      워크북 핸들을 쥔 서비스가 한다(CLAUDE.md §4 "상태는 모듈이 소유한다").
    - Excel이 없는 환경에서도 테스트된다 — 플래그가 입력이라 COM이 필요 없다.
    - **면제 목록이 핵심이다.** 편집 액션을 통째로 막으면 읽기 전용 파일에서
      PDF 내보내기나 보호 해제 같은 정상 요청까지 반려된다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# 읽기 전용이어도 원본을 안 건드리는 액션.
READ_ONLY_SAFE_ACTIONS = frozenset(
    {
        "excel_live.export_pdf",  # 별도 PDF만 만든다
        "excel_live.recalculate",  # 화면 재계산
        "excel_live.save_workbook",  # 실패는 저장기가 알려야 '사본 저장'으로 유도된다
    }
)

# 보호 해제가 목적이라 보호 상태에서도 실행돼야 하는 액션.
# `excel_live_service.protect_sheet`는 보호를 걸기 전에 먼저 Unprotect를 부른다.
SHEET_PROTECTION_EXEMPT = frozenset({"excel_live.protect_sheet"})

# 시트 추가/삭제/이름변경 — 통합문서 **구조** 보호에 걸린다.
STRUCTURE_SENSITIVE_ACTIONS = frozenset(
    {
        "excel_live.create_sheet",
        "excel_live.rename_sheet",
        "excel_live.delete_sheet",
        "excel_live.pivot_table",
        "excel_live.consolidate_sheets",
        "excel_live.consolidate_workbooks_from_folder",
    }
)


class ExcelEditBlockedError(Exception):
    """보호 상태라 편집을 실행하지 않았다.

    실행 실패가 아니라 **실행하지 않기로 한 결정**이다. 메시지는 사용자에게 그대로
    보여도 되는 한국어 문장이다.
    """

    def __init__(self, message: str, *, code: str = "") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class WriteBlock:
    """편집을 막아야 하는가, 막는다면 왜."""

    blocked: bool
    code: str = ""
    reason: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.blocked


_ALLOW = WriteBlock(blocked=False)


def evaluate_write_block(
    *,
    action: str,
    flags: dict[str, Any] | None,
    is_edit_action: bool,
) -> WriteBlock:
    """보호 플래그와 액션을 보고 편집을 막을지 정한다.

    `flags`가 None이면(=상태를 못 읽었으면) **막지 않는다.** 모르는 것을 결함으로
    단정하면 멀쩡한 편집이 전부 반려된다 — 커버리지 검사와 같은 원칙이다.
    """
    if not is_edit_action or not flags:
        return _ALLOW

    name = str(action or "")

    if flags.get("workbook_read_only") and name not in READ_ONLY_SAFE_ACTIONS:
        return WriteBlock(
            blocked=True,
            code="workbook_read_only",
            reason=(
                "이 통합문서는 읽기 전용으로 열려 있어 편집할 수 없습니다. "
                "Excel에서 [읽기 전용 해제] 후 다시 시도하거나, 편집 가능한 사본으로 "
                "저장할까요?"
            ),
            detail=dict(flags),
        )

    if flags.get("marked_final") and name not in READ_ONLY_SAFE_ACTIONS:
        return WriteBlock(
            blocked=True,
            code="marked_final",
            reason=(
                "이 통합문서는 '최종본으로 표시'되어 있어 편집이 잠겨 있습니다. "
                "Excel 상단의 [계속 편집]을 누른 뒤 다시 말씀해 주세요."
            ),
            detail=dict(flags),
        )

    if flags.get("structure_protected") and name in STRUCTURE_SENSITIVE_ACTIONS:
        return WriteBlock(
            blocked=True,
            code="structure_protected",
            reason=(
                "이 통합문서는 구조가 보호되어 있어 시트를 추가·삭제·이름 변경할 수 "
                "없습니다. [검토] → [통합 문서 보호]를 해제한 뒤 다시 시도해 주세요."
            ),
            detail=dict(flags),
        )

    # 시트 보호에서도 원본을 안 건드리는 액션(PDF 내보내기 등)은 통과시킨다.
    # 2026-08-16: 여기 READ_ONLY_SAFE_ACTIONS를 빠뜨려 보호된 시트에서 PDF 내보내기가
    # 막혔다 — 단위 테스트는 통과했고 실제 파일로 돌려 보고서야 드러났다.
    if (
        flags.get("sheet_protected")
        and name not in SHEET_PROTECTION_EXEMPT
        and name not in READ_ONLY_SAFE_ACTIONS
    ):
        sheet = str(flags.get("sheet_name") or "").strip()
        where = f"'{sheet}' 시트는" if sheet else "이 시트는"
        return WriteBlock(
            blocked=True,
            code="sheet_protected",
            reason=(
                f"{where} 보호되어 있어 셀을 편집할 수 없습니다. "
                "시트 보호를 해제할까요?"
            ),
            detail=dict(flags),
        )

    return _ALLOW


def read_protection_flags(
    service: Any, *, workbook_id: str | None, sheet_name: str | None
) -> dict[str, Any] | None:
    """서비스에서 보호 상태를 읽는다. 못 읽으면 None(=판단 보류).

    서비스가 `get_write_protection`을 갖고 있을 때만 부른다 — 테스트의 가짜
    서비스 대부분은 `ExcelLiveService`를 상속하지 않아 이 메서드가 없다.
    가드를 빼면 그 파일 하나에서만 테스트 98개가 한 번에 깨진다.
    """
    getter = getattr(service, "get_write_protection", None)
    if not callable(getter):
        return None
    try:
        flags = getter(workbook_id, sheet_name)
    except Exception:
        return None
    return dict(flags) if isinstance(flags, dict) else None
