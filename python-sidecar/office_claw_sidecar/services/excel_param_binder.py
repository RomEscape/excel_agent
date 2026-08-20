"""
파라미터 바인더 — 플래너가 낸 상징적 파라미터를 실제 워크북 좌표로 확정하는 레이어.

플래너(LLM·룰 어느 쪽이든)는 "금액 열 기준", "월을 행으로" 같은 말을 그대로 흘리거나
key_column=1 처럼 근거 없는 인덱스를 채워 넣는다. 그 값이 실행기까지 그냥 내려가면
엉뚱한 열이 정렬되고도 성공으로 보고된다.

이 모듈은 실행 직전에 다이제스트(실제 시트/머리글/값 후보)와 원문을 대조해
- 존재하지 않는 열 이름 → 원문이 가리키는 실제 머리글로 교정
- 숫자 인덱스 → 머리글 이름으로 교정
- output_sheet="피벗1!A1" 같은 오염된 값 → 시트명/시작셀로 분리
- 필터 값 누락 → 해당 열의 실제 값 후보에서 확정
를 수행하고, 끝내 못 정하는 슬롯은 unresolved로 보고해 되묻게 한다.

상태를 갖지 않는 순수 함수 모듈이며, 라우터는 bind_plan_steps / resolve_sheet_from_message만 쓴다.
"""

from __future__ import annotations

import re
from typing import Any

from .excel_correction_context import find_replace_erases_data
from .excel_formula_builder import build_formula, parse_named_formula
from .excel_header_lexicon import find_header_mentions, resolve_header
from .excel_live_executor import PlanStep
from .korean_number import parse_condition

# 액션별로 "열을 가리키는" 파라미터. 값은 단일 슬롯 이름.
_COLUMN_SLOTS: dict[str, tuple[str, ...]] = {
    "excel_live.sort_range": ("key_column",),
    "excel_live.filter_rows": ("column",),
    "excel_live.pivot_table": ("row_field", "column_field", "value_field"),
    "excel_live.sort_rows": ("column",),
    "excel_live.drop_column": ("column",),
    "excel_live.rename_column": ("column",),
    "excel_live.group_by_aggregate": ("group_column", "value_column"),
    "excel_live.calculate_column_stat": ("column",),
}
# 열 목록을 받는 파라미터.
_COLUMN_LIST_SLOTS: dict[str, tuple[str, ...]] = {
    "excel_live.dedupe_rows": ("key_columns",),
    "excel_live.find_duplicates": ("key_columns",),
}
# 기준 열을 못 정해도 전체 열로 점검하면 되는 액션. 되묻지 않는다.
_OPTIONAL_COLUMN_LIST_ACTIONS = {"excel_live.find_duplicates"}
# 없으면 실행기·검증기가 임의 기본값(1번 열)으로 채워버리는 필수 열 슬롯.
_REQUIRED_COLUMN_SLOTS: dict[str, tuple[str, ...]] = {
    "excel_live.sort_range": ("key_column",),
    "excel_live.filter_rows": ("column",),
}
# 기준 열을 잘못 잡으면 데이터가 조용히 뒤섞이는 슬롯.
# 원문이 기준을 말하지 않았다면 플래너가 채운 값이 그럴듯해도 믿지 않는다.
_REQUIRE_EXPLICIT_COLUMN: dict[str, tuple[str, ...]] = {
    "excel_live.sort_range": ("key_column",),
    # 플래너는 sort_rows로 내기도 한다. 목록에 없으면 "정렬 좀"에 학습셋 열
    # 이름('이름')을 지어내 실행까지 간다(2026-08-18 대화형 러너 실측).
    "excel_live.sort_rows": ("column",),
    "excel_live.dedupe_rows": ("key_columns",),
}
# 결과를 새 시트에 쓰는 액션. output_sheet 오염을 정리한다.
_OUTPUT_SHEET_ACTIONS = {
    "excel_live.pivot_table",
    "excel_live.forecast_linear",
    "excel_live.compare_ranges",
    "excel_live.consolidate_sheets",
    "excel_live.consolidate_workbooks_from_folder",
    "excel_live.create_chart",
}

_SHEET_MENTION_PATTERN = re.compile(r"([A-Za-z0-9가-힣_]{1,20})\s*(?:시트|sheet)", re.IGNORECASE)
_REFERENCE_CONTEXT = re.compile(
    r"(참조표|참조|참고표|조회표는|기준표|룩업|lookup|reference)\s*(?:는|은|을|를|:)?\s*$"
)
#: 시트 이름 뒤에 붙는 **출처 표시**. 한국어는 어순이 아니라 조사로 원본을 가리킨다.
#: `성적부에서` · `성적부의` · `성적부에 있는` · `성적부 기준` → 그 시트는 **원본**이다.
_SOURCE_MARKER = re.compile(
    r"^\s*(?:시트|sheet|탭)?\s*"
    r"(?:에서|의\s|의$|에\s*있는|에\s*든|에\s*나온|기준|것|거|쪽)"
)
_COLUMN_LETTER_MENTION = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]\s*열")
_CELL_REF_PATTERN = re.compile(r"^[A-Z]{1,3}\d{1,7}$")
_COLUMN_LETTER_ONLY = re.compile(r"^[A-Za-z]{1,3}$")
_JOSA = ("으로", "로서", "에서", "들을", "를", "을", "은", "는", "이", "가", "의", "로", "별")

# "새 시트로 만들어줘"의 '새'처럼 이름이 아니라 지시어인 낱말. 이걸 시트 이름으로
# 오인하면 멀쩡한 요청이 "그런 시트 없다"고 막힌다.
_GENERIC_SHEET_WORDS = frozenset(
    {
        "새",
        "이",
        "그",
        "저",
        "요",
        "다른",
        "현재",
        "활성",
        "각",
        "모든",
        "전체",
        "해당",
        "빈",
        "임시",
        "결과",
        "출력",
        "원본",
        "위",
        "아래",
        # "밑에 있는 시트에 기록해줘"의 '있는'이 시트 이름으로 오인돼
        # "'있는' 시트를 찾을 수 없습니다"라고 되물었다(2026-08-18 실측).
        # 위치·존재를 나타내는 수식어는 이름이 아니다 — 조사 뗀 어간형("있")도.
        "있는",
        "있",
        "없는",
        "없",
        "밑",
        "옆",
        "다음",
        "마지막",
        "새로운",
        "new",
        "this",
        "that",
        "other",
        "current",
        "active",
        "all",
        "each",
        "the",
        "a",
        "an",
        "blank",
        "empty",
        "result",
        "output",
    }
)


def _strip_josa(text: str) -> str:
    value = str(text or "").strip()
    for josa in _JOSA:
        if len(value) > len(josa) and value.endswith(josa):
            return value[: -len(josa)]
    return value


def _josa_variants(text: str) -> list[str]:
    """원문형과 조사를 뗀 형태를 **원문형 먼저** 돌려준다.

    2026-08-17 실측: 시트명이 "추이"였는데 `_strip_josa`가 끝 글자 "이"를 주격
    조사로 보고 떼어 "추"를 만들었다. 그 시트를 찾을 수 없다며 되물었고,
    **에러 메시지에는 '현재 시트: … 추이 …'가 그대로 찍혀 있었다.**
    데이터 8턴 + 차트 2턴이 통째로 날아갔다.

    조사인지 이름의 일부인지는 이 함수만으로 알 수 없다. 그러니 정하지 말고
    둘 다 주고, 실제 시트 목록과 대조하는 쪽이 고르게 한다. 원문형이 앞이다 —
    사용자가 부른 그대로가 우선이어야 한다.
    """
    value = str(text or "").strip()
    stripped = _strip_josa(value)
    return [value] if stripped == value else [value, stripped]


def sheet_entry(digest: dict[str, Any], sheet_name: str | None) -> dict[str, Any]:
    sheets = digest.get("sheets") or []
    target = str(sheet_name or digest.get("active_sheet") or "").strip()
    for sheet in sheets:
        if str(sheet.get("name") or "") == target:
            return sheet
    return sheets[0] if sheets else {}


def sheet_names(digest: dict[str, Any]) -> list[str]:
    return [str(sheet.get("name") or "") for sheet in (digest.get("sheets") or [])]


def _headers(entry: dict[str, Any]) -> list[str]:
    return [str(col.get("header") or "") for col in (entry.get("columns") or []) if col.get("header")]


def _column_meta(entry: dict[str, Any], header: str) -> dict[str, Any]:
    for col in entry.get("columns") or []:
        if str(col.get("header") or "").strip() == str(header or "").strip():
            return col
    return {}


_SINGLE_CELL_WRITE_MESSAGE = re.compile(
    r"^(.*?(?<![A-Za-z0-9])[A-Za-z]{1,3}\d{1,7})\s*(?:셀|칸)?\s*에(?:다가?|는)?\s+\S.*?(?:입력|기록|넣어|채워|써|적어)\s*(?:해)?\s*(?:줘요|줘|주세요|주라|줄래|놔|둬|봐|라)?\s*[~.!?…]*$",
    re.DOTALL,
)
_VALUE_GRID_MESSAGE = re.compile(
    r"(?s)^(?=(?:.*[,;\t\n]){3,}).*(?:입력|기록|넣어|채워|써|적어)\s*(?:해)?\s*(?:줘요|줘|주세요|주라|줄래|놔|둬|봐)?\s*[~.!?…]*$"
)


def resolve_sheet_from_message(
    message: str,
    digest: dict[str, Any],
    *,
    default: str | None,
) -> str | None:
    """원문이 "매출 시트"처럼 명시한 시트가 실제로 있으면 그 시트를 작업 대상으로 삼는다.

    여러 시트가 언급되면 첫 번째(=보통 원본)를 고른다. 결과를 쓰는 시트는 output_sheet가 따로 받는다.
    "참조표는 조회표 시트 A:B"처럼 참조 대상으로 불린 시트는 작업 대상이 아니므로 건너뛴다 —
    그렇지 않으면 VLOOKUP 결과가 원본이 아닌 참조표 시트에 써진다.
    """
    text = str(message or "")
    names = {name for name in sheet_names(digest) if name}
    if not names:
        return default
    # 값 격자(붙여넣기)에서는 값 안의 낱말이 시트 이름과 같아도 지목이 아니다 — "재고 관리, 수요 예측 기반 …" 행이
    # 든 표가 **재고 관리 시트**에 써졌다(2026-08-19 ex12 실측: 대시보드에 표가 없어 합계 줄이 빈 계획). 격자는
    # 첫 쉼표 앞 머리말("대시보드 시트에 여기에")만 지목으로 본다.
    # 한 칸 쓰기("A1에 재고 관리 현황 써줘")도 같다 — 셀 좌표 뒤는 값이다. 값 속 낱말이 실재 시트 이름과
    # 같다고 다른 시트에 쓰면 조용한 오실행이다(2026-08-19 ex12 실측과 같은 부류).
    single_write = _SINGLE_CELL_WRITE_MESSAGE.match(text)
    if single_write:
        lead = single_write.group(1)
        found = (
            next(
                (name for m in _SHEET_MENTION_PATTERN.finditer(lead) for name in _josa_variants(m.group(1)) if name in names),
                "",
            )
            or _sheet_named_verbatim(lead, names)
            or _sheet_called_by_its_korean_name(lead, names)
        )
        # "성적부 **기준으로** A2에"·"성적부**에 있는** 표를 A2부터" — 머리말의 시트가
        # 출처로 불렸으면 실행 시트를 옮기지 않는다(2026-08-20 자체 검토:
        # 이 경로는 출처 판정을 안 거쳐 성적부로 실행이 옮겨졌다).
        if found and _sheet_named_as_source(lead, {found}):
            return default
        return found or default
    if _VALUE_GRID_MESSAGE.search(text):
        lead = re.split(r"[,;\t\n]", text, maxsplit=1)[0]
        lead_names = {n for n in names if n}
        for match in _SHEET_MENTION_PATTERN.finditer(lead):
            candidate = next((name for name in _josa_variants(match.group(1)) if name in lead_names), "")
            if candidate:
                return candidate
        return _sheet_named_verbatim(lead, lead_names) or _sheet_called_by_its_korean_name(lead, lead_names) or default
    # "성적부 시트 결석 열 합계를 **요약 A2에** 부탁해요" — 시트 이름이 **대상 셀에 바로 붙어**
    # 있으면 그게 대상 시트다. 이 문장은 시트를 둘 부르는데(성적부=원본, 요약=대상),
    # 앞의 것을 고르면 성적부!A2의 학생 이름 위에 수식이 써진다(2026-08-20 파괴 게이트 실측).
    for name in sorted(names, key=len, reverse=True):
        if re.search(
            rf"(?<![A-Za-z0-9_]){re.escape(name)}\s*(?:시트|sheet|탭)?\s*"
            rf"[A-Za-z]{{1,3}}\d{{1,7}}\s*(?:셀|칸)?\s*에",
            text,
            re.IGNORECASE,
        ):
            return name
    # "B10에다 성적부 시트 결석 다 더한 값 가져와줘" — **대상 셀이 시트 언급보다 앞**이면 그 시트는 원본이고
    # 결과는 지금 보고 있는 시트에 써야 한다. 여기서 원본으로 작업 시트를 옮기면 수식이 원본 시트에 써지고,
    # 실측에서는 성적부!B10의 학생 이름을 덮었다(2026-08-19 결과 워크북 감사).
    dest_first = re.search(r"(?<![A-Za-z0-9])[A-Za-z]{1,3}\d{1,7}\s*(?:셀|칸)?\s*에(?:다가?|는)?(?![A-Za-z0-9])", text)
    if dest_first:
        first_mention = _SHEET_MENTION_PATTERN.search(text)
        if first_mention and first_mention.start() > dest_first.start():
            return default
        # 어순만 보면 위 세 문장을 놓친다 — 시트가 앞에 오기 때문이다.
        # 한국어는 **조사**로 출처를 표시한다: `성적부에서`·`성적부의`·`성적부에 있는`.
        # 대상 셀이 따로 있는데 시트가 출처로 불렸으면 실행 시트를 옮기지 않는다
        # (2026-08-20 파괴 게이트: 성적부!A2의 '김민준' 위에 `=SUM(C2:C5)`가 써졌다).
        if _sheet_named_as_source(text, names):
            return default
    for match in _SHEET_MENTION_PATTERN.finditer(text):
        # 원문형("추이")이 실제 시트면 그걸 쓴다. 조사를 뗀 형태("추")를 먼저 보면
        # 멀쩡한 이름이 잘려 나간다.
        candidate = next((name for name in _josa_variants(match.group(1)) if name in names), "")
        if not candidate:
            continue
        if _REFERENCE_CONTEXT.search(text[max(0, match.start() - 12) : match.start()]):
            continue
        return candidate
    return _sheet_named_verbatim(text, names) or _sheet_called_by_its_korean_name(text, names) or default


