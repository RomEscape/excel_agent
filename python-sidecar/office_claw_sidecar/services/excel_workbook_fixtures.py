"""학습·평가용 가상 통합문서 라이브러리.

플래너는 추론할 때 `excel_workbook_digest`가 실제 파일에서 읽은 '통합문서 상태'를
프롬프트로 받는다. 그런데 학습 데이터에는 그 블록이 비어 있었다. 모델은 본 적 없는
블록을 읽는 법을 배우지 못했고, 대신 학습 중 가장 자주 본 시트명(`Sales_Data`)을
그대로 뱉었다 — 정답의 16.8%가 그 이름이었다.

이 모듈은 그 간극을 메우기 위해 **현실적인 한국어 통합문서**를 제공하고,
정답 계획이 가리키는 시트·열이 그 통합문서 안에 실제로 존재하도록 맞춰 준다.
같은 명령이라도 통합문서가 바뀌면 정답도 바뀌므로, 모델은 이름을 외우는 대신
다이제스트를 읽어야 한다.

- 픽스처 정의(데이터)와 합성 로직(동작)을 이 모듈이 함께 소유한다.
- 데이터 빌더(`scripts/build_planner_sft_jsonl.py`)와 평가 하네스가 이걸 공유한다.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass
from typing import Any

# 계획 파라미터 중 "이미 있는 시트"를 가리키는 자리.
# 여기에 들어간 이름은 반드시 다이제스트에도 있어야 한다.
EXISTING_SHEET_KEYS = ("sheet_name", "source_sheet", "left_sheet", "right_sheet")
EXISTING_SHEET_LIST_KEYS = ("source_sheets",)
# 새로 만드는 시트 자리. 다이제스트에 없어야 정상이다 —
# 여기까지 기존 시트로 바꾸면 "결과를 원본에 덮어써라"를 가르치게 된다.
NEW_SHEET_KEYS = ("output_sheet",)

# 계획 파라미터 중 "이미 있는 머리글"을 가리키는 자리.
EXISTING_COLUMN_KEYS = ("key_column", "column", "group_column", "value_column", "compare_column")
EXISTING_COLUMN_LIST_KEYS = ("key_columns", "columns")
# 새로 붙일 이름. 다이제스트에 없는 게 맞다.
NEW_COLUMN_KEYS = ("new_name", "new_column_name")


@dataclass(frozen=True)
class SheetFixture:
    """시트 하나의 모양. 머리글과 예시 행 몇 개면 다이제스트를 만들 수 있다."""

    name: str
    columns: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]


# 실무에서 실제로 마주치는 문서 모양들. 한국어 머리글이 기본이고,
# 영문 머리글 통합문서를 일부 섞어 "사용자는 한국어로 부르지만 머리글은 영문"인
# 상황(프롬프트 규칙 14)도 학습되게 한다.
WORKBOOK_FIXTURES: tuple[SheetFixture, ...] = (
    SheetFixture(
        name="매출",
        columns=("날짜", "지역", "담당자", "카테고리", "상태", "수량", "단가", "금액"),
        rows=(
            ("2026-01-05", "서울", "김민수", "가전", "완료", "3", "120000", "360000"),
            ("2026-01-07", "부산", "이서연", "가구", "진행중", "1", "450000", "450000"),
            ("2026-02-11", "서울", "박준호", "가전", "완료", "5", "89000", "445000"),
        ),
    ),
    SheetFixture(
        name="거래내역",
        columns=("거래일", "거래처", "품목", "수량", "단가", "공급가액", "부가세", "합계"),
        rows=(
            ("2026-03-02", "대한상사", "A4용지", "20", "3500", "70000", "7000", "77000"),
            ("2026-03-04", "한빛유통", "토너", "4", "58000", "232000", "23200", "255200"),
            ("2026-03-09", "대한상사", "노트북", "1", "1250000", "1250000", "125000", "1375000"),
        ),
    ),
    SheetFixture(
        name="급여대장",
        columns=("사번", "이름", "부서", "직급", "기본급", "수당", "공제액", "실지급액"),
        rows=(
            ("1001", "김민수", "영업1팀", "대리", "3200000", "250000", "410000", "3040000"),
            ("1002", "이서연", "개발팀", "과장", "4100000", "300000", "520000", "3880000"),
            ("1003", "박준호", "영업2팀", "사원", "2700000", "180000", "330000", "2550000"),
        ),
    ),
    SheetFixture(
        name="재고현황",
        columns=("품목코드", "품목명", "창고", "현재고", "안전재고", "단가", "재고금액"),
        rows=(
            ("P-001", "A4용지", "본사", "120", "50", "3500", "420000"),
            ("P-002", "토너", "본사", "8", "20", "58000", "464000"),
            ("P-003", "USB허브", "지사", "45", "30", "19000", "855000"),
        ),
    ),
    SheetFixture(
        name="학과운영비",
        columns=("날짜", "사용목적", "사용처", "결제수단", "금액", "비용유형"),
        rows=(
            ("2026-04-02", "세미나 다과", "카페베네", "법인카드", "48000", "행사비"),
            ("2026-04-08", "실험 소모품", "사이언스몰", "조교카드", "132000", "재료비"),
            ("2026-04-15", "학회 등록비", "한국정보학회", "계좌이체", "250000", "학술활동비"),
        ),
    ),
    SheetFixture(
        name="출근부",
        columns=("사번", "이름", "날짜", "출근시각", "퇴근시각", "근무시간", "지각여부"),
        rows=(
            ("1001", "김민수", "2026-05-04", "08:52", "18:10", "9.3", "정상"),
            ("1002", "이서연", "2026-05-04", "09:14", "18:40", "9.4", "지각"),
            ("1003", "박준호", "2026-05-04", "08:45", "17:55", "9.2", "정상"),
        ),
    ),
    SheetFixture(
        name="고객명단",
        columns=("고객ID", "고객명", "연락처", "지역", "가입일", "등급", "누적구매액"),
        rows=(
            ("C-1001", "김민수", "010-1234-5678", "서울", "2024-03-11", "VIP", "8420000"),
            ("C-1002", "이서연", "010-2222-3333", "경기", "2025-07-02", "일반", "1250000"),
            ("C-1003", "박준호", "010-4444-5555", "부산", "2023-11-20", "VIP", "12300000"),
        ),
    ),
    SheetFixture(
        name="프로젝트일정",
        columns=("과제명", "담당자", "시작일", "종료일", "진행률", "상태"),
        rows=(
            ("사내포털 개편", "김민수", "2026-01-06", "2026-04-30", "0.85", "진행중"),
            ("재고시스템 이관", "이서연", "2026-02-17", "2026-06-12", "0.4", "진행중"),
            ("보안점검", "박준호", "2025-11-03", "2026-01-15", "1", "완료"),
        ),
    ),
    SheetFixture(
        name="예산집행",
        columns=("부서", "예산액", "집행액", "잔액", "집행률", "비고"),
        rows=(
            ("영업1팀", "50000000", "38200000", "11800000", "0.764", ""),
            ("개발팀", "82000000", "79500000", "2500000", "0.97", "추경 요청"),
            ("총무팀", "31000000", "12400000", "18600000", "0.4", ""),
        ),
    ),
    SheetFixture(
        name="설문응답",
        columns=("응답ID", "제출일", "연령대", "성별", "만족도", "재구매의향", "의견"),
        rows=(
            ("R-001", "2026-06-01", "30대", "여", "5", "예", "배송이 빨랐어요"),
            ("R-002", "2026-06-01", "40대", "남", "3", "아니오", "포장이 아쉬움"),
            ("R-003", "2026-06-02", "20대", "여", "4", "예", ""),
        ),
    ),
    SheetFixture(
        name="Sales_Data",
        columns=("Date", "Region", "Rep", "Product", "Category", "Qty", "UnitPrice", "Amount"),
        rows=(
            ("2026-01-05", "Seoul", "Kim", "Laptop", "IT", "3", "1200000", "3600000"),
            ("2026-01-09", "Busan", "Lee", "Monitor", "IT", "6", "280000", "1680000"),
            ("2026-02-02", "Seoul", "Park", "Desk", "Furniture", "2", "350000", "700000"),
        ),
    ),
    SheetFixture(
        name="Sheet1",
        columns=("항목", "구분", "수량", "금액", "비고"),
        rows=(
            ("사무용품", "소모품", "12", "84000", ""),
            ("회식비", "복리후생", "1", "320000", "팀 워크숍"),
            ("교통비", "여비", "8", "96000", ""),
        ),
    ),
)

_FIXTURES_BY_NAME = {fixture.name: fixture for fixture in WORKBOOK_FIXTURES}

# 다이제스트에 섞어 넣는 방해 열. 정답과 무관한 열이 함께 있어야
# "머리글 하나뿐이라 그냥 골랐다"가 아니라 실제로 고르는 법을 배운다.
_DISTRACTOR_COLUMNS = (
    "비고",
    "등록자",
    "수정일",
    "메모",
    "구분코드",
    "담당부서",
)


def _col_letter(index: int) -> str:
    """0-based 열 번호를 A1 표기 열 문자로."""
    n = index + 1
    out = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        out = chr(ord("A") + rem) + out
    return out


def _is_positional(value: Any) -> bool:
    """열 지정이 이름이 아니라 번호/열 문자인지. 이런 값은 머리글로 옮길 필요가 없다."""
    if isinstance(value, bool):
        return True
    if isinstance(value, (int, float)):
        return True
    text = str(value or "").strip()
    if not text:
        return True
    return bool(re.fullmatch(r"[A-Za-z]{1,3}", text)) and text.upper() == text


def _iter_plan_params(action_plan: list[dict[str, Any]]):
    for step in action_plan:
        params = step.get("params")
        if isinstance(params, dict):
            yield step, params


def collect_plan_references(action_plan: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    """정답 계획이 '이미 존재한다고 가정하는' 시트명과 머리글을 모은다."""
    sheets: list[str] = []
    columns: list[str] = []

    def _push(bucket: list[str], value: Any) -> None:
        text = str(value or "").strip()
        if text and text not in bucket:
            bucket.append(text)

    for step, params in _iter_plan_params(action_plan):
        action = str(step.get("action", ""))
        for key in EXISTING_SHEET_KEYS:
            # create_sheet.sheet_name은 새로 만드는 시트라 기존 시트가 아니다.
            if key == "sheet_name" and action == "excel_live.create_sheet":
                continue
            _push(sheets, params.get(key))
        for key in EXISTING_SHEET_LIST_KEYS:
            for item in params.get(key) or []:
                _push(sheets, item)
        for key in EXISTING_COLUMN_KEYS:
            value = params.get(key)
            if not _is_positional(value):
                _push(columns, value)
        for key in EXISTING_COLUMN_LIST_KEYS:
            for item in params.get(key) or []:
                if not _is_positional(item):
                    _push(columns, item)
    return sheets, columns


def _rewrite_plan_sheets(
    action_plan: list[dict[str, Any]], mapping: dict[str, str]
) -> list[dict[str, Any]]:
    """계획 안의 기존 시트 이름을 픽스처 시트 이름으로 바꾼다."""
    if not mapping:
        return action_plan
    rewritten: list[dict[str, Any]] = []
    for step in action_plan:
        params = dict(step.get("params") or {})
        action = str(step.get("action", ""))
        for key in EXISTING_SHEET_KEYS:
            if key == "sheet_name" and action == "excel_live.create_sheet":
                continue
            current = str(params.get(key) or "").strip()
            if current in mapping:
                params[key] = mapping[current]
        for key in EXISTING_SHEET_LIST_KEYS:
            items = params.get(key)
            if isinstance(items, list):
                params[key] = [mapping.get(str(i).strip(), i) for i in items]
        rewritten.append({**step, "params": params})
    return rewritten


def _sheet_entry(
    fixture: SheetFixture, extra_columns: list[str], row_count: int
) -> dict[str, Any]:
    columns = list(fixture.columns) + [c for c in extra_columns if c not in fixture.columns]
    used_range = f"A1:{_col_letter(len(columns) - 1)}{row_count}"
    # 원본 픽스처보다 열이 늘어난 만큼 예시 행도 채워야 열 수가 어긋나지 않는다.
    rows = [list(row) + [""] * (len(columns) - len(row)) for row in fixture.rows]
    return {
        "name": fixture.name,
        "used_range": used_range,
        "columns": [
            {"letter": _col_letter(idx), "header": header} for idx, header in enumerate(columns)
        ],
        "sample_rows": rows,
        "_body_rows": rows,
    }


def _apply_column_summaries(entry: dict[str, Any]) -> None:
    """활성 시트의 값 후보를 채운다 — 실제 다이제스트와 같은 정보량을 갖게."""
    body = entry.pop("_body_rows", [])
    for offset, column in enumerate(entry["columns"]):
        values = [str(row[offset]).strip() for row in body if offset < len(row) and str(row[offset]).strip()]
        if not values:
            continue
        numeric = sum(1 for v in values if re.fullmatch(r"-?[\d,.]+", v)) / len(values) >= 0.8
        if numeric:
            column["numeric"] = True
            continue
        distinct: list[str] = []
        for value in values:
            if value not in distinct:
                distinct.append(value)
        if len(distinct) <= 8:
            column["categories"] = distinct


def synthesize_digest(
    action_plan: list[dict[str, Any]],
    *,
    instruction: str = "",
    seed: str = "",
    preferred_sheet: str = "",
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """정답 계획과 아귀가 맞는 통합문서 다이제스트를 만든다.

    반환값은 (다이제스트, 다시 쓴 계획)이다. 계획 쪽 시트 이름을 픽스처 이름으로
    바꾸기 때문에 원본을 그대로 쓰면 안 된다.

    핵심 규칙 두 가지:
    - 계획이 참조하는 시트·머리글은 반드시 다이제스트에 있다. 없는 걸 쓰라고
      가르치면 환각을 정답으로 굳히게 된다.
    - 시트 이름은 레코드마다 바꾼다. 고정하면 모델이 이름을 외워 버린다.
    """
    rng = random.Random(seed or instruction)
    ref_sheets, ref_columns = collect_plan_references(action_plan)

    # 원문이 시트를 직접 부르면(예: "매출 시트에서") 그 이름은 건드리지 않는다.
    instruction_text = str(instruction or "")
    pinned = {name for name in ref_sheets if name and name in instruction_text}

    candidates = [f for f in WORKBOOK_FIXTURES if f.name not in pinned]
    rng.shuffle(candidates)
    if preferred_sheet and preferred_sheet in _FIXTURES_BY_NAME:
        preferred = _FIXTURES_BY_NAME[preferred_sheet]
        candidates = [preferred] + [f for f in candidates if f.name != preferred_sheet]

    mapping: dict[str, str] = {}
    chosen: list[SheetFixture] = []
    for original in ref_sheets:
        if original in pinned:
            # 원문이 지목한 이름은 그대로 두되, 픽스처가 없으면 새로 만들어 준다.
            fixture = _FIXTURES_BY_NAME.get(original) or SheetFixture(
                name=original, columns=("항목", "값", "비고"), rows=(("샘플", "1", ""),)
            )
            if fixture.name not in {c.name for c in chosen}:
                chosen.append(fixture)
            continue
        pick = next((f for f in candidates if f.name not in {c.name for c in chosen}), None)
        if pick is None:
            pick = rng.choice(WORKBOOK_FIXTURES)
        mapping[original] = pick.name
        chosen.append(pick)

    if not chosen:
        chosen.append(candidates[0] if candidates else WORKBOOK_FIXTURES[0])

    # 시트가 하나뿐이면 "고를 것이 없어서 맞은" 예제가 된다. 방해 시트를 하나 붙인다.
    if len(chosen) < 2:
        filler = next(
            (f for f in candidates if f.name not in {c.name for c in chosen}),
            None,
        )
        if filler is not None:
            chosen.append(filler)

    active = chosen[0]
    # 계획이 쓰는 머리글이 활성 시트에 없으면 넣어 준다. 그래야 정답이 다이제스트와 일치한다.
    missing_columns = [c for c in ref_columns if c not in active.columns]
    distractors = [
        c
        for c in rng.sample(_DISTRACTOR_COLUMNS, k=min(2, len(_DISTRACTOR_COLUMNS)))
        if c not in active.columns and c not in missing_columns
    ]

    sheets: list[dict[str, Any]] = []
    for idx, fixture in enumerate(chosen):
        extra = (missing_columns + distractors) if idx == 0 else []
        entry = _sheet_entry(fixture, extra, row_count=rng.choice([9, 14, 23, 41, 87, 156]))
        if idx == 0:
            _apply_column_summaries(entry)
        else:
            entry.pop("_body_rows", None)
        sheets.append(entry)

    digest = {"active_sheet": active.name, "sheets": sheets}
    return digest, _rewrite_plan_sheets(action_plan, mapping)


def digest_from_fixtures(names: list[str], *, seed: str = "") -> dict[str, Any]:
    """이름으로 고른 픽스처들로 다이제스트를 만든다. 첫 번째가 활성 시트다."""
    rng = random.Random(seed or "|".join(names))
    chosen = [_FIXTURES_BY_NAME[name] for name in names if name in _FIXTURES_BY_NAME]
    if not chosen:
        chosen = [WORKBOOK_FIXTURES[0]]
    sheets: list[dict[str, Any]] = []
    for idx, fixture in enumerate(chosen):
        entry = _sheet_entry(fixture, [], row_count=rng.choice([9, 14, 23, 41, 87, 156]))
        if idx == 0:
            _apply_column_summaries(entry)
        else:
            entry.pop("_body_rows", None)
        sheets.append(entry)
    return {"active_sheet": chosen[0].name, "sheets": sheets}


def numeric_headers(digest: dict[str, Any]) -> list[str]:
    """활성 시트에서 숫자로 보이는 열 — 정렬·합계 후보."""
    for sheet in digest.get("sheets") or []:
        if str(sheet.get("name")) == str(digest.get("active_sheet")):
            return [
                str(col.get("header"))
                for col in sheet.get("columns") or []
                if col.get("numeric") and str(col.get("header"))
            ]
    return []


def categorical_headers(digest: dict[str, Any]) -> list[tuple[str, list[str]]]:
    """활성 시트에서 값 후보가 있는 열 — 그룹·필터 후보."""
    out: list[tuple[str, list[str]]] = []
    for sheet in digest.get("sheets") or []:
        if str(sheet.get("name")) != str(digest.get("active_sheet")):
            continue
        for col in sheet.get("columns") or []:
            categories = [str(c) for c in (col.get("categories") or []) if str(c)]
            if len(categories) >= 2:
                out.append((str(col.get("header")), categories))
    return out


def digest_headers(digest: dict[str, Any], sheet_name: str = "") -> list[str]:
    """다이제스트에서 특정 시트(기본: 활성 시트)의 머리글 목록을 뽑는다."""
    target = sheet_name or str(digest.get("active_sheet") or "")
    for sheet in digest.get("sheets") or []:
        if str(sheet.get("name")) == target:
            return [str(col.get("header") or "") for col in sheet.get("columns") or []]
    return []
