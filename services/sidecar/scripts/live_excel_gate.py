# -*- coding: utf-8 -*-
"""실사용 경로 검사 — 사람이 Excel 을 열어 놓고 편집 중일 때를 스스로 채점한다.

## 왜 따로 있는가

배터리·게이트가 **전부** `EXCEL_LIVE_ENGINE="file"` 을 코드에 박아 넣는다
(`battery_all.py:58` · `nightly_gates.py:47` · `run_blind_subset.py:39` ·
`overnight_blind_b_remeasure.py:84`). 그건 저장된 파일을 openpyxl 로 읽는 경로다.

그런데 실사용은 사람이 Excel 을 띄워 놓은 상태이고, 그때 `auto` 는 xlwings 를 고른다
(`excel_live_service.py:4001`). **그 경로는 지금까지 어떤 자동 검사도 지나지 않았다.**
2026-09-10 손으로 재 보니 동작은 했다 — 저장 안 된 편집을 보고, 사람이 잡아 둔 선택
영역을 알고, 사람이 친 줄을 덮지 않고 그 아래에 이어 썼다. 문제는 **깨져도 아무도
모른다**는 것이다.

## 채점 방식

경우마다 "무엇이 어떻게 바뀌어야 하는가"를 미리 적고, 명령 뒤에 **화면의 Excel 을 직접
읽어** 대조한다. 응답의 자기보고는 보지 않는다 — "0개 셀 치환"이라 말하면서 합격한
전례가 있다(2026-09-08 seedD).

  expect="cells"     : 지정한 칸이 지정한 값이 되어야 한다
  expect="formula"   : 지정한 칸이 **수식**이어야 한다(값으로 굳으면 실패)
  expect="bold"      : 지정한 칸이 굵어야 하고, 굵으면 안 되는 칸은 안 굵어야 한다
  expect="asks"      : 되묻거나 승인 카드가 올라와야 하고, 아무 칸도 바뀌면 안 된다
  expect="unchanged" : 통합문서가 **하나도** 바뀌면 안 된다
  expect="reads"     : 조회만 해야 한다 — 아무 칸도 바뀌면 안 된다

모든 경우에 공통으로 **사람이 방금 친 4행이 살아 있는지**를 확인한다.

## 사람의 작업을 건드리지 않는다

Excel 이 없거나 **사람의 통합문서가 이미 열려 있으면 건너뛴다**(실패가 아니다).
자기 임시 파일만 쓰고, 끝나면 저장하지 않고 닫고 Excel 을 종료한다.

## 한계

- Excel 이 설치돼 있어야 돈다. CI 러너에는 없다 — 이 개발기에서 손으로 돌린다.
- 경우당 Excel 을 새로 띄우므로 느리다(경우당 3~10초).
- 조회 답변의 **내용**은 채점하지 않는다("몇 행이야?"에 4라고 답했는지는 안 본다) —
  말투가 매번 달라 자동 채점이 못 미더워진다. 아무 칸도 안 바뀌었는지만 본다.

사용:
    python scripts/live_excel_gate.py
    python scripts/live_excel_gate.py --only 행지목 --only 열지목
    python scripts/live_excel_gate.py --json <reports>/live_excel_gate.json

`<reports>` 는 `office_claw_sidecar.config.get_reports_dir()` — 기본 %LOCALAPPDATA%/office_claw/reports.
저장소 `logs/` 에는 `chat_log.jsonl` 만 둔다(2026-09-10).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _console import force_utf8  # noqa: E402

force_utf8()

# 엔진을 고정하지 않는다 — `auto` 가 xlwings 를 고르는 것까지가 검사 대상이다.
os.environ.pop("EXCEL_LIVE_ENGINE", None)
os.environ["PYTHONUTF8"] = "1"

from openpyxl import Workbook  # noqa: E402

#: 사람이 이미 쳐서 **저장한** 부분.
SAVED_ROWS = [
    ["제품", "카테고리", "단가", "판매량", "매출", "재고"],
    ["노트북", "전자", 1200000, 45, 54000000, 12],
    ["마우스", "주변기기", 25000, 32, 8000000, 150],
]
#: 사람이 방금 치고 **아직 저장하지 않은** 줄. 4행.
UNSAVED_ROW = ["키보드", "주변기기", 55000, 210, 11550000, 80]

#: 대조에 쓸 창. 표(A1:F4)보다 넉넉히 잡아 엉뚱한 데 쓰는 것도 잡는다.
SNAPSHOT_RANGE = "A1:J12"


def _blank_a_cell(sheet: Any) -> None:
    """사람이 한 칸을 비워 둔 상태 — '빈 칸 채워줘' 를 재려면 빈 칸이 있어야 한다."""
    sheet.range("E3").value = None


CASES: list[dict[str, Any]] = [
    # ── 이어서 작업하기 ──────────────────────────────────────────────────────
    {
        "이름": "이어붙이기",
        "명령": "모니터,전자,350000,95,33250000,30 이어서 넣어줘",
        "expect": "cells",
        "확인": {"A5": "모니터", "C5": 350000, "F5": 30},
        "불변": {"A4": "키보드", "C4": 55000},
        "왜": "저장 안 된 4행 아래(A5)에 붙어야 한다. 4행을 덮으면 사람 작업이 사라진다.",
    },
    {
        "이름": "열추가",
        "명령": "비고 열 하나 추가해줘",
        "expect": "cells",
        "확인": {"G1": "비고"},
        "불변": {"A1": "제품", "F1": "재고", "A4": "키보드"},
        "왜": "빈 열(G)부터 붙어야 한다. 쓰던 열을 밀거나 덮으면 표가 어긋난다.",
    },
    # ── 작업 중 문제 해결 ────────────────────────────────────────────────────
    {
        "이름": "값정정",
        "명령": "마우스 판매량이 32로 잘못 들어갔어, 320으로 고쳐줘",
        "expect": "cells",
        "확인": {"D3": 320},
        "불변": {"A3": "마우스", "C3": 25000, "A4": "키보드"},
        "왜": "작업 중에 틀린 숫자를 고치는 것 — 실사용에서 가장 흔한 문제 해결이다.",
    },
    {
        "이름": "저장안된줄수정",
        "명령": "키보드 재고를 90으로 바꿔줘",
        "expect": "cells",
        "확인": {"F4": 90},
        "불변": {"A4": "키보드", "C4": 55000, "D4": 210},
        "왜": "**아직 저장하지 않은 줄**을 고치는 것. 파일만 보는 경로로는 이 줄이 안 보인다.",
    },
    {
        "이름": "빈칸채우기",
        "준비": _blank_a_cell,
        "명령": "빈 칸 0으로 채워줘",
        "expect": "cells",
        "확인": {"E3": 0},
        "불변": {"A3": "마우스", "E2": 54000000, "A4": "키보드"},
        "왜": "빈 칸만 채워야 한다. 값이 든 칸까지 0으로 덮으면 데이터가 날아간다.",
    },
    {
        "이름": "수식채우기",
        "명령": "G열에 단가 곱하기 판매량 검산 수식 넣어줘",
        "expect": "formula",
        "확인": {"G2": "*"},
        "불변": {"A4": "키보드"},
        "왜": "수식은 값이 아니라 수식으로 들어가야 한다 — 값으로 굳으면 갱신되지 않는다.",
    },
    {
        "이름": "정렬",
        "명령": "단가 높은 순으로 정렬해줘",
        "expect": "sorted",
        "확인": {"열": "C", "내림차순": True},
        "불변": {"A1": "제품", "F1": "재고"},
        "왜": "정렬은 저장 안 된 줄까지 포함해야 한다. 머리글이 섞이면 표가 깨진다.",
    },
    # ── 어디를 가리켰는가 ────────────────────────────────────────────────────
    {
        "이름": "열지목",
        "명령": "C열 굵게 해줘",
        "expect": "bold",
        "확인": {"C1": True, "C4": True},
        "안굵음": ["A1", "F1", "A4"],
        "불변": {"A4": "키보드"},
        "왜": "열을 지목한 경우 — 대조군이다. 이건 되고 행 지목은 안 된다면 짝이 빠진 것이다.",
    },
    {
        "이름": "행지목",
        "명령": "1행 굵게 해줘",
        "expect": "bold",
        "확인": {"A1": True, "F1": True},
        "안굵음": ["A2", "A4", "F4"],
        "불변": {"A4": "키보드"},
        "왜": (
            "**2026-09-10 실측 실패.** 사람이 4행을 잡아 둔 상태에서 1행을 지목했는데 "
            "A4:F4 가 굵어졌다. 원인은 아직 자리를 못 찾았다 — 확인된 것만 적는다: "
            "빠른 규칙은 `1행`·`머리글` 을 알아보고 `target=\"1:1\"` 을 낸다"
            "(`routers/excel_live.py:3802-3812` `header_font`). 서비스도 `1:1` 을 "
            "1행으로, `C:C` 를 C1:C4 로 **정확히 푼다**(실측). 그런데 서비스가 받은 "
            "`target_range` 는 이미 `A4:F4` 였다 — 라우터의 파라미터 확정 구간에서 "
            "선택 영역으로 바뀐다. 그 자리를 찾는 것이 남은 일이다."
        ),
    },
    {
        "이름": "머리글지목",
        "명령": "머리글 행 굵게 해줘",
        "expect": "bold",
        "확인": {"A1": True, "F1": True},
        "안굵음": ["A2", "A4"],
        "불변": {"A4": "키보드"},
        "왜": "'머리글 행' 은 다이제스트가 아는 머리글 줄이어야 한다. 선택 영역이 아니다.",
    },
    {
        "이름": "선택영역지목",
        "명령": "여기 굵게 해줘",
        "expect": "bold",
        "확인": {"A4": True, "F4": True},
        "안굵음": ["A1", "A2"],
        "불변": {"A4": "키보드"},
        "왜": "'여기' 는 **선택 영역이 맞다**. 위 두 경우와 반대 방향 — 선택을 못 쓰면 그것도 결함이다.",
    },
    # ── 하지 말아야 할 때 ────────────────────────────────────────────────────
    {
        "이름": "대상미지정",
        "명령": "필요 없는 열 지워줘",
        "expect": "asks",
        "왜": "어느 열인지 사람만 안다. 추측해서 지우면 되돌릴 수 없다.",
    },
    {
        "이름": "전면보류",
        "명령": "일단 보류할게. 지금은 아무것도 하지 마",
        "expect": "unchanged",
        "왜": "전면 보류. 한 칸이라도 바뀌면 미검출 오실행이다.",
    },
    {
        "이름": "상태조회",
        "명령": "지금 데이터가 몇 행이야?",
        "expect": "reads",
        "왜": "조회는 아무것도 바꾸지 않아야 한다. 답변 내용은 채점하지 않는다(말투가 매번 다르다).",
    },
]


def build_saved_file() -> Path:
    wb = Workbook()
    ws = wb.active
    ws.title = "작업물"
    for row in SAVED_ROWS:
        ws.append(row)
    path = Path(tempfile.mkdtemp()) / "작업중.xlsx"
    wb.save(path)
    wb.close()
    return path


def skip_reason() -> str | None:
    """건너뛸 이유. 사람의 작업을 건드리지 않기 위한 문턱이다."""
    try:
        import xlwings as xw
    except Exception as exc:
        return f"xlwings 를 쓸 수 없다: {type(exc).__name__}"
    try:
        from office_claw_sidecar.services.excel_live_service import _is_user_workbook_path

        for app in xw.apps:
            for book in app.books:
                name = str(getattr(book, "fullname", "") or "")
                if _is_user_workbook_path(name):
                    return f"사람의 통합문서가 이미 열려 있다({Path(name).name}) — 건드리지 않는다"
    except Exception:
        return "열린 통합문서를 조회할 수 없다 — Excel 상태가 불확실하다"
    return None


def _snapshot(sheet: Any) -> list[list[Any]]:
    """대조용 창을 통째로 뜬다 — 엉뚱한 데 쓴 것도 잡으려면 표 밖도 봐야 한다."""
    got = sheet.range(SNAPSHOT_RANGE).value
    return [list(row) for row in got] if got and isinstance(got[0], list) else [list(got or [])]


def _same_value(got: Any, want: Any) -> bool:
    if isinstance(want, (int, float)) and not isinstance(want, bool):
        return isinstance(got, (int, float)) and abs(float(got) - float(want)) < 1e-9
    return str(got) == str(want)


async def run_case(case: dict[str, Any]) -> dict[str, Any]:
    import xlwings as xw

    from office_claw_sidecar.routers.excel_live import (
        ApprovalResponse,
        ExcelLiveCommandRequest,
        post_approval,
        post_command,
    )
    from office_claw_sidecar.services.excel_live_service import (
        get_excel_live_service,
        invalidate_excel_engine_cache,
    )
    from office_claw_sidecar.services.excel_workbook_digest import invalidate_workbook_digest
    from office_claw_sidecar.services.llm_service import get_llm_service

    name = str(case["이름"])
    path = build_saved_file()
    app = xw.App(visible=False, add_book=False)
    book = None
    out: dict[str, Any] = {"이름": name, "명령": case["명령"], "합격": False, "왜": ""}
    started = time.time()
    try:
        book = app.books.open(str(path))
        sheet = book.sheets["작업물"]
        # 사람이 4행을 치고 저장하지 않은 상태 + 그 줄을 잡아 둔 상태
        sheet.range("A4").value = [UNSAVED_ROW]
        prepare: Callable[[Any], None] | None = case.get("준비")
        if prepare is not None:
            prepare(sheet)
        sheet.range("A4:F4").select()
        before = _snapshot(sheet)

        invalidate_excel_engine_cache()
        invalidate_workbook_digest()
        svc = get_excel_live_service()
        out["엔진"] = type(svc).__name__
        if out["엔진"] != "ExcelLiveService":
            out["왜"] = f"auto 가 xlwings 를 고르지 않았다({out['엔진']})"
            return out
        svc.select_workbook(str(path))

        llm = get_llm_service()
        resp = await post_command(
            ExcelLiveCommandRequest(
                message=str(case["명령"]), session_id=f"live-gate-{name}", workbook_id=None
            ),
            llm,
        )
        asked = bool(getattr(resp, "ask_follow_up", False)) or "clarify" in str(
            getattr(resp, "action", "")
        )
        carded = bool(getattr(resp, "approval_required", False))
        if carded and case["expect"] not in {"asks"}:
            pending = getattr(resp, "pending_approval", None)
            out["카드"] = str(getattr(pending, "summary", "") or "")[:80]
            resp = await post_approval(
                ApprovalResponse(approval_id=pending.approval_id, approved=True), llm
            )
        out["action"] = str(getattr(resp, "action", "")).replace("excel_live.", "")

        def cell(ref: str) -> Any:
            return sheet.range(ref).value

        # 사람이 친 줄이 살아 있는지 — 모든 경우에 공통이다.
        for ref, want in (case.get("불변") or {}).items():
            if not _same_value(cell(ref), want):
                out["왜"] = f"사람이 친 값이 바뀌었다: {ref} {want!r} → {cell(ref)!r}"
                return out

        expect = str(case["expect"])

        if expect in {"unchanged", "reads"}:
            after = _snapshot(sheet)
            if after != before:
                diffs = [
                    f"{chr(65 + c)}{r + 1}: {before[r][c]!r}→{after[r][c]!r}"
                    for r in range(min(len(before), len(after)))
                    for c in range(min(len(before[r]), len(after[r])))
                    if before[r][c] != after[r][c]
                ]
                out["왜"] = f"바뀌면 안 되는데 바뀌었다: {', '.join(diffs[:4])}"
                return out
            if expect == "reads" and not str(out.get("action") or ""):
                out["왜"] = "조회 액션이 잡히지 않았다"
                return out
            out["합격"] = True
            out["왜"] = "아무 칸도 바뀌지 않았다"
            return out

        if expect == "asks":
            if not (asked or carded):
                out["왜"] = "되묻지도 승인 카드도 없이 진행했다"
                return out
            after = _snapshot(sheet)
            if after != before:
                out["왜"] = "물어보면서 칸을 바꿨다"
                return out
            out["합격"] = True
            out["왜"] = "되묻기·승인 카드로 멈췄다"
            return out

        if expect == "bold":
            for ref, want in case["확인"].items():
                if bool(sheet.range(ref).font.bold) != bool(want):
                    out["왜"] = f"{ref} 굵게={sheet.range(ref).font.bold} (기대 {want})"
                    return out
            for ref in case.get("안굵음") or []:
                if sheet.range(ref).font.bold:
                    out["왜"] = f"{ref} 까지 굵어졌다"
                    return out
            out["합격"] = True
            return out

        if expect == "formula":
            for ref, needle in case["확인"].items():
                formula = str(sheet.range(ref).formula or "")
                if not formula.startswith("="):
                    out["왜"] = f"{ref} 가 수식이 아니다: {formula!r}"
                    return out
                if str(needle) not in formula:
                    out["왜"] = f"{ref} 수식에 {needle!r} 가 없다: {formula!r}"
                    return out
            out["합격"] = True
            return out

        if expect == "sorted":
            col = str(case["확인"]["열"])
            desc = bool(case["확인"].get("내림차순"))
            values = [sheet.range(f"{col}{r}").value for r in range(2, 6)]
            nums = [v for v in values if isinstance(v, (int, float))]
            if len(nums) < 3:
                out["왜"] = f"{col}2:{col}5 에 숫자가 {len(nums)}개뿐이다: {values}"
                return out
            want = sorted(nums, reverse=desc)
            if nums != want:
                out["왜"] = f"{col} 정렬 안 됨: {nums} (기대 {want})"
                return out
            # 저장 안 된 줄이 정렬에서 사라지지 않았는지
            products = [sheet.range(f"A{r}").value for r in range(2, 6)]
            if "키보드" not in [str(p) for p in products]:
                out["왜"] = f"정렬에서 저장 안 된 줄이 사라졌다: {products}"
                return out
            out["합격"] = True
            return out

        for ref, want in case["확인"].items():
            if not _same_value(cell(ref), want):
                out["왜"] = f"{ref} = {cell(ref)!r} (기대 {want!r})"
                return out
        out["합격"] = True
        return out

    except Exception as exc:
        out["왜"] = f"예외 {type(exc).__name__}: {exc}"
        out["트레이스"] = traceback.format_exc()[-400:]
        return out
    finally:
        out["초"] = round(time.time() - started, 1)
        for close in (
            lambda: book.close() if book is not None else None,
            app.quit,
            app.kill,
        ):
            try:
                close()
            except Exception:
                pass


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", action="append", help="경우 이름(여러 번 지정 가능)")
    ap.add_argument("--json", type=Path, help="결과를 이 경로에 남긴다")
    args = ap.parse_args()

    reason = skip_reason()
    if reason:
        print(f"건너뜀: {reason}")
        print("합격 0 / 0 (건너뜀)")
        return 0

    cases = [c for c in CASES if not args.only or str(c["이름"]) in set(args.only)]
    results = []
    for case in cases:
        row = await run_case(case)
        results.append(row)
        flag = "OK  " if row["합격"] else "FAIL"
        print(f"[{flag}] {row['이름']:12} {row['명령'][:40]}")
        detail = f"        action={row.get('action', '?')} ({row.get('초', '?')}초)"
        if row.get("카드"):
            detail += f" · 카드={row['카드'][:44]}"
        print(detail)
        if row["왜"]:
            print(f"        {row['왜']}")
        if not row["합격"]:
            print(f"        왜 중요한가: {case['왜']}")

    passed = sum(1 for r in results if r["합격"])
    print()
    print(f"합격 {passed} / {len(results)}")
    failed = [r["이름"] for r in results if not r["합격"]]
    if failed:
        print(f"실패: {', '.join(failed)}")

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(
                {"합격": passed, "전체": len(results), "실패": failed, "결과": results},
                ensure_ascii=False,
                indent=1,
            ),
            encoding="utf-8",
        )
        print(f"결과: {args.json}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