# 시트 이름 앞에 붙을 수 없는 낱말 — 조사·접속어·지시어·동사 연결형으로 끝나면 이름의 일부가 아니다.
# 끝 글자만으로 조사를 가리면 명사를 자른다(재고·평가·제도·결과·순서) — 명사 끝에 드문 조사만 쓰고,
# 동사 연결형("넣고", "만들어서")은 어간 목록으로 잡는다.
_SHEET_NAME_STOP_WORD = re.compile(
    r"(?:에|에서|에는|에도|에다|은|는|을|를|으로|랑|이랑|하고|부터|까지|니까)$"
    # '로'는 조사이기도 명사 끝이기도 하다(자료·도로·경로·진로·통로) — 명사는 빼고 조사로 본다.
    r"|(?<!자|도|경|진|통|항|선|가|세|별|대|바)로$"
    # 관형형("대시보드로 쓸 요약 시트", "저장할 결과 시트")은 이름이 아니다.
    r"|(?:쓸|할|넣을|만들|볼|될|담을|옮길|정리할|쓰는|하는|넣는|되는|있는|없는|같은|위한|통한|대한|관한|따른|새로운)$"
    r"|(?:넣|하|쓰|적|만들|채우|끝내|정리하|저장하|입력하|붙이|붙여넣|그리|지우|삭제하|바꾸|고치|보|주|두|놓|시키|나누|합치)고$"
    r"|(?:해|써|넣어|만들어|채워|적어|붙여|그려|지워|바꿔|고쳐|나눠|합쳐)서$"
    r"|(?:하|넣으|쓰|만들|채우|끝내|되)면$"
    # 청유·의지형("시작하자", "해 보자", "할게")도 앞 절이다.
    r"|(?:하자|보자|합시다|할게|할까|해볼게|해보자|해볼까|가자|갑시다)$"
)
_SHEET_NAME_STOP_SET = frozenset(
    {
        "시트",
        "sheet",
        "탭",
        "아니",
        "아니면",
        "아님",
        "아뇨",
        "아니요",
        "아니라",
        "말고",
        "대신",
        "그게",
        "아까",
        "방금",
        "거기",
        "여기",
        "여기다",
        "여기에",
        "여기다가",
        "거기다",
        "저기",
        "저기다",
        "이름",
        "이름의",
        "이름으로",
        "이름은",
        "명으로",
        "표",
        "테이블",
        "값",
        "내용",
        "그리고",
        "그럼",
        "이제",
        "다음으로",
        "이번엔",
        "이번에는",
        "또",
        "또한",
        "한번",
        "하나",
        "새",
        "새로",
        "새로운",
        "빈",
        "다른",
        "이",
        "그",
        "저",
        "요",
        "아",
        "음",
        "어",
        "그냥",
        "일단",
        "먼저",
        "우선",
        "ㅇㅇ",
        "ㅇㅋ",
        "ok",
        "자",
        "네",
        "응",
        "좀",
        "혹시",
        "이제는",
        "그다음",
        "그담에",
        "그리구",
        "근데",
        "그런데",
    }
)


def extend_sheet_name_leftward(text: str, name_start: int, name: str, *, max_words: int = 3) -> str:
    """ "재고 관리 시트" — 한 낱말 패턴이 잡은 '관리' 앞으로 이름 낱말을 붙인다.

    앞 낱말이 조사·접속어·지시어로 끝나면 거기서 멈춘다. 최대 세 낱말.
    """
    head = str(text or "")[:name_start]
    words = head.split()
    picked = [name]
    while words and len(picked) < max_words:
        raw_prev = words[-1]
        # 쉼표·마침표·콜론으로 끝난 낱말은 앞 절이다("시작하자, 체크리스트 시트").
        if re.search(r"[,.:;!?~]$", raw_prev):
            break
        prev = raw_prev.strip("\"'“”‘’")
        if (
            not prev
            or prev.lower() in _SHEET_NAME_STOP_SET
            or _SHEET_NAME_STOP_WORD.search(prev)
            or re.fullmatch(r"[A-Za-z]{1,3}\d{1,7}(?::[A-Za-z]{1,3}\d{1,7})?", prev)
            or not re.fullmatch(r"[A-Za-z0-9_가-힣.&-]+", prev)
        ):
            break
        picked.insert(0, prev)
        words.pop()
    return " ".join(picked)


def sheet_mention_matches_known(message: str, token: str, known: list[str]) -> bool:
    """'<token> 시트' 지목 자리에서 실재 시트 이름(띄어쓰기 무시)이 끝나면 True.

    "재고 관리 시트"의 token='관리'는 실재 "재고 관리"(또는 "재고관리")로 풀린다.
    """
    text = str(message or "")
    squash = lambda v: re.sub(r"\s+", "", str(v or "")).casefold()  # noqa: E731
    names = [squash(n) for n in known if str(n or "").strip()]
    if not names or not token:
        return False
    for m in re.finditer(r"(?:시트|sheet)", text, re.IGNORECASE):
        prefix = text[max(0, m.start() - 40) : m.start()]
        if not squash(prefix).endswith(squash(token)):
            continue
        sp = squash(prefix)
        if any(sp.endswith(n) for n in names):
            return True
    return False


def explicit_sheet_mentions(message: str) -> list[str]:
    """원문이 "<이름> 시트"로 **콕 집어** 부른 이름들을 등장 순서로 돌려준다.

    resolve_sheet_from_message는 통합문서에 **있는** 시트만 고른다. 그래서 아직 없는
    시트를 지목하면(예: "Dashboard 시트 A4에 총 매출 입력해줘") 지목이 통째로 버려지고
    활성 시트로 폴백해, 사용자가 말한 적 없는 시트를 덮어쓴다. 그 지목을 잃지 않으려고
    이름만 따로 뽑아 둔다 — 존재 여부 판정은 호출부가 한다.

    지시어('새', '이', 'new' 등)는 이름이 아니므로 뺀다.
    """
    found: list[str] = []
    for group in explicit_sheet_mention_variants(message):
        if group and group[0] not in found:
            found.append(group[0])
    return found


def explicit_sheet_mention_variants(message: str) -> list[list[str]]:
    """지목 하나당 **후보 묶음**을 준다 — 원문형이 앞, 조사 뗀 형태가 뒤.

    묶음으로 주는 이유(2026-08-17 실측): 후보를 평평하게 펴서 돌려줬더니,
    "언급했는데 없는 시트"를 찾는 가드가 변형형 "추"를 없는 시트로 오인해
    "'추' 시트를 찾을 수 없습니다"라고 되물었다. 원래 이름 "추이"는 멀쩡히
    있는데도 그랬다. **한 지목의 후보 중 하나라도 실재하면 그 지목은 해결된 것**
    이므로, 판정하는 쪽이 묶음 단위로 볼 수 있어야 한다.
    """
    groups: list[list[str]] = []
    for match in _SHEET_MENTION_PATTERN.finditer(str(message or "")):
        group = [
            candidate
            for candidate in (c.strip().strip("\"'") for c in _josa_variants(match.group(1)))
            if candidate and candidate.lower() not in _GENERIC_SHEET_WORDS
        ]
        if group:
            groups.append(group)
    return groups


def _sheet_named_as_source(text: str, names: set[str]) -> bool:
    """문장이 어떤 시트를 **출처로** 불렀는가.

    `성적부에서 …`·`성적부의 …`·`성적부에 있는 …`처럼 조사가 출처를 표시하면 참.
    `성적부 시트에 써줘`처럼 대상으로 부른 경우는 거짓이다.
    """
    for name in sorted(names, key=len, reverse=True):
        for match in re.finditer(rf"(?<![A-Za-z0-9_]){re.escape(name)}", text, re.IGNORECASE):
            tail = text[match.end() : match.end() + 24]
            marker = _SOURCE_MARKER.match(tail)
            if marker is None:
                continue
            # 마커 바로 뒤가 셀 참조면 출처가 아니라 **그 시트 안의 대상**이다 —
            # "성적부의 A2에 제목 써줘"는 성적부!A2에 쓰라는 말이다(2026-08-20 자체 검토).
            if re.match(r"\s*[A-Za-z]{1,3}\d{1,7}(?![A-Za-z0-9])", tail[marker.end() :]):
                continue
            return True
    return False


def _sheet_named_verbatim(text: str, names: set[str]) -> str | None:
    """'Inventory를 표로'처럼 시트 이름이 조사만 붙고 '시트' 없이 나온 경우.

    한 낱말 이름(Inventory)은 한국어 별칭 규칙이 고의로 건너뛴다. 그 상태로
    두면 활성 시트(대개 첫 시트 Dashboard)에 표가 생긴다.
    """
    if not text or not names:
        return None
    particles = r"(?:을|를|이|가|은|는|의|에|에서|으로|로|과|와|도|만)?"
    for name in sorted(names, key=len, reverse=True):
        pattern = rf"(?<![A-Za-z0-9_]){re.escape(name)}{particles}(?![A-Za-z0-9_])"
        match = re.search(pattern, text, re.IGNORECASE)
        if not match:
            continue
        if _REFERENCE_CONTEXT.search(text[max(0, match.start() - 12) : match.start()]):
            continue
        return name
    return None


# 영문 시트명을 한국어로 부를 때 쓰는 말. 시트 이름은 거의 이 낱말들의 조합이다.
_SHEET_NAME_ALIASES: dict[str, tuple[str, ...]] = {
    "project": ("프로젝트", "과제"),
    "plan": ("계획", "일정"),
    "sales": ("매출", "판매", "영업"),
    "data": ("데이터", "자료"),
    "inventory": ("재고",),
    "stock": ("재고",),
    "lookup": ("조회", "참조", "코드"),
    "summary": ("요약", "집계"),
    "report": ("보고", "리포트"),
    "dashboard": ("대시보드", "현황"),
    "customer": ("고객", "거래처"),
    "order": ("주문", "발주"),
    "product": ("제품", "품목", "상품"),
    "budget": ("예산",),
    "cost": ("원가", "비용"),
    "chart": ("차트", "그래프"),
}
_SHEET_NAME_TOKENS = re.compile(r"[A-Za-z가-힣0-9]+")


