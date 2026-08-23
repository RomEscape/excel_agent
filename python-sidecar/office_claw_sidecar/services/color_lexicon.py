"""색 낱말 사전 — **한 곳**. 이름에서 헥사로, 그리고 그 이름들로 만든 정규식.

같은 어휘를 두 곳에 두면 반드시 갈라진다. 실제로 갈라져 있었다(2026-08-24 실측):

    낱말      규칙표            통역
    남색      #002060           #1E6B4F  ← **초록이다**
    회색      #808080           #D9D9D9
    보라색    #7030A0           #7B61FF
    주황색    #ED7D31           #FFA500
    분홍·하늘·갈색·퍼플·오렌지·블랙·네이비   있음    **없음**

게다가 정규식과 변환 함수가 서로 다른 목록을 갖고 있어, 패턴은 `흰`을 잡는데
함수는 몰라서 폴백으로 떨어졌다 — **"흰 글씨"가 노란 글씨가 됐다.**
그래서 여기서는 **정규식을 사전에서 만든다.** 둘이 어긋날 수가 없다.

값은 규칙표(`_quick_color_hex`) 것을 정본으로 삼는다 — 대시보드 실측으로 다듬어
왔고 게이트가 그 값으로 통과해 온 쪽이다(2026-08-18 남색 사고 이후).
"""

from __future__ import annotations

import re

#: 이름 → 헥사. 별칭을 모두 적는다 — 사람은 "빨간색"도 "빨갛"도 쓴다.
#: 새 색을 넣으면 정규식·통역·규칙표가 **동시에** 알게 된다.
COLOR_HEX: dict[str, str] = {}


def _add(hex_code: str, *names: str) -> None:
    for name in names:
        COLOR_HEX[name] = hex_code


_add("#FFFF00", "노란색", "노랑", "노란", "노랗", "yellow")
_add("#FF4D4F", "빨간색", "빨강", "빨간", "빨갛", "red")
_add("#4F8CFF", "파란색", "파랑", "파랗", "blue")
_add("#6AC36A", "초록색", "초록", "green")
# `흰` 하나만 오는 꼴("흰 글씨")도 넣는다 — 예전엔 정규식만 잡고 함수가 몰라
# 폴백(노란색)으로 떨어졌다(2026-08-24 실측).
_add("#FFFFFF", "흰색", "흰", "하얀색", "하얀", "하양", "하얗", "white", "화이트", "백색")
_add("#000000", "검은색", "검정", "검은", "까맣", "black", "블랙")
_add("#002060", "남색", "네이비", "navy", "진파랑", "진한 파랑", "진한파랑")
_add("#D9D9D9", "연회색", "연한 회색", "연한회색")
_add("#808080", "회색", "그레이", "gray", "grey")
_add("#ED7D31", "주황색", "주황", "오렌지", "orange")
_add("#7030A0", "보라색", "보라", "퍼플", "purple")
_add("#FFC0CB", "분홍색", "분홍", "핑크", "pink")
_add("#9DC3E6", "하늘색", "하늘")
_add("#843C0C", "갈색", "브라운", "brown")

_HEX_LITERAL = re.compile(r"#[0-9a-fA-F]{6}")

#: 사전에서 **만든** 정규식. 긴 이름을 앞에 둬야 "노란색"이 "노란"으로 잘리지 않는다.
#: 한국어는 낱말 경계가 없어 `\b`를 못 쓴다 — 길이순 정렬이 그 역할을 한다.
COLOR_TOKEN_PATTERN = re.compile(
    r"(#[0-9a-fA-F]{6}|"
    + "|".join(re.escape(name) for name in sorted(COLOR_HEX, key=len, reverse=True))
    + r")",
    re.IGNORECASE,
)


def color_hex(word: str, *, default: str = "") -> str:
    """색 이름 → `#RRGGBB`. 모르는 낱말이면 `default`(기본은 빈 문자열).

    **모르는 색을 노란색으로 칠하면 안 된다.** 못 알아들은 색은 매핑 실패로 두고
    되묻는 편이 낫다 — 조용히 엉뚱한 색을 칠하면 사용자가 알아채기 어렵다.
    옛 규칙표는 폴백이 노란색이었고, 그래서 "흰 글씨"가 노란 글씨가 됐다.
    """
    token = str(word or "").strip().lower()
    if _HEX_LITERAL.fullmatch(token):
        return token.upper()
    return COLOR_HEX.get(token, default)
