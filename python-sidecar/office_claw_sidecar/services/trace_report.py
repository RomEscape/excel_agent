"""`chat_log.jsonl` 한 줄을 사람이 읽는 형태로 펼친다.

턴 로그는 JSON 한 줄이라 기계는 잘 읽지만 사람은 못 읽는다. 실패한 명령 하나를
들여다볼 때 필요한 건 "무엇을 보고, 무엇을 고르고, 무엇을 실행하고, 검증이
어떻게 나왔는가"를 위에서 아래로 읽는 것이다.

    [USER]        매출 높은 순으로 정렬해줘
    [OBSERVATION] sheet=Sheet1 used_range=A1:F100
                  headers=날짜, 상품, 수량, 단가, 매출, 담당자
    [ROUTE]       quick_rule:miss → planner:local → final:failed
    [PLAN]        excel_live.sort_range {"key_column":"수량","order":"desc"}
    [EXECUTION]   ok
    [VERIFY]      실패 — sort_not_applied
    [FINAL]       실패 (검증 실패 · 재계획 없음)

이 형태로 봐야 원인을 관측/선택/인자/실행/검증/루프 중 하나로 가를 수 있다.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from office_claw_sidecar.config import get_chat_log_path

# 실패를 어디 탓으로 볼 것인가. 위에서부터 먼저 맞는 것을 쓴다 —
# 실행이 터졌으면 그게 원인이지, 그 뒤 검증 결과를 볼 필요가 없다.
_UNKNOWN = ("unknown", "분류 불가")

# 판정 코드를 세 부류로 접는다. 진단할 때 알고 싶은 것은 "성공/실패"가 아니라
# **일을 했는가**이기 때문이다. 되물음과 승인 대기는 에러가 아니지만 사용자 입장에서
# 아무것도 안 된 것은 같다. 이 둘을 성공 쪽에 세면 실제 이행률이 부풀려진다.
DONE = "done"  # 요청한 변경이 실제로 들어갔다
DEFERRED = "deferred"  # 되묻거나 승인을 기다린다 — 파일은 그대로다
BROKEN = "broken"  # 깨졌다

_OUTCOME_CLASS = {
    "ok": DONE,
    "verify_recovered": DONE,
    "asked_back": DEFERRED,
    "approval": DEFERRED,
}


def outcome_class(code: str) -> str:
    """판정 코드를 done/deferred/broken 중 하나로."""
    return _OUTCOME_CLASS.get(str(code), BROKEN)


@dataclass(frozen=True)
class Verdict:
    """이 턴이 어떻게 끝났고 그 책임이 어디에 있는가."""

    code: str
    label: str
    detail: str = ""

    def __str__(self) -> str:
        return f"{self.label}{f' — {self.detail}' if self.detail else ''}"


def _routes(turn: dict[str, Any]) -> list[dict[str, Any]]:
    return [r for r in (turn.get("routes") or []) if isinstance(r, dict)]


def _stage(turn: dict[str, Any], name: str) -> dict[str, Any]:
    for entry in reversed(turn.get("stages") or []):
        if isinstance(entry, dict) and entry.get("stage") == name:
            return entry
    return {}


def _has_route(turn: dict[str, Any], prefix: str) -> dict[str, Any] | None:
    for entry in _routes(turn):
        if str(entry.get("at", "")).startswith(prefix):
            return entry
    return None


def executed_actions(turn: dict[str, Any]) -> list[str]:
    """이 턴이 실제로 실행한 액션 이름들. 계획이 아니라 실행 기록에서 뽑는다."""
    steps = _stage(turn, "executed").get("steps") or []
    return [str(s.get("action", "")) for s in steps if isinstance(s, dict) and s.get("action")]


def goal_missed(turn: dict[str, Any]) -> str:
    """요청이 당연히 포함해야 할 액션이 실행되지 않았으면 그 이름을 준다.

    검증기는 **실행한 액션**의 사후조건을 본다. 차트를 만들라는 요청에 피벗만
    만들고 끝내도, 피벗은 제대로 만들어졌으므로 "검증 통과 · 성공"이 된다.
    그래서 시스템이 스스로 매기는 판정만으로는 이 실패가 영원히 보이지 않는다.

    기대 액션은 턴 자신이 `source.expect`로 들고 다닌다. 진단 러너가 붙이므로
    나중에 쌓인 로그를 다시 읽어도 판정이 재현된다.
    """
    origin = turn.get("source") or {}
    expected = str(origin.get("expect", "") or "") if isinstance(origin, dict) else ""
    if not expected:
        return ""
    return "" if expected in executed_actions(turn) else expected


def route_path(turn: dict[str, Any]) -> str:
    """경로를 한 줄로 잇는다. 재시도로 같은 칸을 두 번 지나면 ×2로 접는다."""
    steps: list[list[Any]] = []
    for entry in _routes(turn):
        name = str(entry.get("at", ""))
        if steps and steps[-1][0] == name:
            steps[-1][1] += 1
        else:
            steps.append([name, 1])
    return " → ".join(name if n == 1 else f"{name}×{n}" for name, n in steps)


def source_label(turn: dict[str, Any]) -> str:
    """이 턴을 만든 주체를 한 조각 문자열로. 사람이 친 명령이면 빈 문자열."""
    origin = turn.get("source") or {}
    if not isinstance(origin, dict) or not origin:
        return ""
    for key in ("test", "case", "suite"):
        if origin.get(key):
            return str(origin[key])
    return str(origin.get("kind", ""))


def classify(turn: dict[str, Any]) -> Verdict:
    """트레이스만 보고 결론과 책임 지점을 정한다.

    로그에 실제로 남은 사실로만 판단한다. "모델이 엉뚱한 열을 골랐다" 같은 인자
    오류는 사용자 의도를 알아야 하므로 자동 분류하지 않는다 — 그건 사람이
    OBSERVATION과 PLAN을 나란히 놓고 봐야 한다.
    """
    outcome = turn.get("outcome") or {}

    if outcome.get("error_type"):
        return Verdict(
            "crash",
            "요청 처리 중 예외",
            f"{outcome.get('error_type')}: {outcome.get('error', '')}",
        )

    executed = _has_route(turn, "execute:error")
    if executed:
        return Verdict("executor", "실행 오류", str(executed.get("why", "")))

    if _has_route(turn, "planner:json_missing"):
        return Verdict("planner_output", "플래너 출력 파싱 실패", "응답에서 JSON을 찾지 못함")

    verify_failed = _has_route(turn, "verify:failed")
    if verify_failed:
        replanned = _has_route(turn, "replan:")
        if outcome.get("ok"):
            return Verdict("verify_recovered", "검증 실패 후 재계획으로 복구", str(verify_failed.get("why", "")))
        if replanned:
            return Verdict("verify_failed", "검증 실패 · 재계획도 실패", str(verify_failed.get("why", "")))
        return Verdict("loop_missing", "검증 실패 · 재계획 없음", str(verify_failed.get("why", "")))

    if _has_route(turn, "final:approval_required"):
        return Verdict("approval", "승인 대기", "")
    if _has_route(turn, "final:asked_back") or outcome.get("ask_follow_up"):
        return Verdict("asked_back", "되물음", str(outcome.get("follow_up_question", "")))

    if outcome.get("ok") is True:
        return Verdict("ok", "성공", "")
    if outcome.get("ok") is False:
        return Verdict("failed", "실패", str(outcome.get("failure_detail", "")))
    return Verdict(*_UNKNOWN)


def _plan_line(step: dict[str, Any]) -> str:
    action = str(step.get("action", ""))
    params = step.get("params")
    if isinstance(params, dict) and params:
        return f"{action} {json.dumps(params, ensure_ascii=False)}"
    return action


def render(turn: dict[str, Any], *, show_prompt: bool = False) -> str:
    """턴 하나를 위 예시 형태의 여러 줄 텍스트로 만든다."""
    lines: list[str] = []

    def add(tag: str, text: str) -> None:
        lines.append(f"  {tag:<14}{text}")

    def cont(text: str) -> None:
        lines.append(f"  {'':<14}{text}")

    outcome = turn.get("outcome") or {}
    verdict = classify(turn)

    origin = turn.get("source") or {}
    lines.append(
        f"  ── {turn.get('at', '')}  turn={turn.get('turn_id', '')}  "
        f"{turn.get('elapsed_ms', 0)}ms"
        + (f"  [{source_label(turn)}]" if origin else "")
    )
    # 사람이 실제로 요구한 말과 이 턴이 처리한 문장이 다를 수 있다(매크로 하위 단계·승인).
    # 원문을 먼저 보여 주지 않으면 19줄짜리 기계 문장만 남아 무엇에서 비롯됐는지 알 수 없다.
    message = str(turn.get("message", ""))
    lineage = turn.get("origin") or {}
    user_input = str(turn.get("user_input") or "")
    if user_input and user_input != message:
        kind = str(lineage.get("kind") or "")
        step = lineage.get("step_index")
        total = lineage.get("total_steps")
        tail = ""
        if kind == "macro_step" and step:
            tail = f"  (매크로 {step}/{total} — {lineage.get('macro_id', '')[:8]})"
        elif kind == "approval":
            tail = "  (승인함)" if lineage.get("approved") else "  (거부함)"
        add("[USER]", f"{user_input}{tail}")
        add("[STEP]", message)
    else:
        add("[USER]", message)
        if str(lineage.get("kind") or "") == "approval":
            cont("(승인함)" if lineage.get("approved") else "(거부함)")
    if lineage.get("user_reply"):
        add("[답변]", str(lineage["user_reply"]))

    obs = _stage(turn, "observation")
    if obs:
        add(
            "[OBSERVATION]",
            f"sheet={obs.get('sheet_name', '')} used_range={obs.get('used_range') or '(없음)'} "
            f"context_range={obs.get('context_range', '')}",
        )
        headers = obs.get("headers") or []
        cont(f"headers={', '.join(str(h) for h in headers) if headers else '(머리글 없음)'}")
        sheets = obs.get("sheets") or []
        if len(sheets) > 1:
            cont(f"sheets={', '.join(str(s) for s in sheets)}")

    add("[ROUTE]", route_path(turn) or "(경로 기록 없음)")

    llm = _stage(turn, "llm_call")
    if llm:
        add("[LLM]", f"{llm.get('model', '')} {llm.get('elapsed_ms', 0)}ms")
        if show_prompt:
            cont(f"prompt_chars={llm.get('prompt_chars')}")
            # 모델이 이 턴에 실제로 본 시트 상태. 지어낸 열 이름을 만났을 때
            # 여기가 비어 있으면 프롬프트 탓, 채워져 있으면 모델 탓이다.
            given = _stage(turn, "planner_context")
            cont(f"통합문서 상태={given.get('workbook_digest') or '(비어 있음)'}")
            if given.get("conversation_history"):
                cont(f"이전 대화={given.get('conversation_history')}")
            cont(f"raw={llm.get('raw_response', '')}")

    planner = _stage(turn, "planner")
    if planner:
        add(
            "[PLANNER]",
            f"intent={planner.get('intent') or '(없음)'} tier={planner.get('escalation_tier') or '-'}"
            + (f" error={planner.get('error')}" if planner.get("error") else ""),
        )

    final_plan = _stage(turn, "plan_final")
    steps = final_plan.get("steps") or planner.get("action_plan") or []
    for step in steps:
        if isinstance(step, dict):
            add("[PLAN]", _plan_line(step))

    executed = _stage(turn, "executed")
    for step in executed.get("steps") or []:
        if not isinstance(step, dict):
            continue
        state = "ok" if step.get("ok") else f"실패({step.get('error', '')})"
        add("[EXECUTION]", f"{step.get('action', '')} → {state}")
        if step.get("verified") is False:
            cont(f"[VERIFY] 실패 — {step.get('verify_detail') or '(사유 없음)'}")
        elif step.get("verified"):
            cont("[VERIFY] 통과")
    if executed.get("replans"):
        cont(f"재계획 {executed.get('replans')}회")

    rollbacks = outcome.get("auto_rollbacks") or []
    if rollbacks:
        add("[ROLLBACK]", ", ".join(f"{r.get('action', '')}({r.get('reason', '')})" for r in rollbacks if isinstance(r, dict)))

    add("[FINAL]", str(verdict))
    if outcome.get("reason"):
        cont(str(outcome.get("reason")))
    # 사용자가 화면에서 읽은 답변 그대로 — 무엇을 했다고 보고했고(실행 리포트),
    # 무엇을 승인해 달라 했는지(승인 카드). 되묻기 질문은 verdict가 이미 보여준다.
    if outcome.get("execution_report"):
        add("[REPLY]", str(outcome.get("execution_report")))
    if outcome.get("approval_summary"):
        add("[APPROVAL]", str(outcome.get("approval_summary")))
    return "\n".join(lines)


def read_turns(path: Path | None = None) -> Iterator[dict[str, Any]]:
    """chat_log.jsonl을 위에서부터 읽는다. 깨진 줄은 건너뛴다."""
    log_path = path or get_chat_log_path()
    if not log_path.exists():
        return
    with log_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(entry, dict):
                yield entry
