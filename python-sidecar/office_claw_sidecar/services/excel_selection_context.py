"""드래그한 영역을 명령의 대상으로 삼는다 — 살아 있는 선택이 옛 주소를 이긴다.

왜 필요한가:
    프론트는 `context_range`로 `lastExcelRangeRef`를 보낸다. 그런데 그건 **직전 명령의
    결과 주소**다. 사용자가 Excel에서 새로 드래그하고 "여기에 표 만들어줘"라고 해도
    옛 주소가 계획에 들어간다. 매번 "A3:J4"처럼 좌표를 부르게 하지 않으려면 지금
    끌어 둔 영역이 이겨야 한다.

    반대로 사용자가 문장에 범위를 직접 적었으면(`A1:C5 정렬해줘`) 그게 최우선이다.
    선택이 그걸 덮으면 사용자가 지정한 대상이 사라진다.

설계:
    - 판정은 순수 함수(`decide_selection_source`)로 두고 COM 조회는 호출부가 한다.
      Excel 없이 테스트된다.
    - 조회는 **필요할 때만** 한다. 매 턴 COM을 왕복하면 단순 명령이 느려진다.
"""

from __future__ import annotations

import re
from typing import Any

# "여기", "이 범위", "드래그한 영역" — 지금 보고 있는 선택을 가리키는 말.
_DEICTIC = re.compile(
    # 2026-08-17 실측: 사람이 쓸 법한 20가지 중 12가지만 잡았다. 좌표를 타이핑하는
    # 사람은 없다 — 드래그해 놓고 "이쪽에", "블록 잡은 데"라고 말한다. 여기서 놓치면
    # 드래그 기능 자체가 없는 것과 같아진다.
    r"여기|여기다|이쪽|요기|저기|저쪽|"
    r"이\s*(범위|영역|부분|표|자리|칸|셀)|"
    r"지금\s*(선택|드래그|잡은|칠한)|"
    r"선택(한|된|해\s*둔)|선택\s*(영역|범위|부분)|"
    r"드래그(한|해\s*둔)|끌어\s*둔|끌어\s*놓은|"
    r"방금\s*(선택|잡은|칠한|드래그)|"
    r"(잡아\s*놓은|잡아\s*둔|잡은|칠한|지정한|블록\s*잡은|음영\s*친)\s*(데|곳|범위|영역|부분)|"
    r"파란\s*(부분|영역)|"
    # 2026-08-18 실측: "컨트롤 c, 컨트롤 v 한 위치에 있는 모든 합을…" — 붙여넣기
    # 직후의 선택 영역이 곧 붙여넣은 자리다. 복붙 표현도 지시어다.
    r"붙여\s*넣(?:은|었던|기\s*한)|복붙(?:한|해\s*둔)?|"
    r"(?:컨트롤|ctrl)\s*\+?\s*(?:씨|브이|[cv])\b"
)

# 문장 안의 명시적 A1 범위. 이게 있으면 선택보다 우선한다.
_EXPLICIT_RANGE = re.compile(r"\b[A-Za-z]{1,3}\d{1,7}(?::[A-Za-z]{1,3}\d{1,7})?\b")


def mentions_explicit_range(message: str) -> bool:
    return bool(_EXPLICIT_RANGE.search(str(message or "")))


def mentions_selection(message: str) -> bool:
    return bool(_DEICTIC.search(str(message or "")))


def decide_selection_source(*, message: str, context_range: str | None) -> str:
    """이 턴의 대상 범위를 어디서 가져올지 정한다.

    반환값:
        "message"   — 문장에 범위가 적혀 있다. 아무것도 하지 않는다.
        "selection" — 지금 Excel에서 끌어 둔 영역을 읽어 써야 한다.
        "context"   — 프론트가 준 context_range를 그대로 쓴다.
    """
    text = str(message or "")
    if mentions_explicit_range(text):
        return "message"
    # "여기에" 라고 했으면 직전 결과 주소가 아니라 지금 선택을 뜻한다.
    if mentions_selection(text):
        return "selection"
    # 줄 만한 문맥이 아예 없으면 선택이라도 알려 주는 편이 낫다.
    if not str(context_range or "").strip():
        return "selection"
    return "context"


def resolve_context_range(
    service: Any,
    *,
    message: str,
    context_range: str | None,
    workbook_id: str | None,
    sheet_name: str | None,
) -> str | None:
    """`decide_selection_source`의 결정을 실제 주소로 바꾼다.

    조회에 실패하면 원래 값을 그대로 돌려준다 — 선택을 못 읽었다고 명령을 죽이지 않는다.
    """
    source = decide_selection_source(message=message, context_range=context_range)
    if source != "selection":
        return context_range
    getter = getattr(service, "get_active_selection_ref", None)
    if not callable(getter):
        return context_range
    if getattr(service, "has_real_selection", True) is False and context_range:
        # 파일 엔진의 '선택'은 드래그가 아니라 사용 범위 폴백이다. 그걸로
        # 붙여넣기 문맥을 덮으면 A8:B9에 넣을 값이 A1 표 위에 써진다
        # (2026-08-18 사람 말투 배터리 실측).
        return context_range
    try:
        live = str(getter(workbook_id, sheet_name) or "").strip().upper()
    except Exception:
        return context_range
    if not live:
        return context_range
    # 진짜 선택(드래그·클릭)이 있는 엔진에서는 한 칸 선택도 사용자의 지목이다 —
    # "H1을 클릭하고 '여기에 입력'"이 낡은 붙여넣기 범위로 가면 안 된다
    # (2026-08-18 멀티턴 사냥). 가짜 선택(파일 엔진)은 위에서 이미 걸렀다.
    return live
