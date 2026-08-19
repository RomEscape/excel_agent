"""로그 커버리지 프로브 — 대표 상황들을 실제로 채팅으로 돌리고 chat_log.jsonl에 각 구간이 남는지 확인한다.

2026-08-19 사용자 요청: "모든 상황을 다 고려해서 에이전트 내 모든 구간을 세세하게 탐색할 수 있도록
채팅을 진행하고 결과 보고가 가능한지 확인." 각 상황마다 기대하는 로그 필드/단계를 적어 두고,
실행 후 실제 기록과 대조해 표로 낸다. 빠진 칸이 곧 로그 구멍이다.

사용: PYTHONUTF8=1 EXCEL_LIVE_ENGINE=file python scripts/probe_trace_coverage.py
(다른 배터리와 동시에 돌리지 말 것.)
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import HTTPException
from openpyxl import Workbook

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
from office_claw_sidecar.services.llm_service import get_llm_service

WS = Path(r"C:\Users\asdjj\AppData\Local\office_claw\Workspace")
WB = WS / "trace_probe.xlsx"
CHAT_LOG = Path(__file__).resolve().parents[2] / "logs" / "chat_log.jsonl"
SESSION = "test-trace-probe"

SEED = [
    ["지역", "주문건수", "출고건수", "정시배송률", "지연건수", "클레임"],
    ["수도권", 10452, 10158, 97.1, 145, 12],
    ["충청권", 3892, 3773, 95.2, 89, 6],
    ["호남권", 3214, 3086, 94.7, 112, 5],
]

# (이름, 문장, context_range, 승인 여부(None=자동 승인, False=거절), 기대 구간)
CASES = [
    ("규칙 실행(쓰기)", "A6에 비고 라고 써줘", None, None, {"routes": ["quick_rule:hit"], "stages": ["understand"], "outcome": ["execution_report"]}),
    ("규칙 실행(집계 훅)", "합계를 표 아래에 한 줄로 넣어줘", "A1:F4", None, {"routes": ["quick_rule:hit", "approval:required"], "approval": ["executed"]}),
    ("오타 정규화", "함계 좀 표 밑에다 한줄로 부탁해", "A1:F4", None, {"notes": ["typo_normalized"]}),
    ("규칙 미스→모델", "클레임 10 넘는 셀만 빨간색으로 칠해줘", None, None, {"routes": ["quick_rule:miss", "planner:local"], "stages": ["llm_call", "planner", "plan_final"]}),
    ("되묻기", "정렬 좀", None, None, {"routes": ["final:asked_back"], "outcome": ["follow_up_question"]}),
    ("되묻기 답변(슬롯)", "주문건수 많은 순으로", None, None, {"stages": ["understand"], "approval_or_final": True}),
    ("부정문 noop", "아직 저장하지 마", None, None, {"outcome_action": ["excel_live.noop", "excel_live.clarify", "excel_live.not_excel_request"]}),
    ("붙여넣기 값 쓰기", "서울,100; 부산,200 입력해줘", "A8:B9", None, {"routes": ["quick_rule:hit"], "request": ["context_range"]}),
    ("값 없는 붙여넣기", "여기에 입력해줘", "A8:B9", None, {"routes": ["final:asked_back"]}),
    ("승인 거절", "A1:F4에 테두리 그려줘", None, False, {"approval_rejected": True}),
    ("없는 시트(대상 문제)", "없는시트 시트 B2에 100 넣어줘", None, None, {"outcome_any": True}),
    ("엑셀 아님", "오늘 날씨 어때?", None, None, {"outcome_action": ["excel_live.not_excel_request", "excel_live.clarify"]}),
    ("크로스시트 수식", "H1에 지역성과 시트 주문건수 합계 가져와줘", None, None, {"approval": ["executed"], "outcome": ["approval_summary"]}),
    ("차트", "정시배송률 추이를 선 그래프로 그려줘", None, None, {"approval": ["executed"]}),
]


def _tail_records(n: int = 400) -> list[dict]:
    lines = CHAT_LOG.read_text(encoding="utf-8", errors="ignore").splitlines()[-n:]
    out = []
    for ln in lines:
        try:
            out.append(json.loads(ln))
        except Exception:
            continue
    return out


async def main() -> None:
    if WB.exists():
        WB.unlink()
    wb = Workbook()
    ws = wb.active
    ws.title = "지역성과"
    for r in SEED:
        ws.append(r)
    wb.save(WB)
    wb.close()
    llm = get_llm_service()
    start_count = len(CHAT_LOG.read_text(encoding="utf-8", errors="ignore").splitlines()) if CHAT_LOG.exists() else 0
    marks: list[tuple[str, str, str]] = []  # (case, turn_id of command, approval turn id)
    for name, text, ctx, approve, _exp in CASES:
        invalidate_excel_engine_cache()
        get_excel_live_service().select_workbook(str(WB))

        try:
            resp = await post_command(
                ExcelLiveCommandRequest(message=text, session_id=SESSION, workbook_id=None, approve=False, context_range=ctx),
                llm,
            )
            if getattr(resp, "approval_required", False):
                pending = resp.pending_approval
                if approve is False:
                    await post_approval(ApprovalResponse(approval_id=pending.approval_id, approved=False), llm)
                else:
                    await post_approval(ApprovalResponse(approval_id=pending.approval_id, approved=True), llm)
        except HTTPException as exc:
            print(f"  ({name}) HTTPException {exc.status_code}: {exc.detail}")
        except Exception as exc:
            print(f"  ({name}) 예외 {type(exc).__name__}: {exc}")
        marks.append((name, text, ""))

    recs = [r for r in _tail_records(3000) if r.get("session_id") == SESSION]
    print(f"\n세션 {SESSION} 레코드 {len(recs)}개 (command {sum(1 for r in recs if r.get('endpoint')=='excel-live/command')} · approval {sum(1 for r in recs if r.get('endpoint')=='excel-live/approval')})\n")
    print("| 상황 | 문장 | command 줄 | routes | stages | llm_call | outcome 핵심 | approval 줄 |")
    print("|---|---|---|---|---|---|---|---|")
    by_msg: dict[str, list[dict]] = {}
    for r in recs:
        by_msg.setdefault(str(r.get("user_input") or r.get("message") or ""), []).append(r)
    for name, text, _ctx, _approve, _exp in CASES:
        cand = [r for r in recs if r.get("endpoint") == "excel-live/command" and (r.get("message") == text or r.get("user_input") == text)]
        # 오타 정규화된 문장은 message가 바뀐다 — user_input으로 잡는다.
        if not cand:
            cand = [r for r in recs if r.get("endpoint") == "excel-live/command" and text[:6] in str(r.get("user_input") or "")]
        cmd = cand[-1] if cand else None
        apr = [r for r in recs if r.get("endpoint") == "excel-live/approval" and str(r.get("user_input") or "").startswith(text[:8])]
        if cmd is None:
            print(f"| {name} | {text} | **없음** | | | | | |")
            continue
        routes = " → ".join(str(x.get("at")) for x in cmd.get("routes", []))
        stages = ",".join(str(s.get("stage")) for s in cmd.get("stages", []))
        llm_calls = [s for s in cmd.get("stages", []) if s.get("stage") == "llm_call"]
        llm_txt = f"{len(llm_calls)}회 " + ",".join(str(s.get("model", ""))[:18] for s in llm_calls) if llm_calls else "-"
        oc = cmd.get("outcome") or {}
        oc_txt = (f"action={oc.get('action')} ask={oc.get('ask_follow_up')} appr={oc.get('approval_required')} "
                  f"interp={oc.get('interpretation')} err={oc.get('error_type', '')}")
        notes = [s for s in cmd.get("stages", []) if s.get("stage") == "typo_normalized"]
        if notes:
            oc_txt += " typo✓"
        apr_txt = "; ".join(
            f"{'거절' if (r.get('origin') or {}).get('approved') is False else '실행'} steps={len((r.get('stages') or [{}])[0].get('steps', []) or [])}"
            for r in apr[-1:]
        ) or "-"
        print(f"| {name} | {text[:22]} | ✓ {cmd.get('turn_id')} | {routes} | {stages} | {llm_txt} | {oc_txt} | {apr_txt} |")
    print(f"\nchat_log 증가: {len(CHAT_LOG.read_text(encoding='utf-8', errors='ignore').splitlines()) - start_count}줄")


from office_claw_sidecar.services import decision_trace as _dt

# 배터리 턴을 사람이 친 명령과 가를 출처 태그(2026-08-19 로그 감사: 실사용 로그의 source가 전부 비어 있었다).
_dt.source(kind="script", name="trace_probe").__enter__()
asyncio.run(main())