def _sheet_called_by_its_korean_name(text: str, names: set[str]) -> str | None:
    """ "프로젝트 계획에서 ..." — '시트'라는 낱말 없이 한국어로 시트를 부른 경우.

    이 말을 놓치면 활성 시트(대개 Sales_Data)에 그대로 작업이 떨어진다. Project_Plan을
    걸러야 할 필터가 매출 데이터를 지우는 식이라, 실행은 성공하고 결과만 틀린다.

    오인식이 더 위험하므로 시트명을 이루는 모든 낱말이 원문에 있을 때만 인정한다.
    낱말이 하나뿐인 이름(Inventory)은 "<이름> 시트" 규칙이 이미 처리한다.
    """
    lowered = str(text or "").lower()
    for name in sorted(names, key=len, reverse=True):
        tokens = [t.lower() for t in _SHEET_NAME_TOKENS.findall(name) if len(t) > 1]
        if len(tokens) < 2:
            continue
        if all(
            token in lowered or any(alias in text for alias in _SHEET_NAME_ALIASES.get(token, ()))
            for token in tokens
        ):
            return name
    return None


def header_mentions(message: str, entry: dict[str, Any], digest: dict[str, Any]) -> list[dict[str, Any]]:
    """원문의 머리글 언급을 위치까지 포함해 돌려준다(시트명으로 쓰인 언급은 제외).

    "매출이익 나누기 매출"처럼 한국어 개념어로만 부른 경우도 사전을 통해 실제 머리글에 잇는다.
    """
    text = str(message or "")
    names = set(sheet_names(digest))
    rows: list[dict[str, Any]] = []
    for hit in find_header_mentions(text, _headers(entry)):
        tail = text[hit["end"] : hit["end"] + 6]
        if hit["header"] in names and re.match(r"\s*(?:시트|sheet)", tail, re.IGNORECASE):
            continue
        rows.append(hit)
    return rows


def mentioned_headers(message: str, entry: dict[str, Any], digest: dict[str, Any]) -> list[str]:
    """원문에 등장한 실제 머리글을 등장 순서대로 돌려준다."""
    return [hit["header"] for hit in header_mentions(message, entry, digest)]


def _resolve_column_value(
    value: Any,
    *,
    entry: dict[str, Any],
    candidates: list[str],
    used: set[str],
) -> tuple[Any, str]:
    """열 슬롯 하나를 실제 머리글로 확정한다. (확정값, 사유)를 돌려준다."""
    headers = _headers(entry)
    if isinstance(value, str):
        text = value.strip()
        if text and text in headers:
            return text, "kept"
        if text and _COLUMN_LETTER_ONLY.match(text):
            return text.upper(), "kept_letter"
        if text:
            # 플래너는 영문 시트에도 "매출"이라고 쓴다. 개념 사전으로 실제 머리글에 잇는다.
            mapped = resolve_header(text, headers)
            if mapped:
                return mapped, "lexicon"
    for candidate in candidates:
        if candidate not in used:
            return candidate, "from_message"
    return value, "unresolved"


def _pick_role_column(message: str, headers: list[str], markers: tuple[str, ...]) -> str | None:
    """ "월을 행으로", "채널을 행으로" 처럼 역할 단서 바로 앞에 오는 머리글을 고른다."""
    text = str(message or "")
    best: tuple[int, str] | None = None
    for hit in find_header_mentions(text, headers):
        tail = text[hit["end"] : hit["end"] + 8]
        for marker in markers:
            if re.match(rf"\s*(?:을|를|은|는|이|가)?\s*{re.escape(marker)}", tail):
                if best is None or hit["start"] < best[0]:
                    best = (hit["start"], hit["header"])
                break
    return best[1] if best else None


# "매출 데이터를 날짜순으로" — 앞의 '매출'은 대상을 부르는 말이지 정렬 기준이 아니다.
_DATASET_LABELS = ("데이터", "자료", "표", "목록", "리스트", "시트", "파일", "내역")
_SORT_ORDER_MARKER = re.compile(
    r"오름차순|내림차순|정렬|순으로|순서대로|큰\s*순|작은\s*순|최신순|과거순|순위"
)

# "A1:L37" 처럼 원문이 직접 말한 범위. 이게 없으면 플래너가 지어낸 범위를 믿으면 안 된다.
_EXPLICIT_RANGE_MENTION = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]{1,3}\d{1,7}\s*:\s*[A-Za-z]{1,3}\d{1,7}")

# 표 전체를 다루는 작업들 — 범위를 잘못 좁히면 일부 행만 집계·정렬돼 조용히 틀린 결과가 된다.
_WHOLE_TABLE_RANGE_SLOTS = {
    "excel_live.pivot_table": "source_range",
    "excel_live.sort_range": "target_range",
}


def _pick_sort_key(message: str, headers: list[str]) -> str | None:
    """정렬 기준 열을 고른다. 정렬어 바로 앞에 붙은 열이 기준이다.

    등장 순서로 첫 열을 집으면 "매출 데이터를 날짜 오름차순으로 정렬"에서 매출로 정렬해
    데이터가 통째로 뒤섞인다. 되돌릴 수는 있어도 사용자가 원한 결과는 아니다.
    """
    text = str(message or "")
    mentions = find_header_mentions(text, headers)
    if not mentions:
        return None

    def is_dataset_label(hit: dict[str, Any]) -> bool:
        tail = text[hit["end"] : hit["end"] + 8].lstrip()
        return any(tail.startswith(word) for word in _DATASET_LABELS)

    ranked = [hit for hit in mentions if not is_dataset_label(hit)] or mentions
    marker = _SORT_ORDER_MARKER.search(text)
    if marker:
        before = [hit for hit in ranked if hit["start"] < marker.start()]
        if before:
            return before[-1]["header"]
    return ranked[0]["header"]


# 집계 기준을 가리키는 조사. "지역별", "제품 분류마다", "고객당" 모두 같은 뜻이다.
_GROUP_MARKER = re.compile(r"\s*(?:별|마다|당)")
# 무엇을 더할지 가리키는 말. "매출 합계"뿐 아니라 "매출이 얼마나 나오는지"도 같은 요청이다.
_MEASURE_MARKER = re.compile(
    r"\s*(?:을|를|은|는|이|가|의)?\s*(?:합계|합|총액|총합|평균|개수|건수|카운트|얼마|실적|규모|집계)"
)


def _bind_pivot(params: dict[str, Any], *, message: str, entry: dict[str, Any]) -> list[str]:
    headers = _headers(entry)
    notes: list[str] = []
    text = str(message or "")
    source_sheet = str(entry.get("name") or "").strip()
    if source_sheet:
        # 슬롯/플래너가 활성 시트(Dashboard 등)를 원본으로 넣어 두면, 머리글이
        # 있는 시트로 옮긴 뒤에도 빈 시트를 집계한다. 비어 있을 때만 채우면 부족하다.
        current = str(params.get("source_sheet") or "").strip()
        if current != source_sheet:
            params["source_sheet"] = source_sheet
            notes.append(f"source_sheet={source_sheet}")
    mentions = find_header_mentions(text, headers)
    row_field = _pick_role_column(message, headers, ("행",))
    col_field = _pick_role_column(message, headers, ("열",))
    if not row_field:
        # "지역별 매출 합계" — 'X별'은 거의 언제나 집계 기준(행)이다.
        # "제품 분류마다"처럼 '별' 대신 '마다/당'을 쓰는 사람도 그만큼 많다.
        for hit in mentions:
            if _GROUP_MARKER.match(text[hit["end"] : hit["end"] + 3]):
                row_field = hit["header"]
                break
    if not row_field:
        # 집계 표지가 없어도 언급된 비숫자 열이 하나면 그게 기준이다.
        # 여기서 못 정하면 플래머가 지어낸 Order_ID로 묶여 180행짜리 "요약"이 나온다.
        text_only = [
            hit["header"]
            for hit in mentions
            if hit["header"] != col_field and not _column_meta(entry, hit["header"]).get("numeric")
        ]
        if len(set(text_only)) == 1:
            row_field = text_only[0]
    value_field = None
    for hit in mentions:
        header = hit["header"]
        if header in (row_field, col_field):
            continue
        if not _column_meta(entry, header).get("numeric"):
            continue
        if _MEASURE_MARKER.match(text[hit["end"] : hit["end"] + 8]):
            value_field = header
            break
    if value_field is None:
        # "지역별 매출을 집계" 처럼 집계어가 붙지 않아도, 언급된 숫자 열이 하나면 그게 값이다.
        numeric_mentions = [
            hit["header"]
            for hit in mentions
            if hit["header"] not in {row_field, col_field}
            and _column_meta(entry, hit["header"]).get("numeric")
        ]
        if len(numeric_mentions) == 1:
            value_field = numeric_mentions[0]
    if row_field:
        params["row_field"] = row_field
        notes.append(f"row_field={row_field}")
    if col_field and col_field != row_field:
        params["column_field"] = col_field
        notes.append(f"column_field={col_field}")
    if value_field:
        params["value_field"] = value_field
        notes.append(f"value_field={value_field}")
    if re.search(r"평균", message):
        params["agg"] = "avg"
    elif re.search(r"개수|건수|카운트", message):
        params["agg"] = "count"
    elif re.search(r"합계|합", message):
        params["agg"] = "sum"
    return notes


def _bind_filter_value(params: dict[str, Any], *, message: str, entry: dict[str, Any]) -> list[str]:
    """ "완료된 건만" 처럼 값만 말한 필터를 실제 셀 값으로 확정한다.

    플래너가 값을 채웠더라도 시트에 없는 값("완료된")이면 필터 결과가 0행이 된다.
    실제 셀 값 목록에 있는 값이 원문에 있으면 그쪽이 항상 옳다.
    """
    notes: list[str] = []
    text = str(message or "")
    numeric = re.search(r"(-?\d+(?:\.\d+)?)\s*(이상|초과|이하|미만)", text)
    if numeric:
        op_map = {"이상": ">=", "초과": ">", "이하": "<=", "미만": "<"}
        params["operator"] = op_map.get(numeric.group(2), ">=")
        params["value"] = float(numeric.group(1))
        notes.append(f"value={params['value']}")
        return notes

    current_value = params.get("value")
    if current_value not in (None, "") and not isinstance(current_value, str):
        return notes

    known_values = {
        str(category)
        for col in entry.get("columns") or []
        for category in col.get("categories") or []
        if category
    }
    if current_value and str(current_value) in known_values:
        notes.extend(_bind_filter_column_from_value(params, entry=entry))
        return notes

    for col in entry.get("columns") or []:
        for category in col.get("categories") or []:
            if category and category in text:
                params["column"] = str(col.get("header") or params.get("column") or "")
                params["operator"] = params.get("operator") or "=="
                params["value"] = category
                notes.append(f"column={params['column']},value={category}")
                return notes
    return notes


def _bind_filter_column_from_value(params: dict[str, Any], *, entry: dict[str, Any]) -> list[str]:
    """값이 어느 한 열에만 있으면 그 열을 기준 열로 삼는다.

    "완료된 것만 남겨줘"는 기준 열을 말하지 않았지만 '완료'가 상태 열에만 있다면
    되물을 것이 없다. 두 열 이상에 있으면 정하지 않고 남겨서 되묻게 한다.
    """
    if str(params.get("column") or "").strip():
        return []
    value = str(params.get("value") or "")
    if not value:
        return []
    owners = [
        str(col.get("header") or "")
        for col in entry.get("columns") or []
        if value in {str(category) for category in col.get("categories") or []}
    ]
    owners = [name for name in owners if name]
    if len(owners) != 1:
        return []
    params["column"] = owners[0]
    return [f"column={owners[0]}"]


_EXCLUSION_VERB = re.compile(r"(빼|제외|없애|제거|지워|지우|삭제|말고|아닌|제하고|빠뜨)", re.IGNORECASE)


