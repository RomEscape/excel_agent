"""
머리글 사전 — 사용자가 말한 개념어를 시트의 실제 머리글로 잇는 레이어.

실무 파일의 머리글은 `Region`, `Gross_Profit`, `Reorder_Point`처럼 영문인 경우가 흔한데
사용자는 "지역별", "매출이익", "재주문점"이라고 말한다. 문자열이 겹치지 않으니
문자 유사도(difflib)로는 절대 못 잇고, 그 결과 열 기준이 미해결로 떨어지거나
1번 열이 임의로 선택된다.

이 모듈은 개념 단위 동의어 묶음을 소유하고 두 가지 질문에 답한다.
- `resolve_header(term, headers)`: "매출이익" → "Gross_Profit"
- `find_header_mentions(message, headers)`: 원문에서 머리글이 언급된 위치·표현 목록

상태 없는 순수 함수 모듈이며, 바인더/라우터가 이 함수들만 쓴다.
"""

from __future__ import annotations

import difflib
import re
from typing import Any

# 개념 하나에 대한 표현 묶음. 한국어와 영어를 같은 줄에 둔다.
# 앞쪽 표현일수록 구체적이어야 한다("매출이익"이 "매출"보다 먼저 잡혀야 한다).
_CONCEPT_GROUPS: tuple[tuple[str, ...], ...] = (
    ("주문번호", "주문id", "주문 번호", "order_id", "orderid", "order no", "전표번호"),
    ("주문일자", "주문일", "주문 날짜", "order_date", "orderdate", "일자", "날짜", "date"),
    ("시작일", "착수일", "start_date", "startdate"),
    ("마감일", "마감", "종료일", "납기", "due_date", "duedate", "deadline"),
    ("고객", "고객사", "거래처", "customer", "client", "account"),
    ("지역", "권역", "지사", "region", "area", "territory"),
    ("채널", "판매채널", "유통채널", "channel"),
    ("품목코드", "제품코드", "sku", "item_code", "itemcode"),
    ("제품", "품목", "상품", "product", "item"),
    ("카테고리", "분류", "품목군", "category", "segment"),
    ("수량", "판매량", "개수", "qty", "quantity", "units"),
    ("단가", "판매가", "가격", "unit_price", "unitprice", "price"),
    ("할인", "할인율", "discount"),
    ("매출이익", "매출 이익", "총이익", "이익금", "gross_profit", "grossprofit", "profit"),
    ("이익률", "수익률", "마진율", "profit_margin", "profitmargin", "margin"),
    ("매출", "매출액", "판매액", "sales", "revenue", "amount", "금액"),
    ("원가", "매입가", "unit_cost", "unitcost", "cost"),
    ("상태", "진행상태", "처리상태", "status"),
    ("담당자", "영업담당자", "영업사원", "담당", "salesperson", "sales_rep", "owner", "manager"),
    ("현재고", "현재 재고", "재고", "재고수량", "current_stock", "currentstock", "stock", "on_hand"),
    ("재주문점", "발주점", "안전재고", "reorder_point", "reorderpoint", "reorder"),
    (
        "권장발주수량",
        "권장 발주수량",
        "발주수량",
        "발주 수량",
        "recommended_order_qty",
        "recommended_order",
        "order_qty",
    ),
    ("재고가치", "재고금액", "inventory_value", "inventoryvalue"),
    ("공급사", "공급업체", "거래업체", "supplier", "vendor"),
    ("진행률", "달성률", "완료율", "progress", "completion"),
    ("잔여일", "남은일수", "잔여일수", "days_remaining", "daysremaining"),
    ("위험", "지연위험", "리스크", "risk", "schedule_risk"),
    ("우선순위", "중요도", "priority"),
    ("업무", "작업", "과업", "task"),
    ("업무영역", "워크스트림", "workstream"),
    ("비고", "메모", "notes", "note", "remark"),
    ("갱신일", "수정일", "last_updated", "lastupdated", "updated"),
    ("월", "month"),
    ("이름", "성명", "name"),
    # ⚠ '성적'을 이 그룹에 넣으려다 되돌렸다(2026-08-27). "성적 좋은 애들이 위로"의
    # 되묻기는 없어지지만, `find_header_mentions`가 부분 문자열로 찾기 때문에 시트
    # 이름 **성적부** 안의 '성적'까지 점수로 잡는다 — 실측: "A2에 성적부 결석 합계
    # 넣어줘"가 [점수, 결석] 두 지목이 되어 교차시트 12문장(현재 12/12)이 위험해진다.
    # 제대로 고치려면 낱말 경계(성적부·성적표 제외)를 사전 검색에 넣어야 한다.
    ("점수", "score", "point"),
    ("결과", "판정", "result", "grade"),
)

