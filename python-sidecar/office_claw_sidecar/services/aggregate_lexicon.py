"""집계 낱말 사전 — **한 곳**. 낱말에서 엑셀 함수로, 그리고 그 낱말들로 만든 정규식.

색 사전과 같은 이유로 모은다(2026-08-24). 실제로 갈라져 있었다:

    낱말      라우터    에이전트   바인더/보정
    개수      COUNT     COUNT      **COUNTA**
    최대      (없음)    MAX        MAX
    소계      SUM       (없음)     (없음)

`COUNT`와 `COUNTA`는 **다른 답을 낸다** — COUNT는 숫자만 세므로 글자가 든 열에서 0이다.
"상태 개수"를 어느 경로가 처리하느냐에 따라 답이 달라졌다. 테스트는 일관되게
COUNTA를 기대하므로(`test_aggregate_binding.py:49` 등) 그쪽으로 통일한다.

그리고 여기서도 **정규식을 사전에서 만든다.** 예전엔 낱말 목록과 변환표가 따로여서,
색 쪽에서는 그 어긋남이 실제 사고("흰 글씨" → 노란 글씨)로 나왔다.

남은 중복: `excel_param_binder._AGGREGATE_FUNCS`와
`excel_correction_context._AGG_WORDS`는 **순서가 의미를 갖는 정규식 목록**이라
("가장 큰"이 "큰"보다 먼저 걸려야 한다) 여기 합치지 않았다. 값은 이미 일치한다.
"""

from __future__ import annotations

import re

#: 낱말 → 엑셀 함수. 별칭을 모두 적는다.
AGG_FUNC: dict[str, str] = {}


def _add(func: str, *words: str) -> None:
    for word in words:
        AGG_FUNC[word] = func


_add("SUM", "합계", "총합계", "총합", "총계", "소계", "합", "합산", "sum")
_add("AVERAGE", "평균", "average", "avg")
_add("MAX", "최대", "최댓값", "최대값", "최고값", "max")
_add("MIN", "최소", "최솟값", "최소값", "최저값", "min")
# **COUNT가 아니라 COUNTA다.** COUNT는 숫자만 세므로 글자가 든 열에서 0을 낸다 —
# "상태 개수"를 묻는 사람이 원하는 답이 아니다.
_add("COUNTA", "개수", "건수", "카운트", "count", "counta")

#: 사전에서 **만든** 정규식. 긴 낱말이 앞이어야 "총합계"가 "총합"으로 잘리지 않는다
#: — 한국어는 낱말 경계가 없어 `\b`를 못 쓴다.
AGG_WORD_PATTERN = re.compile(
    "|".join(re.escape(word) for word in sorted(AGG_FUNC, key=len, reverse=True)),
    re.IGNORECASE,
)


def aggregate_func(word: str, *, default: str = "") -> str:
    """집계 낱말 → 함수 이름. 모르는 낱말이면 `default`(기본은 빈 문자열).

    **모르는 낱말을 SUM으로 치면 안 된다.** 엉뚱한 수식을 조용히 넣느니 매핑 실패로
    두고 되묻는 편이 낫다 — 색 사전이 모르는 색을 노란색으로 칠하던 것과 같은 부류다.
    """
    return AGG_FUNC.get(str(word or "").strip().lower(), default)