def _bind_filter_mode(params: dict[str, Any], *, message: str) -> list[str]:
    """ "남길 것"과 "뺄 것"을 가른다.

    플래너는 "취소된 주문은 빼줘"를 operator="==", value="취소"로 옮겨 놓는다. 그대로 실행하면
    취소 건만 남기고 나머지를 전부 지우는, 요청과 정반대의 편집이 된다.

    판정은 값 바로 뒤에 "만"이 붙었는지로 한다. "완료된 건만 남겨줘"는 포함,
    "취소된 주문은 빼고 나머지만 남겨줘"는 값 뒤가 "된 주문은"이라 제외로 읽는다.
    """
    text = str(message or "")
    value = str(params.get("value") or "").strip()
    if not text or not value or not _EXCLUSION_VERB.search(text):
        return []
    position = text.find(value)
    if position >= 0 and "만" in text[position + len(value) : position + len(value) + 8]:
        return []
    if str(params.get("mode") or "") == "remove":
        return []
    params["mode"] = "remove"
    return ["mode=remove"]


_WRITE_VALUE_PATTERN = re.compile(
    r"([A-Za-z]{1,3}\d{1,7}(?::[A-Za-z]{1,3}\d{1,7})?)"
    r"\s*(?:셀|범위)?\s*에\s*(.+?)\s*(?:입력|기입|써|쓰|넣|적어|set|write)",
    re.IGNORECASE,
)
# "A8:A13에 지역 목록 입력 (서울, 경기, …)" — 값이 동사 **뒤** 괄호에 오는 형태.
# 분해기가 이 문장을 실제로 낸다. 앞의 패턴은 "지역 목록"을 값으로 잡아 A8 한 칸만 채우고,
# 뒤 단계의 SUMIF가 빈 기준을 보게 된다(2026-08-16 실측: 매크로가 11단계에서 멈췄다).
_WRITE_VALUE_PAREN_PATTERN = re.compile(
    r"([A-Za-z]{1,3}\d{1,7}(?::[A-Za-z]{1,3}\d{1,7})?)"
    r"\s*(?:셀|범위)?\s*에\s*[^()]*?(?:입력|기입|써|쓰|넣|적어|set|write)[^()]*?\(([^()]+)\)",
    re.IGNORECASE,
)

_CELL_REF_PATTERN = re.compile(r"^([A-Za-z]{1,3})(\d{1,7})$")


def _column_index(letters: str) -> int:
    index = 0
    for char in letters.upper():
        index = index * 26 + (ord(char) - ord("A") + 1)
    return index


def _range_shape(ref: str) -> tuple[str, int, int] | None:
    """ "E2:G2" → ("E2", 1, 3). 단일 셀이면 (셀, 1, 1).

    시작 셀은 항상 좌상단으로 정규화한다. "G2:E2"처럼 거꾸로 적어도 E2가 기준이 돼야
    값이 범위 밖으로 밀려나지 않는다.
    """
    parts = ref.upper().split(":")
    head = _CELL_REF_PATTERN.match(parts[0])
    if not head:
        return None
    if len(parts) == 1:
        return parts[0], 1, 1
    tail = _CELL_REF_PATTERN.match(parts[1])
    if not tail:
        return None

    head_col, tail_col = _column_index(head.group(1)), _column_index(tail.group(1))
    head_row, tail_row = int(head.group(2)), int(tail.group(2))
    left_letters = head.group(1) if head_col <= tail_col else tail.group(1)
    return (
        f"{left_letters}{min(head_row, tail_row)}",
        abs(tail_row - head_row) + 1,
        abs(tail_col - head_col) + 1,
    )


def _shape_write_values(raw: str, row_count: int, col_count: int) -> list[list[Any]]:
    """원문의 값 부분을 대상 범위 모양에 맞춘 2차원 배열로 만든다.

    단일 셀이면 콤마를 건드리지 않는다 — "C3에 1,000 입력"의 천 단위 구분자를
    구분자로 오해해 쪼개면 값이 1과 0으로 망가진다. 범위가 명시됐을 때만
    콤마를 값 구분자로 본다.
    """
    if row_count == 1 and col_count == 1:
        literal = _coerce_literal(raw)
        return [[literal]] if literal != "" else []

    parts = [_coerce_literal(part) for part in raw.split(",")]
    parts = [part for part in parts if part != ""]
    if not parts:
        return []
    if col_count == 1:
        return [[part] for part in parts]
    if row_count == 1:
        return [parts]
    return [parts[index : index + col_count] for index in range(0, len(parts), col_count)]


def _coerce_literal(text: str) -> Any:
    value = str(text or "").strip().strip("'\"")
    if not value:
        return value
    try:
        number = float(value.replace(",", ""))
    except ValueError:
        return value
    return int(number) if number.is_integer() else number


# "지워줘"는 빈 값을 쓰는 게 정답이다. 그 경우만 빈 쓰기를 허용한다.
_CLEARING_INTENT = re.compile(r"(지워|지운|삭제|제거|비워|비우|clear|초기화|없애)", re.IGNORECASE)


# 계산을 **시킨** 문장인가. 명사가 아니라 동사로 판정한다.
#
# 처음엔 "합계|총|평균" 같은 명사를 넣었더니 "A1에 총매출 입력"이 걸렸다 —
# 사용자가 진짜로 '총매출'이라는 머리글을 쓰려는 정당한 요청인데 막힌다.
# 계산을 시킨 문장은 동사나 최상급으로 드러난다("더한", "구하는", "가장 큰").
_COMPUTE_REQUEST = re.compile(
    r"(더한|더해|합산|구하는|구해|계산해|계산하|산출|세는|세어|매기는|매겨|"
    r"가장\s*(큰|작은|높|낮|많|적)|최댓값|최솟값)"
)


def write_values_echo_the_request(params: dict[str, Any], message: str) -> bool:
    """쓰려는 값이 **요청 문장을 되뇐 것**인지 본다.

    2026-08-17 실측: 함수 선택 배터리 12건 중 2건이 이렇게 실패했다.

        "F2에 서울 지역 매출만 더한 값 넣어줘"  → F2에 "서울 지역 매출만 더한" (텍스트)
        "F7에 가장 큰 매출 값 넣어줘"          → F7에 "가장 큰 매출" (텍스트)

    같은 실패가 서식에서도 났다 — "천 단위 콤마 넣어줘"가 셀에 '천 단위 콤마'를
    써서 원래 있던 97000을 덮었다. 규칙이 못 잡으면 플래너가 **시킨 말을 값으로**
    쓴다. 계산을 시킨 문장인데 쓰려는 값이 그 문장 안에 그대로 들어 있으면,
    데이터가 아니라 지시문이다.

    판정을 좁게 잡는다 — 사용자가 진짜로 "합계"라는 **머리글**을 쓰려는 경우가 있다.
    그래서 (1) 계산을 시킨 문장이고, (2) 값이 문장의 연속된 조각이며,
    (3) 두 글자를 넘을 때만 되뇐 것으로 본다.
    """
    text = re.sub(r"\s+", " ", str(message or "")).strip()
    if not text or not _COMPUTE_REQUEST.search(text):
        return False
    values = params.get("values_2d")
    if not isinstance(values, list) or not values:
        return False
    # 2행 × 2열 이상의 격자는 지시문의 되뇜이 아니라 **표 데이터**다. 값 하나가
    # 우연히 계산 낱말을 품어도("AI 물량 자동 산출", "평균 24시간 / 1회") 표를
    # 통째로 되묻기로 보내면 안 된다(2026-08-19 ex7 건설 대화 실측: 6×5 격자가
    # '산출' 한 낱말 때문에 "어떤 값을 넣을지 정하지 못했습니다"로 샜다).
    filled_rows = [
        [c for c in (row if isinstance(row, list) else [row]) if c is not None and str(c).strip() != ""]
        for row in values
    ]
    filled_rows = [r for r in filled_rows if r]
    if len(filled_rows) >= 2 and max(len(r) for r in filled_rows) >= 2:
        return False
    for row in values:
        cells = row if isinstance(row, list) else [row]
        for cell in cells:
            token = re.sub(r"\s+", " ", str(cell if cell is not None else "")).strip()
            if len(token) <= 2:
                continue
            if token.startswith("="):
                continue  # 수식은 정상이다
            # 각주·완결 문장은 지시문의 되뇜이 아니라 **글자 그대로 쓸 값**이다 —
            # "A45에 ※ AI 적용 후 수치는 … 산출되었습니다 입력"(2026-08-19 ex11 실측: '산출' 한 낱말
            # 때문에 되묻기로 샜다). 되뇐 조각은 짧은 구("가장 큰 매출")지 문장이 아니다.
            if _LITERAL_SENTENCE_VALUE.search(token) or len(token) >= 24:
                continue
            if token in text:
                return True
    return False


_LITERAL_SENTENCE_VALUE = re.compile(
    r"^[※*＊†‡#(\[]|(?:습니다|입니다|됩니다|합니다|세요|십시오|하세요|바랍니다|드립니다|요|임|음)[.!?]?$|[.!?。]$"
)


def write_values_are_empty(params: dict[str, Any]) -> bool:
    """무엇을 쓸지 끝내 정하지 못한 상태인지 본다.

    규칙 파서가 "H1에 넣어줘"처럼 값이 없는 문장에서 `values_2d=[[""]]`를 만들어 내고,
    그게 그대로 실행돼 **빈 칸을 쓰고도 성공으로 보고**된 사례가 있다(2026-08-16 실측:
    되묻기 다음 턴에 원 요청의 '총매출'이 유실됐는데 검증기는 빈 값 대 빈 셀을 같다고
    보고 통과시켰다). 추측해서 빈 칸을 쓰느니 무엇을 넣을지 되묻는 편이 낫다.
    """
    values = params.get("values_2d")
    if not isinstance(values, list) or not values:
        return True
    for row in values:
        cells = row if isinstance(row, list) else [row]
        for cell in cells:
            if cell is not None and str(cell).strip() != "":
                return False
    return True


def _bind_write_values(params: dict[str, Any], *, message: str) -> list[str]:
    """ "C3에 120 입력해줘"처럼 값이 원문에만 있는 쓰기 명령을 채운다.

    플래너는 이 경우 values_2d를 빠뜨리기 쉬운데, 그러면 검증기에서 막혀
    가장 단순한 명령이 되묻기로 떨어진다.
    """
    existing = params.get("values_2d")
    text = str(message or "")
    # 괄호형이 있으면 그쪽이 진짜 값이다 — 앞 패턴은 "지역 목록" 같은 설명을 값으로 잡는다.
    match = _WRITE_VALUE_PAREN_PATTERN.search(text) or _WRITE_VALUE_PATTERN.search(text)
    if not match:
        return []
    shape = _range_shape(match.group(1))
    if shape is None:
        return []
    top_left, row_count, col_count = shape
    existing_cells = 0
    if isinstance(existing, list) and existing:
        existing_cells = sum(len(r) if isinstance(r, list) else 1 for r in existing)
        # 값이 이미 있어도 원문이 지목한 범위를 다 못 채우면 그대로 둘 수 없다.
        # "A1:A3에 총매출,총이익,평균주문금액 입력"에 값 하나만 실려 오면
        # 나머지 두 칸은 아무 말 없이 빈 채로 남는다.
        if existing_cells >= row_count * col_count:
            return []
    values = _shape_write_values(match.group(2), row_count, col_count)
    if not values:
        return []
    if existing_cells and sum(len(row) for row in values) <= existing_cells:
        # 원문에서 다시 뽑아도 나아지지 않으면 플래너가 준 값을 존중한다.
        return []
    params["values_2d"] = values
    # 원문에 범위가 명시됐으면 그 좌상단이 항상 옳다. 플래너가 범위의 끝 셀을
    # 시작으로 잡아 오는 경우가 있는데, 두면 값이 범위 밖으로 밀려 쓰인다.
    start_cell = str(params.get("start_cell") or "").strip().upper()
    is_placeholder = not start_cell or start_cell in {"__ACTIVE_CELL__", "__ACTIVE_SELECTION__"}
    if is_placeholder or row_count > 1 or col_count > 1:
        params["start_cell"] = top_left
    return [f"values_2d={values}"]


