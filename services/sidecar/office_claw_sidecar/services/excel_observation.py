"""관측(Observe)을 계획 루프에 넣을지 정하는 모듈.

## 왜 모드가 세 개인가

"편집 의도면 첫 단계도 편집이어야 한다"는 규칙을 그냥 풀면 무엇이 좋아지는지 알 수
없다. 규칙을 푸는 것과 **읽은 값을 모델에게 돌려주는 것**은 다른 일이고, 지금 실행기는
뒤엣것을 하지 않기 때문이다. `execute_plan()`은 계획 전체를 한 번에 돌리고, 스텝 사이에
LLM이 없다. 그래서 계획이 `[read_range, highlight]`여도 highlight의 인자는 읽기 **전에**
이미 정해져 있다.

둘을 갈라서 재려고 모드를 셋으로 둔다.

| 모드 | 첫 단계 read | 읽은 값을 모델에 | 무엇을 재는가 |
|---|---|---|---|
| `off` | 금지 (현재) | 안 줌 | 기준선 |
| `read_first` | 허용 | 안 줌 | 규칙 해제 **단독** 효과 |
| `loop` | 허용 | 줌 | 관측→재계획 전체 효과 |

`read_first`가 `off`와 비슷하고 `loop`만 좋아지면, 문제는 금지 규칙이 아니라 읽은 것을
추론에 다시 넣지 않는 구조였다는 뜻이다. 셋이 다 비슷하면 다이제스트로 충분하다는
뜻이고, 그러면 관측 루프는 지연만 늘리는 복잡도다.

## 기본값이 off인 이유

`build_planner_prompt`는 SFT 데이터 생성과 **같은 함수**다. 프롬프트에 블록이 하나
늘면 이미 학습된 모델은 본 적 없는 형식을 받는다. `off`에서는 관측 블록이 빈 문자열이라
프롬프트가 바이트 단위로 예전과 같다. 실험 팔에서만 달라진다.
"""

from __future__ import annotations

import os
from enum import StrEnum
from typing import Any

ENV_VAR = "EXCEL_OBSERVATION_MODE"

# 워크북을 바꾸지 않고 상태만 알려주는 액션들. 편집 의도의 첫 단계로 나오면
# `off`에서는 재계획을 강제하고, 나머지 모드에서는 그대로 둔다.
OBSERVATION_ACTIONS = frozenset(
    {
        "excel_live.read_range",
        "excel_live.validate_data",
    }
)

# 한 턴에 관측을 몇 번까지 허용할지. 1이면 "읽고 → 다시 계획하고 → 편집"까지다.
# 늘리면 모델이 읽기만 반복하다 턴을 끝낼 수 있다.
MAX_OBSERVATIONS = 1

# 관측 결과를 프롬프트에 실을 때의 상한. 넘으면 잘라내고 잘랐다고 밝힌다.
# 조용히 자르면 모델은 그게 전부인 줄 알고 판단한다.
_MAX_ROWS = 80
_MAX_COLS = 12
_MAX_CELL_CHARS = 24


class ObservationMode(StrEnum):
    OFF = "off"
    READ_FIRST = "read_first"
    LOOP = "loop"


def current_mode() -> ObservationMode:
    raw = str(os.getenv(ENV_VAR, "") or "").strip().lower()
    try:
        return ObservationMode(raw)
    except ValueError:
        return ObservationMode.OFF


def _resolve(mode: ObservationMode | None) -> ObservationMode:
    return mode if mode is not None else current_mode()


def is_observation(action: str) -> bool:
    return str(action or "") in OBSERVATION_ACTIONS


def allows_read_first(mode: ObservationMode | None = None) -> bool:
    """편집 의도 요청의 첫 단계로 관측 액션을 허용하는가."""
    return _resolve(mode) is not ObservationMode.OFF


def feeds_result_back(mode: ObservationMode | None = None) -> bool:
    """관측 결과를 모델에게 돌려주는가."""
    return _resolve(mode) is ObservationMode.LOOP


