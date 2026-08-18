"""직전 턴을 고쳐 달라는 말 — "아니 부산으로 바꿔줘".

2026-08-17 실측. 멀티턴 두 턴이 데이터를 지웠다:

    [1] "매출 시트 A10에 서울 입력"   → A10 = '서울'
    [2] "아니 부산으로 바꿔줘"        → find_replace(find='부산', replace='')

플래너가 **찾을 말과 바꿀 말을 뒤바꿨다.** 바꿀 값 '부산'을 찾을 말로 쓰고 바꿀 말은
비웠다. 시트 어딘가에 원래 있던 '부산'(B4)이 지워졌고, 정작 고치려던 A10은 그대로였다.
그리고 `replaced_cells: 1`과 함께 성공으로 보고됐다.

원인은 문장 자체가 아니라 **문맥이 없다는 것**이다. "아니 X로 바꿔줘"는 시트 전체를
뒤지라는 말이 아니라 *방금 쓴 칸*을 고치라는 말이다. 직전 쓰기를 기억하면 규칙으로
풀린다 — 모델을 부를 필요가 없다.

일부러 좁게 잡았다:
  - 정정 표지(아니 / 말고 / 대신)가 **명시적으로** 있어야 한다. "부산으로 바꿔줘"만
    으로는 진짜 찾아 바꾸기일 수 있다.
  - 직전 쓰기가 **한 칸**이어야 한다. 여러 칸을 한 값으로 덮는 건 정정이 아니다.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any

# 슬롯과 같은 수명. 5분 지난 "아니"는 무엇을 가리키는지 알 수 없다.
_TTL_SECONDS = 300
_MAX_ENTRIES = 256


@dataclass(frozen=True)
class LastWrite:
    """직전에 사용자가 값을 써 넣은 한 칸."""

    sheet_name: str
    cell: str
    value: Any
    at_ts: float


_last_writes: dict[str, LastWrite] = {}


def _prune(now: float) -> None:
    stale = [key for key, item in _last_writes.items() if now - item.at_ts > _TTL_SECONDS]
    for key in stale:
        _last_writes.pop(key, None)
    if len(_last_writes) > _MAX_ENTRIES:
        oldest = sorted(_last_writes.items(), key=lambda kv: kv[1].at_ts)
        for key, _ in oldest[: len(_last_writes) - _MAX_ENTRIES]:
            _last_writes.pop(key, None)


_SINGLE_CELL = re.compile(r"^[A-Z]{1,3}\d{1,7}$")


def record_write(
    session_key: str, *, sheet_name: str, address: str, values: Any, now: float | None = None
) -> None:
    """방금 쓴 한 칸을 기억한다. 여러 칸이면 기억하지 않는다 — 정정 대상이 모호하다."""
    key = str(session_key or "").strip()
    if not key:
        return
    cell = str(address or "").strip().upper().replace("$", "")
    if not _SINGLE_CELL.fullmatch(cell):
        _last_writes.pop(key, None)
        return
    value = values
    while isinstance(value, list):
        if len(value) != 1:
            _last_writes.pop(key, None)
            return
        value = value[0]
    stamp = time.time() if now is None else now
    _prune(stamp)
    _last_writes[key] = LastWrite(
        sheet_name=str(sheet_name or ""), cell=cell, value=value, at_ts=stamp
    )


def recall_write(session_key: str, *, now: float | None = None) -> LastWrite | None:
    key = str(session_key or "").strip()
    if not key:
        return None
    item = _last_writes.get(key)
    if item is None:
        return None
    stamp = time.time() if now is None else now
    if stamp - item.at_ts > _TTL_SECONDS:
        _last_writes.pop(key, None)
        return None
    return item


@dataclass(frozen=True)
class LastFormula:
    """직전에 넣은 한 칸짜리 수식. "그 아래 칸에는 평균"의 문맥이다."""

    sheet_name: str
    cell: str
    formula: str
    at_ts: float


_last_formulas: dict[str, LastFormula] = {}


def record_formula(
    session_key: str, *, sheet_name: str, cell: str, formula: str, now: float | None = None
) -> None:
    key = str(session_key or "").strip()
    if not key:
        return
    target = str(cell or "").strip().upper().replace("$", "").split(":")[0]
    text = str(formula or "").strip()
    if not _SINGLE_CELL.fullmatch(target) or not text.startswith("="):
        _last_formulas.pop(key, None)
        return
    stamp = time.time() if now is None else now
    _last_formulas[key] = LastFormula(
        sheet_name=str(sheet_name or ""), cell=target, formula=text, at_ts=stamp
    )


def recall_formula(session_key: str, *, now: float | None = None) -> LastFormula | None:
    key = str(session_key or "").strip()
    item = _last_formulas.get(key)
    if item is None:
        return None
    stamp = time.time() if now is None else now
    if stamp - item.at_ts > _TTL_SECONDS:
        _last_formulas.pop(key, None)
        return None
    return item


# "그 아래 칸에는 평균 넣어줘" — 직전 수식과 같은 범위, 함수만 바꾼다.
_BELOW_MENTION = re.compile(r"(그\s*)?(아래|밑)(에|의)?\s*(칸|셀)")
_AGG_WORDS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"가장\s*(큰|높|많)|최댓값|최대값"), "MAX"),
    (re.compile(r"가장\s*(작은|낮|적)|최솟값|최소값"), "MIN"),
    (re.compile(r"평균"), "AVERAGE"),
    (re.compile(r"개수|건수"), "COUNTA"),
    (re.compile(r"합계|총합|합\s|더한"), "SUM"),
)
_SIMPLE_AGG_FORMULA = re.compile(r"^=\s*[A-Z]+\(([^()]+)\)$")


def build_below_formula_plan(message: str, last: LastFormula | None) -> list[dict] | None:
    """직전 턴 "F2에 합계" 다음의 "그 아래 칸에는 평균" → F3에 =AVERAGE(같은 범위).

    2026-08-17 배터리 실측: 이 문장이 플래너로 가서 =AVERAGE(A2:A1)이 A1:A8에
    적용돼 **날짜 열이 덮였다.** 직전 수식을 기억하면 규칙으로 풀린다 — 어느 열의
    평균인지 물을 필요도 없다. 방금 그 열이다.
    """
    if last is None:
        return None
    text = str(message or "")
    if not _BELOW_MENTION.search(text):
        return None
    # "B10 아래 칸에는 …" — 직전 수식(F2)이 아니라 다른 셀을 지목했다면 이
    # 문맥의 소관이 아니다. 밀어붙이면 F3에 써진다(2026-08-18 멀티턴 사냥).
    cited = {c.upper() for c in re.findall(r"\b[A-Za-z]{1,3}\d{1,7}\b", text)}
    if cited and last.cell not in cited:
        return None
    func = next((f for pattern, f in _AGG_WORDS if pattern.search(text)), "")
    if not func:
        return None
    args_match = _SIMPLE_AGG_FORMULA.match(last.formula)
    if args_match is None:
        # 인자를 못 읽는 복합 수식은 흉내 내지 않는다.
        return None
    col_row = re.fullmatch(r"([A-Z]{1,3})(\d{1,7})", last.cell)
    if col_row is None:
        return None
    target = f"{col_row.group(1)}{int(col_row.group(2)) + 1}"
    params: dict = {"range_ref": target, "formula_a1": f"={func}({args_match.group(1)})"}
    if last.sheet_name:
        params["sheet_name"] = last.sheet_name
    return [
        {
            "action": "excel_live.set_formula",
            "params": params,
            "reason": f"직전 수식({last.cell}) 아래 칸에 {func}",
        }
    ]


def forget(session_key: str) -> None:
    _last_writes.pop(str(session_key or "").strip(), None)
    _last_formulas.pop(str(session_key or "").strip(), None)


def reset_for_tests() -> None:
    _last_writes.clear()
    _last_formulas.clear()


# "아니" 하나로는 부족하다 — "아니 그거 지워줘"는 정정이 아니라 새 명령이다.
_CORRECTION_MARKER = re.compile(r"(^|\s)(아니|아니라|아냐|아니야|말고|대신)(\s|$|,)")
# 취소·되돌리기는 정정이 아니다. 여기로 새면 되돌릴 것을 덮어쓴다.
_NOT_A_VALUE_EDIT = re.compile(r"(취소|되돌|undo|지워|삭제|없애|빼\s*줘|비워)", re.IGNORECASE)
# "부산으로" — 바꿀 값은 '(으)로' 앞에 온다. 옮겨/이동은 셀 이동 정정이다.
_VALUE_BEFORE_RO = re.compile(r"([^\s,]{1,40}?)(?:으로|로)(?=\s|$|\s*(?:바꿔|변경|고쳐|해|수정|옮겨|이동))")
_TRAILING_VERB = re.compile(r"(바꿔|바꾸|변경|고쳐|고치|수정|해|줘|주세요|해줘)\s*$")
# "부산이라니까" — 강조 어미는 값이 아니다(2026-08-18 멀티턴 사냥:
# '부산이라니까'가 통째로 셀 값이 됐다).
_EMPHASIS_ENDING = re.compile(r"(?:이?라니까+|이?라니깐|이?라고요?|이?잖아)$")
# 색·서식 낱말은 값 정정이 아니라 서식 요청이다 — "아니 빨간색으로 바꿔줘"가
# 셀 값 '빨간색'이 됐다(같은 사냥). 여기서 물러나면 서식 규칙이 받는다.
_FORMAT_WORD = re.compile(r"(색$|색깔$|굵게$|기울임$|밑줄$|테두리$|경계선$)")


def parse_correction(message: str) -> str:
    """ "아니 부산으로 바꿔줘" → "부산". 정정이 아니면 빈 문자열."""
    text = str(message or "").strip()
    if not text or not _CORRECTION_MARKER.search(text):
        return ""
    if _NOT_A_VALUE_EDIT.search(text):
        return ""

    # "서울 말고 부산으로" — 표지 뒤쪽만 본다. 표지 앞은 고칠 대상(옛 값)이다.
    marker = None
    for hit in _CORRECTION_MARKER.finditer(text):
        marker = hit
    tail = text[marker.end() :].strip() if marker else text

    hit = _VALUE_BEFORE_RO.search(tail)
    if hit:
        value = hit.group(1).strip()
    else:
        # "아니 부산" — 표지 뒤에 값만 남은 경우.
        value = _TRAILING_VERB.sub("", tail).strip().rstrip(",.")
        if " " in value:
            # 여러 낱말이면 무엇이 값인지 확신할 수 없다.
            return ""
    value = value.strip().strip("'\"")
    value = _EMPHASIS_ENDING.sub("", value)
    if not value or len(value) > 40:
        return ""
    # 지시대명사는 값이 아니다.
    if value in {"그거", "이거", "저거", "그것", "이것", "그", "이", "저", "여기", "거기"}:
        return ""
    if _FORMAT_WORD.search(value):
        return ""
    return value


def build_correction_plan(message: str, last: LastWrite | None) -> list[dict[str, Any]] | None:
    """정정 문장 + 직전 한 칸 쓰기 → 그 칸을 다시 쓰는 계획."""
    if last is None:
        return None
    value = parse_correction(message)
    if not value:
        return None
    if _SINGLE_CELL.fullmatch(value.upper()):
        # "아니 B2로 옮겨줘" — 값이 아니라 **자리**를 고치는 정정이다.
        # 값으로 쓰면 A10에 문자 'B2'가 들어간다(2026-08-18 멀티턴 사냥 실측).
        new_cell = value.upper()
        move_params: dict[str, Any] = {"start_cell": new_cell, "values_2d": [[last.value]]}
        clear_params: dict[str, Any] = {"target_range": last.cell}
        if last.sheet_name:
            move_params["sheet_name"] = last.sheet_name
            clear_params["sheet_name"] = last.sheet_name
        return [
            {
                "action": "excel_live.write_range",
                "params": move_params,
                "reason": f"직전 값('{last.value}')을 {new_cell}로 이동",
            },
            {
                "action": "excel_live.clear_range",
                "params": clear_params,
                "reason": f"원래 자리 {last.cell} 비우기",
            },
        ]
    if str(last.value or "").strip() == value:
        # 이미 그 값이다. 다시 써 봐야 달라지는 게 없다.
        return None
    params: dict[str, Any] = {"start_cell": last.cell, "values_2d": [[value]]}
    if last.sheet_name:
        params["sheet_name"] = last.sheet_name
    return [
        {
            "action": "excel_live.write_range",
            "params": params,
            "reason": f"직전에 쓴 {last.cell}을(를) '{value}'로 정정",
        }
    ]


# 바꿀 말이 비면 찾은 글자를 지운다. 진짜 지우려는 요청일 때만 허용한다.
_DELETION_INTENT = re.compile(r"(지워|삭제|없애|제거|빼|비워|없앰|clear|remove|delete)", re.IGNORECASE)


def find_replace_erases_data(params: dict[str, Any], message: str) -> bool:
    """바꿀 말이 비어 있는데 지우라는 말이 없으면, 그건 계획이 잘못된 것이다.

    2026-08-17: "아니 부산으로 바꿔줘"가 `replace_text=""`로 와서 시트의 '부산'을
    지웠다. 빈 치환은 정당한 요청("괄호 지워줘")도 있으므로 문장을 함께 본다.
    """
    if not isinstance(params, dict):
        return False
    if str(params.get("find_text") or "").strip() == "":
        return False
    if str(params.get("replace_text") or "").strip() != "":
        return False
    return not _DELETION_INTENT.search(str(message or ""))