_OUTPUT_SHEET_TARGET_PATTERN = re.compile(
    r"([A-Za-z0-9가-힣_]{1,20})\s*(?:시트|sheet)\s*(?:으로|로|에)", re.IGNORECASE
)


def _bind_consolidate(params: dict[str, Any], *, message: str, digest: dict[str, Any]) -> list[str]:
    """ "1분기,2분기,3분기 시트를 통합1 시트로" 에서 원본/결과 시트를 갈라낸다."""
    known = [name for name in sheet_names(digest) if name]
    if not known:
        return []
    text = str(message or "")
    changes: list[str] = []

    output = str(params.get("output_sheet") or "").strip()
    if not output or output in known:
        for match in _OUTPUT_SHEET_TARGET_PATTERN.finditer(text):
            candidate = match.group(1)
            if candidate not in known:
                output = candidate
                params["output_sheet"] = candidate
                changes.append(f"output_sheet={candidate}")
                break

    sources = params.get("source_sheets")
    valid_sources = [s for s in sources if s in known] if isinstance(sources, list) else []
    if not valid_sources:
        found = sorted(
            ((text.find(name), name) for name in known if name != output and name in text),
            key=lambda row: row[0],
        )
        valid_sources = [name for pos, name in found if pos >= 0]
    if valid_sources and valid_sources != sources:
        params["source_sheets"] = valid_sources
        changes.append(f"source_sheets={valid_sources}")
    return changes


def _normalize_output_sheet(
    params: dict[str, Any], *, message: str = "", source_sheet: str = "", action: str = ""
) -> list[str]:
    """결과를 쓸 시트를 확정한다.

    - "피벗1!A1"처럼 시트명과 셀이 붙어 온 값을 분리한다.
    - 원문이 "Region_Chart 시트에"라고 결과 시트를 지목했으면 그 이름을 쓴다.
    - 결과 시트가 원본 시트와 같으면 원본을 덮어쓰게 되므로 다른 이름으로 옮긴다.
    """
    changes: list[str] = []
    raw = params.get("output_sheet")
    if isinstance(raw, str) and "!" in raw:
        sheet_part, _, cell_part = raw.partition("!")
        sheet_part = sheet_part.strip().strip("'")
        cell_part = cell_part.strip().upper()
        if sheet_part:
            params["output_sheet"] = sheet_part
            if _CELL_REF_PATTERN.match(cell_part) and "output_start" in params:
                params["output_start"] = cell_part
            changes.append(f"output_sheet={sheet_part}")

    named = _result_sheet_from_message(message)
    if action == "excel_live.create_chart" and not named:
        # 차트는 표를 덮어쓰지 않는 오버레이라 소스 시트에 그대로 붙이면 된다.
        # 집계 계열과 달리 별도 시트로 뺄 이유가 없는데, 플래너가 "Rep_Chart" 같은
        # 이름을 지어내면 사용자가 보던 시트에는 아무것도 안 생기고 낯선 시트만 하나 는다.
        if params.pop("output_sheet", None) is not None:
            changes.append("output_sheet=소스 시트")
        return changes
    if named and str(params.get("output_sheet") or "").strip() != named:
        params["output_sheet"] = named
        changes.append(f"output_sheet={named}")

    current = str(params.get("output_sheet") or "").strip()
    if action == "excel_live.pivot_table" and source_sheet and (not current or current == source_sheet):
        # 비어 있으면 원본 시트 A1에 쓴다. 오늘 실슬에서 Sales_Data 머리글을
        # 지역/매출 집계로 덮어썼다. "새 시트"라고 불렀든 아니든 원본은 건드리면 안 된다.
        params["output_sheet"] = f"{source_sheet}_집계"
        changes.append(f"output_sheet={params['output_sheet']}")
        current = params["output_sheet"]
    elif current and source_sheet and current == source_sheet:
        # 집계 결과를 원본 시트에 쓰면 원본 데이터가 지워진다.
        params["output_sheet"] = f"{source_sheet}_집계"
        changes.append(f"output_sheet={params['output_sheet']}")

    changes.extend(_normalize_output_start(params, message=message))
    return changes


def _normalize_output_start(params: dict[str, Any], *, message: str = "") -> list[str]:
    """결과 시트의 시작 셀은 원문이 지목하지 않는 한 A1이다.

    플래너가 근거 없이 `output_start="B1"`을 채우면 결과표가 한 칸 밀려 A열이 빈다.
    실행은 성공으로 보고되지만 이후 차트·수식이 A열을 참조해 조용히 어긋난다.
    """
    if "output_start" not in params:
        return []
    current = str(params.get("output_start") or "").strip().upper()
    if not current or current == "A1":
        return []
    if _CELL_REF_PATTERN.match(current) and current in _mentioned_cell_refs(message):
        return []
    params["output_start"] = "A1"
    return [f"output_start=A1(was {current})"]


_CELL_REF_MENTION = re.compile(r"(?<![A-Za-z0-9])([A-Za-z]{1,3}\d{1,7})(?![A-Za-z0-9])")


def _mentioned_cell_refs(message: str) -> set[str]:
    return {match.group(1).upper() for match in _CELL_REF_MENTION.finditer(str(message or ""))}


_RESULT_SHEET_MENTION = re.compile(
    r"([A-Za-z0-9가-힣_]{1,24})\s*(?:시트|sheet)\s*(?:에|로|에다|으로)", re.IGNORECASE
)


def _result_sheet_from_message(message: str) -> str:
    """ "Region_Chart 시트에 정리해줘" — 결과를 담을 시트 이름을 원문에서 찾는다."""
    match = _RESULT_SHEET_MENTION.search(str(message or ""))
    if not match:
        return ""
    name = _strip_josa(match.group(1))
    return name if name and name not in {"새", "다른", "이", "그"} else ""


_COLOR_WORDS: tuple[tuple[str, str], ...] = (
    # 긴 낱말이 먼저다 — "연회색"이 "회색"에 먼저 걸리면 진회색이 된다.
    ("연회색", "#F2F2F2"),
    # 한 음절 '빨'은 '**빨**리'·'**빨**래'에도 걸린다 — 분홍 요청이 빨강이 됐다
    # (2026-08-20 게이트7 실측: "빨리 찾아야 해서요 … 분홍색으로" → D2·D4가 빨강).
    ("빨강", "#FF0000"),
    ("빨간", "#FF0000"),
    ("빨갛", "#FF0000"),
    ("빨개", "#FF0000"),
    ("적색", "#FF0000"),
    ("red", "#FF0000"),
    ("주황", "#FFA500"),
    ("노랑", "#FFFF00"),
    ("노란", "#FFFF00"),
    ("yellow", "#FFFF00"),
    ("초록", "#00B050"),
    ("녹색", "#00B050"),
    ("green", "#00B050"),
    # 하늘색이 "파랑"류보다 먼저 있어야 한다 — 둘 다 있는 문장은 드물지만
    # 하늘색 문장이 파랑으로 뭉개지면 안 된다.
    ("하늘색", "#9DC3E6"),
    ("파랑", "#0070C0"),
    ("파란", "#0070C0"),
    ("blue", "#0070C0"),
    ("회색", "#D9D9D9"),
    ("gray", "#D9D9D9"),
    ("grey", "#D9D9D9"),
    # 2026-08-18 사람 말투 실측: "분홍색으로 강조"가 어휘에 없어 플래너의
    # 빨강이 그대로 나갔다. 강조에 실제로 쓰는 이름들을 채운다.
    ("분홍", "#FFC0CB"),
    ("핑크", "#FFC0CB"),
    ("pink", "#FFC0CB"),
    ("남색", "#002060"),
    ("네이비", "#002060"),
    ("navy", "#002060"),
    ("보라", "#7030A0"),
    ("purple", "#7030A0"),
    ("갈색", "#843C0C"),
    ("brown", "#843C0C"),
)
# 머리글 재지목 시 시트를 계획에 실어 줄 액션들 — 열 기준으로 도는 편집이라
# 시트가 빠지면 활성 시트를 망친다. 생성·조회류는 제외.
_SHEET_RETARGET_ACTIONS = frozenset(
    {
        "excel_live.sort_range",
        "excel_live.sort_rows",
        "excel_live.filter_rows",
        "excel_live.dedupe_rows",
        "excel_live.set_number_format",
        "excel_live.highlight_by_condition",
        "excel_live.apply_data_bar",
        "excel_live.apply_color_scale",
    }
)
_COLUMN_ONLY_MENTION = re.compile(r"(?<![A-Za-z0-9])([A-Za-z])\s*열")
_CONDITION_FORMAT_ACTIONS = {"excel_live.highlight_by_condition", "excel_live.fill_range"}
_CONDITION_OPERATORS: dict[str, str] = {
    "이상": ">=",
    "초과": ">",
    "넘는": ">",
    "이하": "<=",
    "미만": "<",
    "이면": "==",
}
_CONDITION_WORD = re.compile("(" + "|".join(_CONDITION_OPERATORS) + ")")


def _split_range_prefix(range_ref: str) -> tuple[str, str]:
    """`PROJECT_PLAN!I2:I21` → (`PROJECT_PLAN!`, `I2:I21`). 시트 접두어를 보존하기 위해."""
    if "!" not in range_ref:
        return "", range_ref
    sheet, _, ref = range_ref.rpartition("!")
    return f"{sheet}!", ref


def _retarget_sheet_by_headers(
    text: str,
    entry: dict[str, Any] | None,
    digest: dict[str, Any] | None,
    prefix: str,
) -> tuple[dict[str, Any] | None, str]:
    """원문이 부른 열을 가장 많이 가진 시트로 대상을 옮긴다.

    "재고가 재주문점 이하인 제품" 은 Inventory 시트 이야기인데, 활성 시트가 Sales_Data면
    엉뚱한 열을 칠하고 0건으로 끝난다. "제품" 하나만 겹쳐도 활성 시트에 눌러앉지 않도록
    겹치는 열 개수로 고른다. 최고점이 하나가 아니면 옮기지 않는다 — 추측은 위험하다.
    """
    if not digest:
        return entry, prefix
    scored = [
        (len({hit["header"] for hit in find_header_mentions(text, _headers(sheet))}), sheet)
        for sheet in (digest.get("sheets") or [])
        if _headers(sheet)
    ]
    if not scored:
        return entry, prefix
    best = max(score for score, _ in scored)
    leaders = [sheet for score, sheet in scored if score == best]
    if best == 0 or len(leaders) != 1:
        return entry, prefix
    target = leaders[0]
    if target is entry:
        return entry, prefix
    return target, f"{target.get('name')}!"


_OPERATOR_EXPRESSION = re.compile(r"^\s*(.+?)\s*(<=|>=|<>|!=|<|>|==?)\s*(.+?)\s*$")


def _normalize_operator_expression(
    params: dict[str, Any],
    *,
    entry: dict[str, Any] | None,
    prefix: str,
    digest: dict[str, Any] | None = None,
) -> list[str]:
    """operator에 식을 통째로 넣어 오는 경우를 풀어 준다.

    플래너가 `operator: "Current_Stock < Reorder_Point"` 처럼 답할 때가 있다. 그대로
    넘기면 "지원하지 않는 연산자"로 죽는다. 양쪽이 열 이름이면 열 비교로, 오른쪽이
    숫자면 임계값 비교로 바꾼다. 식에 나온 열을 가진 시트를 직접 찾으므로 활성 시트가
    달라도 된다.
    """
    match = _OPERATOR_EXPRESSION.match(str(params.get("operator") or "").strip())
    if not match:
        return []
    left_name, symbol, right_name = match.group(1), match.group(2), match.group(3)
    symbol = {"==": "==", "=": "==", "!=": "<>"}.get(symbol, symbol)

    resolved = _sheet_owning_columns(left_name, right_name, entry, digest)
    if resolved is None:
        return []
    owner, left_letter, right_letter = resolved
    if owner is not entry and owner.get("name"):
        prefix = f"{owner['name']}!"

    params["operator"] = symbol
    params["target_range"] = f"{prefix}{left_letter}:{left_letter}"
    changes = [f"operator={symbol}", f"target_range={prefix}{left_letter}:{left_letter}"]

    if right_letter:
        params["compare_column"] = right_letter
        params.setdefault("threshold", 0)
        changes.append(f"compare_column={right_letter}")
        return changes
    try:
        params["threshold"] = float(str(right_name).replace(",", "").replace("%", ""))
        changes.append(f"threshold={params['threshold']}")
    except ValueError:
        pass
    return changes


