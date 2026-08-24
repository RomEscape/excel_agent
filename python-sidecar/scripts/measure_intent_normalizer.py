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

# 프롬프트는 **프로덕션 것 하나뿐**이다. 예전에는 여기 복제본이 있었고, 거기엔
# `highlight`가 통째로 빠져 있었다(16종). 그래서 이 스크립트가 재던 100%/96%는
# 실제로 배포되는 프롬프트가 아닌 것의 점수였다(2026-08-23 확인).
from office_claw_sidecar.services.excel_intent_normalizer import (
    _PROMPT as PROMPT,
)

#: (맞았나, 모델이 말한 확신도) — 모델이 자기가 모를 때를 아는지 재려는 것.
CONF: list[tuple[bool, str]] = []


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
            CONF.append((ok, str((out or {}).get('confidence') or '').lower()))
            stats[style][0] += ok
            stats[style][1] += 1
            mark = "OK  " if ok else "FAIL"
            print(f"{mark} [{style}] {intent:6s} {msg[:34]:36s} → {json.dumps(out, ensure_ascii=False)[:72]}", flush=True)

    inc_ok = 0
    print()
    for msg, judge in INCIDENT_CASES:
        out = await ask(llm, model, msg)
        ok = bool(judge(out))
        CONF.append((ok, str((out or {}).get('confidence') or '').lower()))
        inc_ok += ok
        mark = "OK  " if ok else "FAIL"
        print(f"{mark} [사고] {msg[:36]:38s} → {json.dumps(out, ensure_ascii=False)[:72]}", flush=True)

    print("\n" + "=" * 78)
    for style, name in (("T", "훈련체"), ("U", "사용자체")):
        ok, total = stats[style]
        print(f"{name}: {ok}/{total} ({ok/total*100:.0f}%)   [플래너 실측: 훈련체 67% / 사용자체 58%]")
    print(f"GUI 사고 문장: {inc_ok}/{len(INCIDENT_CASES)}")
    if CONF:
        import collections

        tab = collections.Counter((c or "(없음)", ok) for ok, c in CONF)
        print()
        print("확신도 대 정답 — 모델이 자기가 모를 때를 아는가")
        for conf in sorted({c for c, _ in tab}):
            good, bad = tab[(conf, True)], tab[(conf, False)]
            total = good + bad
            print(f"  {conf:8} {total:3d}건 중 맞음 {good:3d} ({100 * good / max(total, 1):.0f}%) · 틀림 {bad}")


asyncio.run(main())
