"""범용 모델(ax4-light) 의도 정규화 정확도 측정.

`measure_planner_generalization.py`의 실측: 플래너(작은 SFT)는 훈련체 67% /
사용자체 58%였고, 실패는 표현이 아니라 **파라미터 암기**(=SUM(E:E), "천단위")였다.

가설: 이해(의도·대상 분류)는 범용 인스트럭트 모델에게, 좌표 확정은 바인더에게.
검증: 같은 36문장 + 이번 주 GUI 사고 문장 8건을 ax4-light에 "구조화된 의도"로
번역시키고 정답률을 잰다. 모델은 좌표를 만들지 않는다 — 문장에 있는 범위를
옮겨 적고, 열은 머리글 이름으로만 가리킨다.

실행:
    & $PY scripts\\measure_intent_normalizer.py            # 기본(ax4-light)
    & $PY scripts\\measure_intent_normalizer.py <모델이름>  # 다른 모델 비교
"""

from __future__ import annotations

import asyncio
import json
import sys

sys.path.insert(0, ".")

from office_claw_sidecar.services.llm_json import extract_json_object
from office_claw_sidecar.services.llm_service import get_llm_service

HEADERS = ["날짜", "지역", "담당자", "금액"]

PROMPT = """너는 Excel 명령 해석기다. 사용자 문장을 아래 JSON으로만 번역해라.

시트 머리글: {headers}

JSON 형식:
{{"task": "<작업>", "range": "<문장에 적힌 범위 그대로, 없으면 null>",
  "column": "<대상 열의 머리글 이름, 없으면 null>", "option": "<핵심 옵션, 없으면 null>"}}

task 목록: fill_color(배경색), font(글자 서식·색), number_format(표시 형식),
formula(수식·계산), sort(정렬), filter(필터), dedupe(중복 제거),
clear_values(값 비우기), reset_all(서식까지 초기화), create_table(표 생성),
pivot(집계표·피벗), chart(차트), write_value(값 입력), find_replace(찾아 바꾸기),
read(조회), other(그 외)

규칙:
- 범위·좌표를 **만들어내지 마라.** 문장에 적힌 것만 옮겨 적는다.
- 열은 좌표가 아니라 머리글 이름으로 가리킨다.
- option: 색 이름, asc/desc, 필터 값, SUM/AVERAGE/MAX/MIN/COUNT, 새 값 등 하나.

예시:
문장: "B2:B9 파란색으로 칠해줘"
{{"task": "fill_color", "range": "B2:B9", "column": null, "option": "파란색"}}
문장: "매출 높은 순서로 보여줘"
{{"task": "sort", "range": null, "column": "금액", "option": "desc"}}
문장: "G1에 담당자별 평균 수식 넣어줘"
{{"task": "formula", "range": "G1", "column": "금액", "option": "AVERAGE"}}

문장: "{message}"
JSON:"""


def _norm(v) -> str:
    return str(v or "").strip().lower()


def j_task(*tasks):
    return lambda o: _norm(o.get("task")) in {t.lower() for t in tasks}


def j_sum(o):
    return _norm(o.get("task")) == "formula" and _norm(o.get("column")) == "금액" and "sum" in _norm(o.get("option"))


def j_sort_desc(o):
    return _norm(o.get("task")) == "sort" and _norm(o.get("column")) == "금액" and "desc" in _norm(o.get("option"))


def j_filter_seoul(o):
    return _norm(o.get("task")) == "filter" and "서울" in _norm(o.get("option"))


def j_numfmt(o):
    return _norm(o.get("task")) == "number_format"


def j_fill(o):
    return _norm(o.get("task")) == "fill_color" and _norm(o.get("range")) == "a1:d1"