def _sheet_owning_columns(
    left_name: str,
    right_name: str,
    entry: dict[str, Any] | None,
    digest: dict[str, Any] | None,
) -> tuple[dict[str, Any], str, str] | None:
    """식의 왼쪽(그리고 가능하면 오른쪽) 열을 실제로 가진 시트를 찾는다.

    양쪽을 다 가진 시트를 우선한다. "제품" 같은 흔한 낱말이 다른 시트 머리글에
    우연히 걸려 엉뚱한 시트로 가는 걸 막기 위해서다.
    """
    candidates: list[dict[str, Any]] = []
    if entry:
        candidates.append(entry)
    for sheet in (digest or {}).get("sheets") or []:
        if sheet is not entry and _headers(sheet):
            candidates.append(sheet)

    partial: tuple[dict[str, Any], str, str] | None = None
    for candidate in candidates:
        headers = _headers(candidate)
        left_letter = _letter_for(candidate, resolve_header(left_name, headers))
        if not left_letter:
            continue
        right_letter = _letter_for(candidate, resolve_header(right_name, headers))
        if right_letter:
            return candidate, left_letter, right_letter
        if partial is None:
            partial = (candidate, left_letter, "")
    return partial


def _letter_for(entry: dict[str, Any], header: str | None) -> str:
    if not header:
        return ""
    return str(_column_meta(entry, header).get("letter") or "").upper()


def _bind_condition_format(
    params: dict[str, Any],
    *,
    message: str,
    entry: dict[str, Any] | None = None,
    digest: dict[str, Any] | None = None,
) -> list[str]:
    """ "매출이 10만 원 미만이면 빨간색" 처럼 원문에만 있는 대상 열·색·임계값을 채운다.

    플래너는 target_range를 A:Z로 뭉뚱그리거나, 반대로 `Project_Plan!J2:J21`처럼
    그럴듯하지만 틀린 열을 지어낸다. 원문이 열을 이름으로 불렀다면 그쪽이 최종 근거다.
    """
    changes: list[str] = []
    text = str(message or "")

    current = str(params.get("target_range") or "").strip().upper()
    prefix, current = _split_range_prefix(current)
    # 원문이 말한 열 이름이 다른 시트에만 있으면 그 시트로 옮긴다("진행률"은 Project_Plan에 있다).
    entry, prefix = _retarget_sheet_by_headers(text, entry, digest, prefix)
    broad = current in {"", "__ACTIVE_SELECTION__", "A:Z", "A:XFD"}
    letter = ""
    column = _COLUMN_ONLY_MENTION.search(text)
    if column:
        letter = column.group(1).upper()
    elif entry:
        # "매출이 10만 원 미만" — 열을 이름으로 불렀으면 그 열의 문자로 좁힌다.
        # 값 일치 조건("상태가 대기인 애들만")의 기준 열은 원래 글자 열이다 —
        # 숫자 열만 인정하면 텍스트 조건이 못 좁혀져 0건 강조가 된다
        # (2026-08-18 지저분판 실측).
        equality_value = str(params.get("value") or "").strip()
        hits = list(find_header_mentions(text, _headers(entry)))
        # "…**운송장**이 몇 개인지 …, **상태 열에서** 대기인 셀만" — 첫 히트로 고르면
        # 운송장(A열)이 이긴다. `<머리글> 열/칸/컬럼`으로 콕 집은 것이 먼저다.
        named = [
            hit
            for hit in hits
            if re.search(
                rf"{re.escape(str(hit['header']))}\s*(?:열|칸|컬럼|column)", text, re.IGNORECASE
            )
        ]
        for hit in named + hits:
            meta = _column_meta(entry, hit["header"])
            if meta.get("letter") and (meta.get("numeric") or equality_value):
                letter = str(meta["letter"]).upper()
                break
    # 원문이 범위를 직접 말하지 않았다면 플래너가 좁혀 놓은 범위를 믿지 않는다.
    trusted = bool(_EXPLICIT_RANGE_MENTION.search(text))
    # 이미 한 열로, 행까지 지정해 좁혀진 범위가 **원문이 부른 그 열과 같으면** 그대로 둔다.
    # 통합문서를 보고 확정한 범위를 되돌리면 0칸 강조가 된다(2026-08-20 게이트7).
    # 다른 열이면 플래너의 추측이므로 원문이 이긴다(기존 규칙 그대로).
    scoped = re.fullmatch(r"([A-Z]{1,3})\d{1,7}:([A-Z]{1,3})\d{1,7}", current)
    same_column_scope = bool(scoped and letter and scoped.group(1) == scoped.group(2) == letter)
    if letter and (broad or (not trusted and not same_column_scope)):
        params["target_range"] = f"{prefix}{letter}:{letter}"
        changes.append(f"target_range={prefix}{letter}:{letter}")
    elif prefix and current:
        params["target_range"] = f"{prefix}{current}"

    lowered = text.lower()
    for word, hex_code in _COLOR_WORDS:
        if word in lowered:
            if str(params.get("fill_color") or "").upper() != hex_code:
                params["fill_color"] = hex_code
                changes.append(f"fill_color={hex_code}")
            break

    # "현재고가 재주문점 이하" — 기준이 숫자가 아니라 같은 행의 다른 열인 경우.
    comparison = _column_comparison(text, entry)
    if comparison:
        left, right, operator = comparison
        params["target_range"] = f"{prefix}{left}:{left}"
        params["compare_column"] = right
        params["operator"] = operator
        params.setdefault("threshold", 0)
        changes.extend(
            [f"target_range={prefix}{left}:{left}", f"compare_column={right}", f"operator={operator}"]
        )
        return changes

    changes.extend(_normalize_operator_expression(params, entry=entry, prefix=prefix, digest=digest))

    # 플래너는 같은 문장에도 '>=', 'greater_than', {'comparator': 'gt'} 를 번갈아 낸다.
    # 비교 방향과 기준값은 사용자가 이미 말했으므로 원문을 최종 근거로 삼는다.
    parsed = parse_condition(text)
    if parsed:
        operator, threshold, percent = parsed
        if str(params.get("operator") or "") != operator:
            params["operator"] = operator
            changes.append(f"operator={operator}")
        if percent and _looks_like_ratio_column(entry, letter):
            # 진행률이 0.8로 저장된 시트에 80을 넣으면 아무 행도 걸리지 않는다.
            threshold = threshold / 100.0
        params["threshold"] = threshold
        changes.append(f"threshold={threshold}")
    return changes


_COMPARISON_WORDS: dict[str, str] = {
    "이하": "<=",
    "이상": ">=",
    "미만": "<",
    "초과": ">",
    # "보다 적거나 같은"은 "같"에 먼저 걸려 ==로 새기 쉽다. 긴 표현부터 맞춘다.
    "보다 작거나 같": "<=",
    "보다 적거나 같": "<=",
    "보다 낮거나 같": "<=",
    "보다 크거나 같": ">=",
    "보다 많거나 같": ">=",
    "보다 높거나 같": ">=",
    "보다 작": "<",
    "보다 적": "<",
    "보다 낮": "<",
    "보다 크": ">",
    "보다 많": ">",
    "보다 높": ">",
    "같": "==",
}
_COMPARISON_WORDS_BY_LENGTH = sorted(_COMPARISON_WORDS.items(), key=lambda item: -len(item[0]))


def _column_comparison(text: str, entry: dict[str, Any] | None) -> tuple[str, str, str] | None:
    """ "현재고가 재주문점 이하" → (E, F, "<="). 열끼리 비교하는 조건인지 판정한다.

    두 머리글 사이에 숫자가 끼어 있으면 열 비교가 아니라 값 비교이므로 넘긴다.
    """
    if not entry:
        return None
    headers = _headers(entry)
    mentions = find_header_mentions(text, headers)
    if len(mentions) < 2:
        return None
    left, right = mentions[0], mentions[1]
    between = text[left["end"] : right["start"]]
    if re.search(r"\d", between):
        return None
    tail = text[right["end"] : right["end"] + 16]
    operator = next((op for word, op in _COMPARISON_WORDS_BY_LENGTH if word in tail), None)
    if operator is None:
        return None
    left_letter = str(_column_meta(entry, left["header"]).get("letter") or "")
    right_letter = str(_column_meta(entry, right["header"]).get("letter") or "")
    if not left_letter or not right_letter:
        return None
    return left_letter.upper(), right_letter.upper(), operator


def _last_data_row(entry: dict[str, Any] | None) -> int:
    """시트 사용범위의 마지막 행. 알 수 없으면 헤더 다음 한 행만 채운다."""
    used = str((entry or {}).get("used_range") or "")
    match = re.search(r"(\d+)\s*$", used)
    if match:
        try:
            return max(2, int(match.group(1)))
        except ValueError:
            pass
    return 2


# 수식 안의 맨 A1 범위. 시트 접두(`매출!`)가 붙은 건 이 시트 이야기가 아니라 건드리지 않는다.
_BARE_A1_RANGE = re.compile(r"(?<![!:\w$])(\$?)([A-Z]{1,3})(\$?)(\d{1,7}):(\$?)([A-Z]{1,3})(\$?)(\d{1,7})")
# 열 전체 참조(D:D). 시트 접두가 붙은 것은 제외.
_BARE_COL_RANGE = re.compile(r"(?<![!:\w$])\$?([A-Z]{1,3}):\$?([A-Z]{1,3})(?![\w$:])")


def formula_has_reversed_range(formula: str) -> bool:
    """`=AVERAGE(A2:A1)`처럼 시작 행이 끝 행보다 큰 범위 — 항상 플래너 오염이다."""
    for hit in _BARE_A1_RANGE.finditer(str(formula or "")):
        _c1, col1, _c2, row1, _c3, col2, _c4, row2 = hit.groups()
        if int(row1) > int(row2) or _column_index(col1) > _column_index(col2):
            return True
    return False


def formula_refers_beyond_used_columns(formula: str, entry: dict[str, Any] | None) -> bool:
    """수식이 참조하는 열이 전부 데이터 밖인 범위가 있는가 (=AVERAGE(E:E), 데이터는 A~D)."""
    last_col = _last_data_column(entry)
    if last_col < 1:
        return False
    text = str(formula or "")
    for hit in _BARE_A1_RANGE.finditer(text):
        cols = (_column_index(hit.group(2)), _column_index(hit.group(6)))
        if min(cols) > last_col:
            return True
    for hit in _BARE_COL_RANGE.finditer(text):
        cols = (_column_index(hit.group(1)), _column_index(hit.group(2)))
        if min(cols) > last_col:
            return True
    return False


def _last_data_column(entry: dict[str, Any] | None) -> int:
    """사용 범위의 마지막 열 번호. 모르면 0."""
    used = str((entry or {}).get("used_range") or "")
    match = re.search(r":\s*\$?([A-Z]{1,3})\$?\d+\s*$", used, re.IGNORECASE)
    return _column_index(match.group(1).upper()) if match else 0


def clamp_formula_to_used_range(formula: str, entry: dict[str, Any] | None) -> str:
    """데이터 끝을 넘는 **행**을 사용 범위까지 잘라낸다.

    2026-08-17 실측: 사용 범위가 A1:D4인 시트에 `=SUM(D2:D181)`이 들어갔다. 181은
    이 통합문서에 없는 숫자다 — 학습셋에 그대로 있는 리터럴이다(CLAUDE.md §3.4).
    합계는 어차피 맞게 나오지만 사용자가 수식을 열어 보면 틀린 표로 보이고,
    COUNT 계열은 실제로 값이 달라진다.

    **열까지 데이터 밖이면 손대지 않는다.** 처음엔 행만 보고 잘랐다가 회귀를 냈다:

        =VLOOKUP(A2,$F$2:$H$200,2,FALSE)   사용 범위 A1:C8

    F~H는 이 시트에 아예 없는 열이다. 즉 참조표 전체가 플래너의 추측이고, 검증기가
    그걸 거부해 "조회값 열이 필요합니다"라고 되묻고 있었다. 행만 보고 `$H$8`로
    다듬으니 그럴듯해져서 검증을 통과했고, **아무도 말하지 않은 F~H열로 실행됐다.**
    범위를 다듬는 일이 "이건 추측이다"라는 신호를 지워 버린 것이다.

    그래서 자르는 조건은 둘 다다 — 열이 데이터 안에 있고, 시작 행도 데이터 안에 있을 것.
    """
    text = str(formula or "")
    if not text.startswith("=") or not entry:
        return text
    used = str(entry.get("used_range") or "")
    if not re.search(r"\d\s*$", used):
        return text
    last_row = _last_data_row(entry)
    last_col = _last_data_column(entry)
    if last_row < 2 or last_col < 1:
        return text

    def _clip(hit: re.Match[str]) -> str:
        c1, col1, r1, row1, c2, col2, r2, row2 = hit.groups()
        start, end = int(row1), int(row2)
        if end <= last_row or start > last_row:
            return hit.group(0)
        if max(_column_index(col1), _column_index(col2)) > last_col:
            # 없는 열을 가리키고 있다. 다듬으면 추측이 사실처럼 보인다.
            return hit.group(0)
        return f"{c1}{col1}{r1}{start}:{c2}{col2}{r2}{last_row}"

    return _BARE_A1_RANGE.sub(_clip, text)


