"""표시 형식 낱말 사전 — **한 곳**. 개념어에서 엑셀 서식 코드로.

색·집계와 같은 이유로 모은다(2026-08-24). 같은 낱말이 셋으로 갈라져 있었다:

    낱말      라우터        통역      검증기
    퍼센트    `0.0%`        `0%`      `0.00%`
    통화      `"₩"#,##0`    `"₩"#,##0`  `#,##0`  ← 기호가 사라진다

퍼센트는 **같은 문장**("퍼센트로 보여줘")이 어느 층에서 풀리느냐로 답이 달라졌다
(`test_phrasing_robustness.py:55`는 `0.0%`, `test_excel_live_new_tools.py:205`는
`0.00%`를 고정하고 있었다 — 둘 다 같은 문장이다). 정당한 차이가 아니라 갈라짐이다.

정본은 **라우터 값**으로 삼는다. 문장 규칙이 먼저 보는 경로라 실사용에서 가장 많이
닿고, 게이트·말투 테스트가 그 값으로 통과해 왔다.

여기 없는 것: 라우터의 `_NUMBER_FORMAT_HINTS`는 "소수점 세 자리" 같은 **자릿수
표현을 정규식으로** 읽는다. 그건 낱말→값 표가 아니라 파싱이라 그대로 뒀다.
"""

from __future__ import annotations

#: 개념어 → 엑셀 서식 코드. 별칭을 모두 적는다.
FORMAT_CODE: dict[str, str] = {}


def _add(code: str, *words: str) -> None:
    for word in words:
        FORMAT_CODE[word] = code


_add("#,##0", "천단위", "천 단위", "천단위구분", "쉼표", "콤마", "comma", "thousand", "숫자")
# 소수 자릿수를 말하지 않은 "퍼센트"는 **한 자리**다 — 라우터 값이 정본이다.
_add("0.0%", "퍼센트", "백분율", "percent", "percentage")
_add('"₩"#,##0', "통화", "원화", "currency", "krw")
_add('#,##0"원"', "원")
_add("yyyy-mm-dd", "날짜", "date")
_add("0.00", "소수점", "decimal")
_add("@", "텍스트", "text")
_add("General", "일반", "general")


def format_code(word: str, *, default: str = "") -> str:
    """개념어 **한 낱말** → 서식 코드. 모르면 `default`(기본은 빈 문자열).

    모르는 말을 짐작해 서식을 씌우면 숫자가 엉뚱하게 보인다 — 되묻는 편이 낫다.
    """
    return FORMAT_CODE.get(str(word or "").strip().lower(), default)


def format_code_in_text(text: str, *, default: str = "") -> str:
    """문구 **안에서** 개념어를 찾아 서식 코드로. `"천 단위 콤마"`처럼 여러 낱말이 섞인다.

    모델이 내는 option은 한 낱말이 아니다 — 정확일치만 보면 `"천 단위 콤마"`가
    통째로 미매핑이 된다(2026-08-24에 이걸로 한 번 깼다).
    긴 낱말을 먼저 본다: `"원화"`가 `"원"`으로 잘리면 `#,##0"원"`이 돼 ₩가 사라진다.
    """
    lowered = str(text or "").strip().lower()
    if not lowered:
        return default
    exact = FORMAT_CODE.get(lowered)
    if exact:
        return exact
    for word in sorted(FORMAT_CODE, key=len, reverse=True):
        if word in lowered:
            return FORMAT_CODE[word]
    return default