# (그룹, 의도, 판정, [(스타일, 문장)])
MAIN_CASES = [
    ("배경색", j_fill, [
        ("T", "A1:D1 노란색으로 채워줘"),
        ("T", "A1:D1 배경 노란색 적용"),
        ("U", "A1:D1 노란색으로 채워줄 수 있어?"),
        ("U", "혹시 A1:D1 좀 노랗게 해줄래?"),
        ("U", "머리글이 눈에 띄게 A1:D1 배경을 노랗게 하고 싶은데"),
        ("U", "A1:D1 이 부분 노랗게 만들어주면 좋겠어"),
    ]),
    ("합계수식", j_sum, [
        ("T", "F2에 금액 합계 수식 넣어줘"),
        ("T", "F2에 금액 합계 적용"),
        ("U", "F2에 금액 다 더한 값 넣어줄 수 있어?"),
        ("U", "금액 전부 합쳐서 F2에 보여줄래?"),
        ("U", "F2에다 금액 총합 좀 계산해줬으면 하는데"),
        ("U", "매출 총액이 얼마인지 F2에 넣어놔줘"),
    ]),
    ("내림차순정렬", j_sort_desc, [
        ("T", "금액 기준 내림차순 정렬해줘"),
        ("T", "금액 내림차순 정렬 적용"),
        ("U", "금액 큰 순서대로 정렬해줄 수 있어?"),
        ("U", "금액 많은 것부터 위로 오게 해줄래?"),
        ("U", "비싼 거래가 먼저 보이게 좀 정리해줬으면 해"),
        ("U", "금액이 높은 순으로 줄 세워줘"),
    ]),
    ("필터", j_filter_seoul, [
        ("T", "지역이 서울인 행만 필터해줘"),
        ("T", "지역 서울 필터 적용"),
        ("U", "서울 데이터만 남겨줄 수 있어?"),
        ("U", "서울 지역 것만 보고 싶은데"),
        ("U", "서울 아닌 데는 좀 치워줄래?"),
        ("U", "서울 거래만 추려줘"),
    ]),
    ("표시형식", j_numfmt, [
        ("T", "D2:D9 천 단위 콤마 적용해줘"),
        ("T", "금액 열 천 단위 구분 기호 적용"),
        ("U", "금액에 천 단위 콤마 넣어줄 수 있어?"),
        ("U", "금액 숫자 읽기 편하게 콤마 좀 찍어줄래?"),
        ("U", "금액이 잘 안 읽혀서 자릿수 구분 해줬으면 해"),
        ("U", "D열 숫자에 쉼표 표시되게 해줘"),
    ]),
    ("중복제거", j_task("dedupe"), [
        ("T", "중복 행 제거해줘"),
        ("T", "중복 데이터 제거 적용"),
        ("U", "중복된 행 지워줄 수 있어?"),
        ("U", "똑같은 행이 두 번 들어갔는데 정리해줄래?"),
        ("U", "겹치는 데이터 좀 없애줬으면 해"),
        ("U", "같은 내용이 반복된 줄은 빼줘"),
    ]),
]

# 이번 주 GUI에서 실제로 실패했던 문장들. task 분류만 판정한다
# (대상 확정은 문맥·바인더 몫이라 단일 턴 정규화의 책임이 아니다).
INCIDENT_CASES = [
    ("A1:D9 여기 부분 초기화시켜줄 수 있어?", j_task("reset_all", "clear_values")),
    ("이 부분은 원래대로 초기화해줄 수 있어? 표 없애줘", j_task("reset_all", "clear_values")),
    ("글자도 흰색으로", j_task("font")),
    ("금액에 천 단위 콤마 넣어줘", j_task("number_format")),
    ("A12에 합계 라고 입력해줘", j_task("write_value")),
    ("서울을 전부 SEOUL로 바꿔줘", j_task("find_replace")),
    ("지역별 금액 합계 집계표 만들어줘", j_task("pivot")),
    ("A1:D13 여기에 출석부를 본격적으로 만들기 시작하자", j_task("create_table")),
]


async def ask(llm, model: str, message: str) -> dict:
    prompt = PROMPT.format(headers=", ".join(HEADERS), message=message)
    try:
        raw = await llm.chat(
            [{"role": "user", "content": prompt}],
            model=model, temperature=0.0, json_only=True, timeout=60,
        )
        return extract_json_object(raw, require_keys=("task",)) or {}
    except Exception as exc:
        return {"task": f"(오류: {exc})"}


async def main() -> None:
    model = sys.argv[1] if len(sys.argv) > 1 else "ax4-light:latest"
    llm = get_llm_service()
    print(f"모델: {model}\n")

    stats = {"T": [0, 0], "U": [0, 0]}
    for intent, judge, phrasings in MAIN_CASES:
        for style, msg in phrasings:
            out = await ask(llm, model, msg)
            ok = bool(judge(out))
            stats[style][0] += ok
            stats[style][1] += 1
            mark = "OK  " if ok else "FAIL"
            print(f"{mark} [{style}] {intent:6s} {msg[:34]:36s} → {json.dumps(out, ensure_ascii=False)[:72]}", flush=True)

    inc_ok = 0
    print()
    for msg, judge in INCIDENT_CASES:
        out = await ask(llm, model, msg)
        ok = bool(judge(out))
        inc_ok += ok
        mark = "OK  " if ok else "FAIL"
        print(f"{mark} [사고] {msg[:36]:38s} → {json.dumps(out, ensure_ascii=False)[:72]}", flush=True)

    print("\n" + "=" * 78)
    for style, name in (("T", "훈련체"), ("U", "사용자체")):
        ok, total = stats[style]
        print(f"{name}: {ok}/{total} ({ok/total*100:.0f}%)   [플래너 실측: 훈련체 67% / 사용자체 58%]")
    print(f"GUI 사고 문장: {inc_ok}/{len(INCIDENT_CASES)}")


asyncio.run(main())
