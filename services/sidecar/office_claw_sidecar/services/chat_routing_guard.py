"""엑셀 명령 경로의 입구 게이트 — LLM을 부르기 **전에** 결정론으로 거른다.

왜 여기 있는가:
    라우팅 기본값을 "워크북이 열려 있으면 엑셀 경로"로 뒤집으면, 이제 **모든 문장이**
    /excel-live/command로 들어온다. 그런데 이 엔드포인트에는 안전 계층이 없었다 —
    `is_denied_intent`도 마스킹도 /agent/chat 전용이다(2026-08-16 조사).
    그리고 `_build_generic_excel_follow_up`은 catch-all이라 무슨 입력에나 엑셀 되묻기를
    돌려준다. 실측으로 "우울해 죽고 싶어"에도 "어떤 작업을 원하시는지 한 단계만 더
    구체화해 주세요"를 돌려줬다.

두 가지만 판정한다:
    1. `detect_crisis_intent` — 자해·자살 신호. 엑셀 되묻기로 받으면 안 되는 입력.
    2. `classify_off_topic` — 엑셀 일이 아닌 요청. 일반 채팅으로 내려보낸다.

설계 원칙:
    - **LLM을 부르지 않는다.** 게이트가 모델을 부르면 잡담 한 마디가 플래너 왕복
      비용을 낸다. 결정론 규칙만 쓴다.
    - **놓치는 쪽이 낫다(fail-open).** 위기 판정이 과하면 "이 행 죽여버려" 같은 정상
      편집이 막힌다. 판정은 좁게 잡고, 애매하면 엑셀 경로로 보낸다.
    - `_looks_like_excel_request`를 업무 외 필터로 재사용하면 안 된다 —
      "파이썬에서 리스트 정렬하는 법 알려줘"에 True를 준다(조사 실측).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# ── 1. 위기 신호 ────────────────────────────────────────────────────────────
# 좁게 잡는다. 엑셀 문맥에서 "지워/죽여/없애"는 셀·행·시트를 가리키는 일상어다.
# 따라서 **1인칭 자기 지시**가 붙은 형태만 본다.
_CRISIS_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p)
    for p in (
        r"죽고\s*싶",
        r"자살",
        r"자해",
        r"살기\s*싫",
        r"살고\s*싶지\s*않",
        r"사라지고\s*싶",
        r"내가\s*죽",
        r"목숨을\s*끊",
        r"세상을\s*떠나고\s*싶",
    )
)

# 위기어처럼 보이지만 엑셀 작업인 문장. 이게 걸리면 위기 판정을 취소한다.
_CRISIS_FALSE_FRIENDS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p)
    for p in (
        r"(행|열|셀|시트|범위|표|데이터|값|수식|파일)\s*(을|를)?\s*(지워|삭제|없애|죽여)",
        r"(지워|삭제|없애)\s*(주|줘|해)",
    )
)

CRISIS_REPLY = (
    "그런 마음이 든다니 많이 힘드신 것 같아 걱정됩니다. 혼자 감당하지 마시고 "
    "주변 사람이나 전문 상담에 꼭 연락해 주세요 — 자살예방상담전화 109, "
    "정신건강상담전화 1577-0199는 24시간 통화할 수 있습니다.\n"
    "괜찮아지시면 그때 엑셀 작업은 다시 도와드리겠습니다."
)


def detect_crisis_intent(message: str) -> bool:
    """자해·자살 신호가 있으면 True. 엑셀 되묻기로 받으면 안 되는 입력이다."""
    text = str(message or "")
    if not any(p.search(text) for p in _CRISIS_PATTERNS):
        return False
    # "필요 없는 시트 죽여줘"처럼 대상이 통합문서면 위기가 아니다.
    return not any(p.search(text) for p in _CRISIS_FALSE_FRIENDS)


# ── 2. 업무 외 판정 ─────────────────────────────────────────────────────────
# 엑셀 어휘가 하나도 없으면서 다른 도메인 어휘가 뚜렷한 문장만 잡는다.
_OFF_TOPIC_MARKERS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p)
    for p in (
        r"(파이썬|자바|자바스크립트|c\+\+|리액트|sql\s*문법)\s*(으?로|에서|코드|짜|작성)",
        # 사이에 조사·부사가 낀다("자기소개서 좀 써줘"). 단독 '시'는 '시트/시작'과
        # 겹쳐 오탐이 크므로 넣지 않는다.
        r"(에세이|소설|자기소개서|이력서|자소서|보고서 초안)\s*\S{0,4}?\s*(써|작성|만들|지어)",
        r"(날씨|기온|미세먼지)\s*(어때|알려|어떻)",
        r"(뉴스|정치|대통령|선거|주가|환율|코인)\s*(어때|알려|어떻|전망)",
        r"(번역|통역)\s*(해|좀|해줘)",
        r"(레시피|요리법|맛집|여행지)\s*(알려|추천)",
        r"너\s*(는)?\s*누구",
        r"시스템\s*프롬프트",
        r"이전\s*지시.*(무시|잊)",
    )
)

# 이 중 하나라도 있으면 엑셀 일로 본다 — 업무 외 판정을 취소한다.
_EXCEL_MARKERS = re.compile(
    r"엑셀|excel|워크북|workbook|시트|sheet|셀\b|cell|수식|함수|표|테이블|table|"
    r"차트|그래프|피벗|필터|정렬|합계|평균|집계|서식|테두리|병합|열|행|범위|"
    r"[A-Za-z]{1,3}\d{1,7}"
)


# ── 3. 스몰토크 판정 ────────────────────────────────────────────────────────
# 인사·감사·작별·기분·목적어 없는 희망 표현·능력 질문 — 명령이 아니라 대화다.
# 이게 없어서 "안녕하세요"가 계획 전용 플래너에 도달해 list_workbooks가 나오고,
# "다 지워버리고 싶네"(푸념)가 clear_range 퀵룰에 명중했다(2026-09-01 스모크
# 12문 실측). 엑셀 어휘가 하나라도 있으면 이 판정 전에 엑셀 경로로 확정되므로
# ("고마워, B열 정렬해줘"는 엑셀), 여기 걸리는 문장은 어휘상 순수 대화뿐이다.
_SMALLTALK_MARKERS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p)
    for p in (
        r"^\s*(안녕|안뇽|하이|헬로|hello|hi|ㅎㅇ|ㅎ2)[!~.\s]*$",
        r"^\s*안녕하세요",
        r"^\s*(좋은\s*(아침|하루)|굿모닝|굿밤)",
        r"^\s*(고마워|고맙|감사합|감사해|감사히|땡큐|thank|thx)",
        r"(수고했|수고하셨|수고해|잘\s*자|잘자|안녕히|내일\s*(봐|보자)|퇴근할게|이만\s*갈게)",
        r"^\s*(심심|피곤|졸리|졸려|배고파|배고프|힘들다|힘드네)",
        r"(점심|저녁|아침)\s*(은|을|때)?\s*뭐\s*먹",
        r"뭐\s*먹을까",
        r"(너|넌|너는)\s*뭐\s*(를)?\s*할\s*수\s*있",
        r"무엇을\s*할\s*수\s*있",
        r"어떤\s*(기능|일|작업)\s*(이|을|를)?\s*(할\s*수\s*있|가능|되)",
        r"(이|요|저)\s*(프로그램|앱|서비스)\s*(은|는|을|를)?\s*어떻게\s*(써|쓰는|사용)",
        # 목적어 없는 희망·푸념 — "다 지워버리고 싶네"는 명령이 아니다. 대상을
        # 부른 문장("이 표 다 지워줘")은 엑셀 어휘에 먼저 걸려 여기 안 온다.
        r"(다\s*지워\s*버리고\s*싶|때려\s*치우고\s*싶|집에\s*가고\s*싶"
        r"|쉬고\s*싶|그만\s*두고\s*싶|일하기\s*싫|하기\s*싫다)",
    )
)


@dataclass(frozen=True)
class RouteVerdict:
    """이 문장을 엑셀 경로에서 처리해도 되는가."""

    off_topic: bool
    why: str = ""

    @property
    def excel_ok(self) -> bool:
        return not self.off_topic


def classify_off_topic(message: str) -> RouteVerdict:
    """엑셀 작업으로 볼 수 없는 요청이면 off_topic=True.

    애매하면 엑셀로 보낸다 — 오판으로 정상 편집을 막는 쪽이 더 나쁘다.
    """
    text = str(message or "").strip()
    if not text:
        return RouteVerdict(off_topic=False)
    if _EXCEL_MARKERS.search(text):
        return RouteVerdict(off_topic=False)
    lowered = text.lower()
    for pattern in _OFF_TOPIC_MARKERS:
        hit = pattern.search(lowered)
        if hit:
            return RouteVerdict(off_topic=True, why=hit.group(0)[:40])
    for pattern in _SMALLTALK_MARKERS:
        hit = pattern.search(lowered)
        if hit:
            return RouteVerdict(off_topic=True, why=f"smalltalk:{hit.group(0)[:32]}")
    return RouteVerdict(off_topic=False)