def truncate_at_observation(steps: list[Any], mode: ObservationMode | None = None) -> list[Any]:
    """관측 단계에서 계획을 끊는다.

    `loop`에서만 끊는다. 끊지 않으면 뒤 단계가 **읽기 전에 정해진 인자로** 그대로
    실행돼 버려서, 재계획할 기회가 오기 전에 파일이 이미 바뀐다. 관측 이후는 모델이
    데이터를 보고 다시 정하게 둬야 한다.
    """
    if not feeds_result_back(mode) or not steps:
        return steps
    for index, step in enumerate(steps):
        if is_observation(_action_of(step)):
            return steps[: index + 1]
    return steps


def _action_of(step: Any) -> str:
    if isinstance(step, dict):
        return str(step.get("action", ""))
    return str(getattr(step, "action", "") or "")


def should_replan_after_observation(
    execution: Any,
    *,
    mode: ObservationMode | None = None,
    observed: int = 0,
    max_observations: int = MAX_OBSERVATIONS,
) -> bool:
    """관측만 하고 끝난 턴인가. 그렇다면 그 결과를 들고 다시 계획해야 한다.

    실패 재계획(`should_replan_after_execution`)과 트리거가 정반대다. 저쪽은 마지막
    단계가 **깨졌을 때** 돌고, 이쪽은 마지막 단계가 **성공한 관측일 때** 돈다. 그래서
    합치지 않고 따로 둔다 — 합치면 실패 재계획의 조건을 읽기 어려워진다.
    """
    if not feeds_result_back(mode):
        return False
    if observed >= max_observations:
        return False
    last = getattr(execution, "last", None)
    if last is None:
        return False
    if not is_observation(getattr(last, "action", "")):
        return False
    return not getattr(last, "error", None)


def render_observation(execution: Any, *, max_rows: int = _MAX_ROWS, max_cols: int = _MAX_COLS) -> str:
    """관측 결과를 프롬프트에 붙일 블록으로 만든다.

    다이제스트(머리글 + 예시 3행)와 달리 **실제로 읽은 값 전부**다. 빈칸이 어디
    있는지, 어떤 값이 문자열인지는 이걸 봐야만 알 수 있다. 그래서 타입 표시를
    같이 붙인다 — `"22"`와 `22`가 프롬프트에서 구분되지 않으면 읽어도 소용이 없다.
    """
    last = getattr(execution, "last", None)
    if last is None:
        return ""
    result = getattr(last, "result", None)
    if not isinstance(result, dict):
        return ""

    action = str(getattr(last, "action", ""))
    address = str(result.get("address", "") or "").strip()
    lines = [f"관측 결과 ({action}{f' {address}' if address else ''}):"]

    issues = result.get("issues")
    if issues is not None:
        lines.append(f"- 검사 결과: {issues}")
        lines.append("위 관측을 근거로 편집 계획을 세워라. 다시 읽지 마라.")
        return "\n".join(lines) + "\n"

    values = result.get("values")
    if not isinstance(values, list) or not values:
        return ""

    clipped_rows = values[:max_rows]
    row_clipped = len(values) - len(clipped_rows)
    col_clipped = 0
    for row_index, row in enumerate(clipped_rows, start=1):
        cells = row if isinstance(row, list) else [row]
        col_clipped = max(col_clipped, len(cells) - max_cols)
        rendered = " | ".join(_render_cell(cell) for cell in cells[:max_cols])
        lines.append(f"  {row_index:>3}: {rendered}")
    if col_clipped > 0:
        lines.append(f"  (열 {col_clipped}개 더 있음 — 잘라냈다)")
    if row_clipped > 0:
        lines.append(f"  (행 {row_clipped}개 더 있음 — 잘라냈다)")

    lines.append(
        "빈 칸은 (빈칸), 문자열은 따옴표로 표시했다. 위 값을 근거로 편집 계획을 세워라."
    )
    lines.append("이미 읽었으므로 read_range를 다시 넣지 마라.")
    return "\n".join(lines) + "\n"


def _render_cell(cell: Any) -> str:
    """값과 타입을 같이 보여 준다. 이 구분이 이 블록의 존재 이유다."""
    if cell is None or (isinstance(cell, str) and not cell.strip()):
        return "(빈칸)"
    if isinstance(cell, bool):
        return str(cell)
    if isinstance(cell, (int, float)):
        return str(cell)
    text = str(cell)
    if len(text) > _MAX_CELL_CHARS:
        text = text[: _MAX_CELL_CHARS - 1] + "…"
    return f'"{text}"'