def _bind_named_formula(
    params: dict[str, Any], *, message: str, entry: dict[str, Any] | None
) -> tuple[list[str], bool]:
    """ "이익률 열에 매출이익 나누기 매출"을 =G2/F2 로 확정한다.

    돌려주는 두 번째 값은 해결 여부. 열을 못 찾으면 추측하지 않고 되묻게 한다.
    """
    raw = str(params.pop("named_formula_message", "") or "") or str(message or "")
    if not entry:
        return [], False
    headers = _headers(entry)
    parsed = parse_named_formula(raw, headers)
    if parsed is None:
        return [], False

    target_meta = _column_meta(entry, parsed.target)
    letters: list[str] = []
    for operand in parsed.operands:
        meta = _column_meta(entry, operand)
        letter = str(meta.get("letter") or "")
        if not letter:
            return [], False
        letters.append(letter)
    target_letter = str(target_meta.get("letter") or "")
    if not target_letter:
        return [], False

    start_row = 2
    end_row = _last_data_row(entry)
    formula = build_formula(parsed, letters, start_row)
    if not formula:
        return [], False
    params["range_ref"] = f"{target_letter}{start_row}:{target_letter}{end_row}"
    params["formula_a1"] = formula
    params.pop("formula_mode", None)
    return [f"range_ref={params['range_ref']}", f"formula_a1={formula}"], True


def _looks_like_ratio_column(entry: dict[str, Any] | None, letter: str) -> bool:
    """해당 열의 값이 0~1 비율로 저장돼 있는지 샘플로 판단한다."""
    if not entry or not letter:
        return False
    columns = entry.get("columns") or []
    offset = next((i for i, col in enumerate(columns) if str(col.get("letter") or "").upper() == letter), -1)
    if offset < 0:
        return False
    samples: list[float] = []
    for row in entry.get("sample_rows") or []:
        if offset >= len(row):
            continue
        try:
            samples.append(float(str(row[offset]).replace(",", "").rstrip("%")))
        except ValueError:
            continue
    return bool(samples) and all(0 <= v <= 1 for v in samples)


def _bind_message_only_slots(
    steps: list[PlanStep], *, message: str
) -> tuple[list[PlanStep], list[dict[str, Any]]]:
    """워크북 상태 없이도 원문만으로 확정할 수 있는 슬롯을 채운다."""
    bound: list[PlanStep] = []
    notes: list[dict[str, Any]] = []
    for step in steps:
        params = dict(step.params)
        changes: list[str] = []
        if step.action == "excel_live.write_range":
            changes.extend(_bind_write_values(params, message=message))
            # 여기에도 있어야 한다 — 머리글을 모르면 `bind_plan_steps`가 이 함수로
            # 조기 반환하므로(위 "머리글을 모르면…" 분기), 그쪽에만 검사를 두면
            # 실제로 타는 경로에서 그대로 통과한다(2026-08-17 실측: 검사가 True를
            # 돌려주는데도 셀에 '가장 큰 매출'이 그대로 써졌다).
            if write_values_echo_the_request(params, message):
                notes.append(
                    {
                        "action": step.action,
                        "slot": "values_2d",
                        "status": "unresolved",
                        "reason": "echoed_request",
                    }
                )
        # 머리글을 모르는 시트에서 "정렬 좀 해주세요" — 플래너가 학습셋 열 이름('이름')을 지어내 실행까지
        # 갔다(2026-08-19 ex15 v2 실측). 원문이 열을 말하지 않았으면 여기서도 미해결이다.
        if step.action in _REQUIRE_EXPLICIT_COLUMN and not _COLUMN_LETTER_MENTION.search(str(message or "")):
            for slot in _REQUIRE_EXPLICIT_COLUMN[step.action]:
                if not (_SORT_KEY_STATED.search(str(message or ""))):
                    notes.append({"action": step.action, "slot": slot, "status": "unresolved", "reason": "not_stated"})
        if changes:
            notes.append({"action": step.action, "status": "bound", "changes": changes})
        bound.append(PlanStep(action=step.action, params=params, reason=step.reason))
    return bound, notes


# 머리글을 모를 때 "X 기준", "X 순으로", "X별"처럼 기준 열을 **말로** 댔는지만 본다.
_SORT_KEY_STATED = re.compile(r"[가-힣A-Za-z0-9_()%]+\s*(?:기준|순으로|순서로|높은\s*순|낮은\s*순|많은\s*순|적은\s*순|큰\s*순|작은\s*순|오름차순|내림차순|별로|별)")