_TOKEN_SPLIT = re.compile(r"[\s_\-./()\[\]]+")


def normalize(text: Any) -> str:
    """머리글·개념어 비교용 표준형. 공백/구분자/대소문자 차이를 없앤다."""
    return _TOKEN_SPLIT.sub("", str(text or "").strip().lower())


def _concept_index() -> dict[str, int]:
    index: dict[str, int] = {}
    for group_id, group in enumerate(_CONCEPT_GROUPS):
        for surface in group:
            index.setdefault(normalize(surface), group_id)
    return index


_SURFACE_TO_GROUP: dict[str, int] = _concept_index()


def concept_id(term: Any) -> int | None:
    """표현 하나가 속한 개념 묶음 번호. 어느 묶음에도 없으면 None."""
    key = normalize(term)
    if not key:
        return None
    if key in _SURFACE_TO_GROUP:
        return _SURFACE_TO_GROUP[key]
    # "Gross_Profit_KRW"처럼 접미사가 붙은 머리글도 개념을 알아볼 수 있어야 한다.
    best: tuple[int, int] | None = None
    for surface, group_id in _SURFACE_TO_GROUP.items():
        if len(surface) < 3 or surface not in key:
            continue
        if best is None or len(surface) > best[0]:
            best = (len(surface), group_id)
    return best[1] if best else None


def resolve_header(term: Any, headers: list[str]) -> str | None:
    """개념어 하나를 실제 머리글로 확정한다.

    완전일치 → 표준형 일치 → 개념 묶음 일치 → 부분 포함 → 문자 유사도 순으로 좁힌다.
    개념 묶음이 문자 유사도보다 먼저다: "매출"과 "Sales"는 글자가 하나도 안 겹친다.
    """
    text = str(term or "").strip()
    if not text or not headers:
        return None
    if text in headers:
        return text

    key = normalize(text)
    if not key:
        return None
    for header in headers:
        if normalize(header) == key:
            return header

    target_group = concept_id(text)
    if target_group is not None:
        for header in headers:
            if concept_id(header) == target_group:
                return header

    for header in headers:
        norm = normalize(header)
        if norm and (norm in key or key in norm):
            return header

    close = difflib.get_close_matches(key, [normalize(h) for h in headers], n=1, cutoff=0.82)
    if close:
        for header in headers:
            if normalize(header) == close[0]:
                return header
    return None


def _surfaces_for_header(header: str) -> list[str]:
    """머리글 하나를 원문에서 찾을 때 쓸 표현 목록(자기 자신 + 같은 개념의 한국어 표현)."""
    raw = str(header or "").strip()
    surfaces = [raw]
    # 사람은 단위 꼬리를 떼고 부른다 — "재적생 수(명)"을 "재적생 수"로, "장학금 지급액(억원)"을
    # "장학금 지급액"으로(2026-08-19 ex11 실측: "재적생 수 많은 순으로"가 기준 열을 못 찾아 되물었다).
    # 띄어쓰기 없는 꼴("재적생수")도 같은 머리글이다.
    bare = re.sub(r"\s*[(\[（][^)\]）]*[)\]）]\s*$", "", raw).strip()
    if len(bare) >= 2 and bare != raw:
        surfaces.append(bare)
    for cand in (raw, bare):
        squashed = re.sub(r"\s+", "", cand)
        if len(squashed) >= 2 and squashed != cand:
            surfaces.append(squashed)
    group_id = concept_id(header)
    if group_id is not None:
        surfaces.extend(_CONCEPT_GROUPS[group_id])
    # 긴 표현부터 찾아야 "매출이익"이 "매출"에 먹히지 않는다.
    unique: list[str] = []
    for surface in sorted({s for s in surfaces if s}, key=len, reverse=True):
        unique.append(surface)
    return unique


def find_header_mentions(message: str, headers: list[str]) -> list[dict[str, Any]]:
    """원문에서 언급된 머리글을 등장 순서대로 돌려준다.

    각 항목은 {"header", "surface", "start", "end"}. 같은 자리를 두 머리글이 다투면
    더 긴 표현이 이긴다("매출이익"이 잡힌 구간을 "매출"이 다시 가져가지 못한다).
    """
    text = str(message or "")
    if not text or not headers:
        return []
    candidates: list[dict[str, Any]] = []
    for header in headers:
        if not str(header or "").strip():
            continue
        for surface in _surfaces_for_header(header):
            pattern = re.escape(surface)
            if surface.isascii():
                pattern = rf"(?<![A-Za-z0-9]){pattern}(?![A-Za-z0-9])"
            for match in re.finditer(pattern, text, re.IGNORECASE):
                candidates.append(
                    {
                        "header": header,
                        "surface": surface,
                        "start": match.start(),
                        "end": match.end(),
                    }
                )

    # 여러 낱말 머리글은 **마지막 낱말만으로** 부르기도 한다 — "평균 평점(GPA)"을 "평점 높은 순".
    # 그 낱말이 다른 머리글과 겹치지 않을 때만(두 머리글이 '건수'로 끝나면 안 쓴다).
    tails: dict[str, list[str]] = {}
    for header in headers:
        bare = re.sub(r"\s*[(\[（][^)\]）]*[)\]）]\s*$", "", str(header or "").strip())
        words = [w for w in re.split(r"[\s_/·]+", bare) if w]
        if len(words) >= 2 and len(words[-1]) >= 2 and not words[-1].isdigit():
            tails.setdefault(words[-1], []).append(header)
    for tail, owners in tails.items():
        if len(owners) != 1 or any(tail == str(h).strip() for h in headers):
            continue
        pattern = re.escape(tail)
        if tail.isascii():
            pattern = rf"(?<![A-Za-z0-9]){pattern}(?![A-Za-z0-9])"
        for match in re.finditer(pattern, text, re.IGNORECASE):
            candidates.append(
                {"header": owners[0], "surface": tail, "start": match.start(), "end": match.end()}
            )

    # 앞에 나온 표현이 먼저 자리를 잡되, 같은 자리를 다투면 긴 쪽이 이긴다.
    # - "매출이익 나누기 매출": 0번 자리는 Gross_Profit이 가져가고 Sales는 뒤 자리를 쓴다.
    # - "지역별 ... Region_Chart 시트": Region은 앞의 '지역'으로 잡혀야 한다.
    candidates.sort(key=lambda h: (h["start"], -(h["end"] - h["start"])))
    claimed: list[tuple[int, int]] = []
    taken: set[str] = set()
    result: list[dict[str, Any]] = []
    for hit in candidates:
        if hit["header"] in taken:
            continue
        span = (hit["start"], hit["end"])
        if any(not (span[1] <= c[0] or span[0] >= c[1]) for c in claimed):
            continue
        claimed.append(span)
        taken.add(hit["header"])
        result.append(hit)
    result.sort(key=lambda h: h["start"])
    return result