def bind_plan_steps(
    steps: list[PlanStep],
    *,
    digest: dict[str, Any],
    message: str,
    sheet_name: str | None,
) -> tuple[list[PlanStep], list[dict[str, Any]]]:
    """플랜의 상징적 파라미터를 실제 워크북 좌표로 확정한다.

    다이제스트를 못 읽었으면(빈 워크북·연결 실패) 아무것도 바꾸지 않는다 — 추측으로 덮어쓰는 게 더 위험하다.
    """
    original_entry = sheet_entry(digest, sheet_name)
    entry = original_entry
    headers_known = bool(entry and _headers(entry))
    if headers_known:
        # 활성 시트가 이전 턴의 Inventory여도, "지역별 매출"은 Sales_Data 이야기다.
        retargeted, _prefix = _retarget_sheet_by_headers(message, entry, digest, "")
        if retargeted is not None:
            entry = retargeted
            headers_known = bool(_headers(entry))
    retargeted_name = str(entry.get("name") or "").strip() if entry else ""
    original_name = str(original_entry.get("name") or "").strip() if original_entry else ""
    if not headers_known:
        # 머리글을 모르면 열 바인딩은 추측이 되므로 하지 않는다.
        # 다만 원문에만 있는 리터럴(쓰기 값)은 워크북 상태와 무관하게 채울 수 있다.
        return _bind_message_only_slots(steps, message=message)

    candidates = mentioned_headers(message, entry, digest)
    # 원문이 기준 열을 한 번도 말하지 않았는지. 말하지 않았다면 정렬/중복제거는 되물어야 한다.
    column_stated = bool(candidates) or bool(_COLUMN_LETTER_MENTION.search(str(message or "")))
    bound: list[PlanStep] = []
    notes: list[dict[str, Any]] = []
    created_sheet = ""

    for step in steps:
        params = dict(step.params)
        used: set[str] = set()
        changes: list[str] = []
        # 바인딩 중에 액션 자체가 바뀔 수 있다(쓰기 → 수식).
        step_action_override: str | None = None

        if not column_stated:
            for slot in _REQUIRE_EXPLICIT_COLUMN.get(step.action, ()):
                # 원문이 기준을 말하지 않았다. 플래너가 채워 둔 값은 학습 데이터에서
                # 튀어나온 이름일 수 있으므로 "값이 있다"는 이유로 지워선 안 된다.
                notes.append(
                    {
                        "action": step.action,
                        "slot": slot,
                        "status": "unresolved",
                        "reason": "not_stated",
                    }
                )

        range_slot = _WHOLE_TABLE_RANGE_SLOTS.get(step.action)
        if range_slot and not _EXPLICIT_RANGE_MENTION.search(str(message or "")):
            # 원문이 범위를 말하지 않았는데 플래너가 "A1:L37" 같은 범위를 지어내면
            # 표의 일부만 집계·정렬해 놓고 성공했다고 보고한다. 표 전체로 되돌린다.
            current = str(params.get(range_slot) or "").strip()
            if current and current != "__ACTIVE_SELECTION__":
                params[range_slot] = "__ACTIVE_SELECTION__"
                changes.append(f"{range_slot}=__ACTIVE_SELECTION__")

        if step.action == "excel_live.pivot_table":
            changes.extend(_bind_pivot(params, message=message, entry=entry))
            for slot in ("row_field", "column_field", "value_field"):
                if isinstance(params.get(slot), str):
                    used.add(str(params[slot]))

        if step.action == "excel_live.sort_range" and column_stated:
            sort_key = _pick_sort_key(message, _headers(entry))
            if sort_key and sort_key != params.get("key_column"):
                params["key_column"] = sort_key
                changes.append(f"key_column={sort_key}")
                used.add(sort_key)

        if (
            step.action in {"excel_live.apply_data_bar", "excel_live.apply_color_scale"}
            and len(candidates) == 1
        ):
            # "평균운행시간 열에 데이터 막대 넣어줘" — 열 이름을 말했는데 표 전체에
            # 칠하면, 두 열에 각각 요청해도 같은 범위에 두 번 그린 셈이 된다
            # (2026-08-18 사람 말투 실측: 노선 시트 데이터 막대 2건이 1건으로).
            letter = _letter_for(entry, candidates[0])
            current = str(params.get("target_range") or "").strip().upper()
            span = re.match(r"(?:[^!]+!)?([A-Z]+)\d*:([A-Z]+)\d*$", current)
            is_placeholder = current in {"", "__ACTIVE_SELECTION__", "__USED_RANGE__"}
            if letter and (is_placeholder or (span and span.group(1) != span.group(2))):
                last_row = _last_data_row(entry)
                if last_row >= 2:
                    params["target_range"] = f"{letter}2:{letter}{last_row}"
                    changes.append(f"target_range={params['target_range']}")
                    used.add(candidates[0])

        for slot in _COLUMN_SLOTS.get(step.action, ()):  # 단일 열 슬롯
            if slot not in params:
                # 플래너가 기준 열을 아예 빼먹으면 검증기가 1번 열로 채워버린다.
                # 원문이 열을 말했다면 여기서 채우고, 아니면 미해결로 보고해 되묻는다.
                if slot not in _REQUIRED_COLUMN_SLOTS.get(step.action, ()):
                    continue
                params[slot] = None
            if params.get(slot) is None and slot == "column_field":
                continue
            before = params[slot]
            resolved, reason = _resolve_column_value(before, entry=entry, candidates=candidates, used=used)
            if isinstance(resolved, str):
                used.add(resolved)
            if reason == "unresolved":
                notes.append({"action": step.action, "slot": slot, "status": "unresolved"})
            elif resolved != before:
                params[slot] = resolved
                changes.append(f"{slot}={resolved}")

        for slot in _COLUMN_LIST_SLOTS.get(step.action, ()):
            raw_list = params.get(slot)
            if not isinstance(raw_list, list) or not raw_list:
                # "코드 열 기준으로 중복 제거"처럼 원문이 기준을 말했으면 채우고,
                # "중복 없애줘"처럼 기준이 없으면 임의로 정하지 말고 미해결로 보고한다.
                if candidates:
                    params[slot] = [candidates[0]]
                    changes.append(f"{slot}=[{candidates[0]}]")
                elif step.action not in _OPTIONAL_COLUMN_LIST_ACTIONS:
                    notes.append({"action": step.action, "slot": slot, "status": "unresolved"})
                continue
            resolved_list: list[Any] = []
            for item in raw_list:
                resolved, _reason = _resolve_column_value(item, entry=entry, candidates=candidates, used=used)
                if isinstance(resolved, str):
                    used.add(resolved)
                resolved_list.append(resolved)
            if resolved_list != raw_list:
                params[slot] = resolved_list
                changes.append(f"{slot}={resolved_list}")

        if step.action == "excel_live.filter_rows":
            changes.extend(_bind_filter_value(params, message=message, entry=entry))
            changes.extend(_bind_filter_mode(params, message=message))

        if step.action == "excel_live.write_range":
            changes.extend(_bind_write_values(params, message=message))
            # 무엇을 쓸지 못 정했으면 추측해서 빈 칸을 쓰지 않는다 — 되묻는 쪽이 맞다.
            # 시킨 말을 값으로 쓰려 한다면, 그게 집계 요청인지 먼저 본다.
            # 여기서는 다이제스트가 있어 "매출"이 몇 번 열인지 안다 —
            # 빠른 규칙은 그걸 몰라서 되묻는 데서 멈출 수밖에 없었다.
            plan_already_has_formulas = any(s.action == "excel_live.set_formula" for s in steps)
            if write_values_echo_the_request(params, message) and not plan_already_has_formulas:
                # 계획에 수식 단계가 이미 있으면 이 쓰기는 집계 줄의 **이름표**다.
                # 변환하면 "합계" 라벨이 =SUM(A2:A6)이 된다(2026-08-18 지저분판
                # 실측: 글자 열 A7에 텍스트 합계 대신 0짜리 수식이 들어갔다).
                aggregate = build_aggregate_formula(message, entry=entry, digest=digest)
                if aggregate:
                    target = _top_left_of(params.get("start_cell"))
                    if target:
                        step_action_override = "excel_live.set_formula"
                        params = {"range_ref": target, "formula_a1": aggregate}
                        changes.append(f"formula_a1={aggregate}")
            if (
                step_action_override is None
                and write_values_are_empty(params)
                and not _CLEARING_INTENT.search(str(message or ""))
            ):
                notes.append({"action": step.action, "slot": "values_2d", "status": "unresolved"})
            elif step_action_override is None and write_values_echo_the_request(params, message):
                # 값이 **있는데 그게 지시문**인 경우다. 낡음 필터는 "값이 채워졌으면
                # 해결된 것"으로 보므로, 사유를 붙여 걸러지지 않게 한다.
                notes.append(
                    {
                        "action": step.action,
                        "slot": "values_2d",
                        "status": "unresolved",
                        "reason": "echoed_request",
                    }
                )

        if step.action == "excel_live.set_formula" and (
            params.get("named_formula_message") or str(params.get("formula_mode") or "") == "named"
        ):
            formula_changes, resolved = _bind_named_formula(params, message=message, entry=entry)
            if resolved:
                changes.extend(formula_changes)
            else:
                notes.append({"action": step.action, "slot": "formula_a1", "status": "unresolved"})

        if (step_action_override or step.action) == "excel_live.set_formula":
            formula_text = str(params.get("formula_a1") or "")
            if formula_has_reversed_range(formula_text):
                # =AVERAGE(A2:A1) — 뒤집힌 범위는 항상 플래너 오염이다. 2026-08-17
                # 배터리 실측: 이게 A1:A8에 적용돼 **날짜 열이 통째로 덮였다.**
                # 대상 range_ref까지 오염돼 있으므로 고쳐 쓰지 않고 되묻는다.
                notes.append(
                    {
                        "action": step.action,
                        "slot": "formula_a1",
                        "status": "unresolved",
                        "reason": "degenerate_range",
                    }
                )
            elif formula_refers_beyond_used_columns(formula_text, entry):
                # =AVERAGE(E:E)인데 데이터는 A~D뿐 — 근거 없는 열이다. 문장이 말한
                # 머리글("금액")로 다시 세울 수 있으면 세운다. 못 세우면 그대로 둔다
                # — 참조표처럼 **일부러** 빈 영역을 가리키는 수식(VLOOKUP)이 있다.
                rebuilt = build_aggregate_formula(message, entry=entry, digest=digest)
                if rebuilt:
                    params["formula_a1"] = rebuilt
                    changes.append(f"formula_a1={rebuilt}")
            # 학습 리터럴로 들어온 범위를 데이터 끝까지로 자른다.
            clipped = clamp_formula_to_used_range(str(params.get("formula_a1") or ""), entry)
            if clipped and clipped != params.get("formula_a1"):
                params["formula_a1"] = clipped
                changes.append(f"formula_a1={clipped}")

        if step.action == "excel_live.find_replace" and find_replace_erases_data(params, message):
            # 바꿀 말이 비었다 = 찾은 글자를 지운다. 지우라고 하지 않았으면 계획이 뒤집힌 것이다.
            notes.append(
                {
                    "action": step.action,
                    "slot": "replace_text",
                    "status": "unresolved",
                    "reason": "empty_replacement",
                }
            )

        if step.action in _CONDITION_FORMAT_ACTIONS:
            # 조건부 강조는 범위에 `재고!E:E` 접두를 붙이는 경로다. 이미 옮긴
            # entry를 넘기면 접두가 비어 활성 시트(매출)의 E열을 칠한다.
            changes.extend(
                _bind_condition_format(params, message=message, entry=original_entry, digest=digest)
            )

        if step.action == "excel_live.consolidate_sheets":
            changes.extend(_bind_consolidate(params, message=message, digest=digest))

        if step.action in _OUTPUT_SHEET_ACTIONS:
            changes.extend(
                _normalize_output_sheet(
                    params,
                    message=message,
                    source_sheet=str(entry.get("name") or ""),
                    action=step.action,
                )
            )

        if (
            retargeted_name
            and retargeted_name != original_name
            and step.action not in _WRITE_INTO_NEW_SHEET_ACTIONS
            and step.action != "excel_live.create_sheet"
            and not str(params.get("sheet_name") or "").strip()
        ):
            params["sheet_name"] = retargeted_name
            changes.append(f"sheet_name={retargeted_name}")

        changes.extend(_bind_created_sheet_target(step.action, params, created_sheet=created_sheet))
        if step.action == "excel_live.create_sheet":
            created_sheet = str(params.get("sheet_name") or "").strip() or created_sheet

        # 미해결 보고는 슬롯 순회 시점에 남는데, 그 뒤 단계에서 채워지는 슬롯이 있다.
        # 예: "완료된 것만 남겨줘"는 열을 말하지 않았지만 값 '완료'가 상태 열에만
        # 있으므로 _bind_filter_value가 기준 열을 특정한다. 이미 정해진 슬롯을
        # 미해결로 남겨 두면 물어볼 필요가 없는 걸 묻게 된다.
        notes = [note for note in notes if not _is_stale_unresolved(note, step.action, params)]

        final_action = step_action_override or step.action
        if (
            retargeted_name
            and retargeted_name != original_name
            and not str(params.get("sheet_name") or "").strip()
            and final_action in _SHEET_RETARGET_ACTIONS
        ):
            # 머리글로 시트를 되찾았으면 계획에도 실어야 한다 — 안 실으면
            # 실행이 활성 시트에서 돈다(2026-08-18 멀티턴 사냥: "금액 기준
            # 내림차순"이 요약 시트를 정렬했다).
            params["sheet_name"] = retargeted_name
            changes.append(f"sheet_name={retargeted_name}")
        if changes:
            notes.append({"action": final_action, "status": "bound", "changes": changes})
        bound.append(PlanStep(action=final_action, params=params, reason=step.reason))

    return bound, notes


# 말로 시킨 집계 → 함수. 순서가 중요하다: "가장 큰"이 "큰"보다 먼저 걸려야 한다.
_AGGREGATE_FUNCS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(가장\s*(큰|높|많)|최댓값|최대값|최고값)"), "MAX"),
    (re.compile(r"(가장\s*(작은|낮|적)|최솟값|최소값|최저값)"), "MIN"),
    (re.compile(r"(평균|average)", re.IGNORECASE), "AVERAGE"),
    (re.compile(r"(개수|건수|몇\s*건|몇\s*개|세는|세어)"), "COUNTA"),
    (re.compile(r"(합계|다\s*더|모두\s*더|전부\s*더|더한|더해|합산|총합)"), "SUM"),
)
# 조건이 붙은 집계는 기준 열과 값이 더 필요하다. 여기서 만들지 않고 되묻게 둔다.
_CONDITIONAL_AGGREGATE = re.compile(r"(만\s|만의|별로|별\s|이상|이하|초과|미만|넘|같은|조건)")


def _top_left_of(cell: Any) -> str:
    """`F7` / `F7:G9` 어느 쪽이 와도 왼쪽 위 한 칸을 준다. 집계 결과는 한 칸이다."""
    text = str(cell or "").strip().upper().replace("$", "")
    if not text:
        return ""
    head = text.split(":")[0]
    return head if re.fullmatch(r"[A-Z]{1,3}\d{1,7}", head) else ""


def build_aggregate_formula(
    message: str, *, entry: dict[str, Any] | None, digest: dict[str, Any] | None
) -> str:
    """ "가장 큰 매출 값 넣어줘" → "=MAX(B2:B6)".

    바인더에서만 만들 수 있다 — 빠른 규칙은 다이제스트를 못 봐서 "매출"이 몇 번
    열인지 모른다(2026-08-17: 그래서 되묻는 데서 멈췄다).

    조건이 붙은 집계("서울 지역만")는 만들지 않는다. 기준 열과 값이 더 필요한데
    잘못 짚으면 엉뚱한 숫자가 조용히 들어간다 — 그럴 땐 되묻는 편이 낫다.
    """
    text = str(message or "")
    if not text or not entry or _CONDITIONAL_AGGREGATE.search(text):
        return ""
    func = next((f for pattern, f in _AGGREGATE_FUNCS if pattern.search(text)), "")
    if not func:
        return ""
    headers = mentioned_headers(text, entry, digest or {})
    if len(headers) != 1:
        # 열을 하나로 못 좁히면 추측하지 않는다.
        return ""
    letter = str(_column_meta(entry, headers[0]).get("letter") or "")
    if not letter:
        return ""
    return f"={func}({letter}2:{letter}{_last_data_row(entry)})"


def _is_stale_unresolved(note: dict[str, Any], action: str, params: dict[str, Any]) -> bool:
    """이 단계에서 결국 채워진 슬롯의 미해결 보고인지."""
    if note.get("status") != "unresolved" or note.get("action") != action:
        return False
    if note.get("reason") == "echoed_request":
        # 값이 채워져 있다는 게 바로 문제다 — 그 값이 시킨 말 자체다.
        # 여기서 "채워졌으니 해결됨"으로 지우면 설명문이 셀에 그대로 들어간다.
        return False
    if note.get("reason") in {"degenerate_range", "empty_replacement"}:
        # 값이 있어도 그 값 자체가 오염이다(뒤집힌 범위, 빈 치환).
        return False
    if note.get("reason") == "not_stated":
        # 원문이 기준 열을 말하지 않았다. 플래너가 채운 값도, 그 값을 다듬은 결과도
        # 근거가 될 수 없다("Qty" → "QTY"처럼 학습 데이터의 열 이름이 그대로 나온다).
        # 데이터가 조용히 뒤섞이느니 되묻는 편이 낫다.
        return False
    value = params.get(str(note.get("slot") or ""))
    if isinstance(value, list):
        return bool(value)
    return value is not None and value != ""


# 앞 단계에서 만든 시트에 이어서 써야 하는 액션. 원본을 읽어 집계하는 액션은 제외한다.
_WRITE_INTO_NEW_SHEET_ACTIONS = frozenset(
    {
        "excel_live.write_range",
        "excel_live.set_formula",
        "excel_live.fill_range",
        "excel_live.apply_border",
        "excel_live.create_table",
    }
)


def _bind_created_sheet_target(action: str, params: dict[str, Any], *, created_sheet: str) -> list[str]:
    """ "Summary 시트 만들어서 A1에 ... 쓰고" 의 두 번째 단계가 갈 곳.

    시트를 만들면 활성 시트가 따라 옮겨갈 거라 기대하지만 실제로는 그렇지 않다.
    그대로 두면 write_range가 원본 시트 A1에 떨어져 머리글을 덮어쓴다. 되돌리기 전까지
    사용자는 "Summary 시트를 만들었습니다"라는 성공 메시지만 본다.
    """
    if not created_sheet or action not in _WRITE_INTO_NEW_SHEET_ACTIONS:
        return []
    if str(params.get("sheet_name") or "").strip():
        return []
    params["sheet_name"] = created_sheet
    return [f"sheet_name={created_sheet}"]
