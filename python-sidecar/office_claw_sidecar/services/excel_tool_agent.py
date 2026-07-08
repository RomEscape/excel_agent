"""
Excel Tool-Calling 에이전트 — Ollama OpenAI 호환 function calling 기반.

기존 정규식 파서(excel_live_agent.py)를 대체한다. LLM에 excel_tool_schemas의
tools 배열을 전달하고, 응답의 tool_calls를 해석해:

  - SAFE 도구    → 즉시 실행하고 결과를 tool 메시지로 재주입 후 루프 계속
  - CONFIRM 도구 → 실행하지 않고 승인 대기 정보를 반환. 라우터가 승인 흐름을
                   처리하고, 승인되면 `resume_excel_tool_turn`으로 루프를 재개한다.
  - DENIED 도구  → 거부 사유를 tool 메시지로 재주입 (LLM이 사용자에게 설명)
  - tool_calls 없음 → 일반 대화 응답으로 종료

턴 결과 형식:
  {"type": "chat",     "assistant_text": str, "executed": [...]}
  {"type": "approval", "action": str, "params": dict, "sheet_name": str|None,
                        "reason": str, "executed": [...],
                        "resume": {"messages": [...], "tool_call_id": str}}

B안(승인 후 재개): approval의 `resume`는 승인 대기 CONFIRM 직전까지의 대화
상태를 담는다. 라우터가 승인 시 이 상태에 실행 결과를 tool 메시지로 덧붙여
`resume_excel_tool_turn`을 호출하면, LLM이 결과를 보고 다음 단계(추가 도구
호출 또는 최종 답변)를 이어간다. 한 문장에 여러 변경 작업이 있어도 승인→실행
→다음 승인 흐름이 자연스럽게 연결된다.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from office_claw_sidecar.services.audit_service import AuditService
from office_claw_sidecar.services.excel_actions import execute_excel_action
from office_claw_sidecar.services.excel_live_service import (
    ExcelLiveError,
    get_excel_live_service,
)
from office_claw_sidecar.services.excel_tool_schemas import (
    get_excel_tools,
    tool_name_to_action,
    validate_tool_params,
)
from office_claw_sidecar.services.llm_service import LLMService
from office_claw_sidecar.services.tool_registry import PermissionLevel, get_tool

logger = logging.getLogger(__name__)
_audit = AuditService()

# LLM이 도구를 연쇄 호출할 수 있는 최대 라운드 수 (무한 루프 방지)
MAX_TOOL_ROUNDS = 4

# qwen3 등 thinking 모델이 content에 남기는 사고 블록 제거용
_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)

_SYSTEM_PROMPT_TEMPLATE = """당신은 사용자의 데스크톱에서 실행 중인 Excel을 제어하는 한국어 업무 비서입니다.

규칙:
- Excel 작업 요청이면 반드시 제공된 함수(tools)를 호출해 처리합니다. 함수 이름·파라미터를 텍스트로 설명하지 말고 실제로 호출하세요.
- 셀 주소·범위는 A1 표기(예: 'B2', 'A1:C10', 'D:D')를 사용합니다.
- 사용자가 범위를 말하지 않으면 해당 파라미터를 생략해 현재 선택 범위가 사용되게 합니다.
- 함수 실행 결과(tool 메시지)를 받으면 그 값을 근거로 한국어로 간결하게 답합니다. 결과를 지어내지 마세요.
- Excel과 무관한 일반 질문이면 함수를 호출하지 말고 한국어로 바로 답합니다.

{context}"""


def _strip_think_blocks(text: str) -> str:
    return _THINK_BLOCK_RE.sub("", text or "").strip()


def _build_excel_context() -> str:
    """열린 통합문서/선택 상태를 시스템 프롬프트용 문자열로 구성한다 (실패 시 축약)."""
    try:
        service = get_excel_live_service()
        rows = service.list_workbooks()
    except Exception:
        return "현재 Excel 연결 상태: 확인 불가 (Excel이 실행 중이 아닐 수 있음)"
    if not rows:
        return "현재 Excel 연결 상태: 열린 통합문서 없음"

    selected = None
    try:
        selected = service.get_selected_workbook_id()
    except Exception:
        pass

    lines = ["현재 열린 통합문서:"]
    for row in rows:
        marker = " (선택됨)" if selected and row.get("workbook_id") == selected else ""
        lines.append(
            f"- {row.get('name', '')} / 활성 시트: {row.get('active_sheet', '')}{marker}"
        )
    return "\n".join(lines)


def _parse_tool_arguments(raw: Any) -> dict[str, Any]:
    """tool_calls의 arguments(JSON 문자열 또는 dict)를 dict로 정규화한다."""
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str) and raw.strip():
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError(f"arguments가 객체가 아닙니다: {type(parsed).__name__}")
        return parsed
    return {}


def _tool_result_message(tool_call_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "role": "tool",
        "tool_call_id": tool_call_id or "",
        "content": json.dumps(payload, ensure_ascii=False, default=str),
    }


async def _run_tool_loop(
    *,
    messages: list[dict[str, Any]],
    llm_service: LLMService,
    workbook_id: str | None,
    sheet_name: str | None,
    executed: list[dict[str, Any]],
    rounds_left: int,
) -> dict[str, Any]:
    """
    tool-calling 핵심 루프. messages 리스트를 in-place로 갱신하며 진행한다.

    한 라운드의 tool_calls를 순서대로 처리하다가 첫 CONFIRM을 만나면,
    OpenAI 프로토콜 유효성(미응답 tool_call은 그 CONFIRM 하나만 남도록)을 위해
    assistant 메시지의 tool_calls를 CONFIRM까지로 잘라 남기고 승인 대기로 반환한다.
    """
    tools = get_excel_tools()

    for _ in range(rounds_left):
        reply = await llm_service.chat_with_tools(messages, tools=tools)
        tool_calls = reply.get("tool_calls") or []

        if not tool_calls:
            return {
                "type": "chat",
                "assistant_text": _strip_think_blocks(reply.get("content", "")),
                "executed": executed,
            }

        # 처리된 tool_call들의 tool 결과 메시지 (순서 유지)
        handled: list[dict[str, Any]] = []
        confirm_idx: int | None = None
        confirm_ctx: dict[str, Any] | None = None

        for idx, tool_call in enumerate(tool_calls):
            call_id = str(tool_call.get("id", "") or "")
            function = tool_call.get("function") or {}
            tool_name = str(function.get("name", "") or "")
            action = tool_name_to_action(tool_name)

            if action is None:
                handled.append(
                    _tool_result_message(call_id, {"error": f"알 수 없는 함수입니다: {tool_name}"})
                )
                continue

            try:
                params = _parse_tool_arguments(function.get("arguments"))
            except (json.JSONDecodeError, ValueError) as exc:
                handled.append(
                    _tool_result_message(call_id, {"error": f"함수 인자 파싱 실패: {exc}"})
                )
                continue

            # Pydantic 모델로 인자 사전 검증 — 실패 메시지는 LLM에 재주입해 자가 교정 유도
            try:
                params = validate_tool_params(action, params)
            except ValueError as exc:
                handled.append(_tool_result_message(call_id, {"error": str(exc)}))
                continue

            # 스키마의 sheet_name 파라미터는 실행 레벨 시트 지정으로 승격
            call_sheet = str(params.pop("sheet_name", "") or "").strip() or sheet_name

            tool_def = get_tool(action)
            if tool_def is not None and tool_def.permission == PermissionLevel.DENIED:
                handled.append(
                    _tool_result_message(call_id, {"error": "보안 정책에 의해 거부된 작업입니다."})
                )
                continue

            if tool_def is not None and tool_def.permission == PermissionLevel.CONFIRM:
                confirm_idx = idx
                confirm_ctx = {
                    "action": action,
                    "params": params,
                    "sheet_name": call_sheet,
                    "call_id": call_id,
                    "reason": _strip_think_blocks(reply.get("content", "")),
                }
                break

            # SAFE 실행
            try:
                result = execute_excel_action(
                    action=action,
                    params=params,
                    workbook_id=workbook_id,
                    sheet_name=call_sheet,
                )
                _audit.log(
                    action="excel.live.tool_call",
                    target=action,
                    detail=f"ok=True workbook={workbook_id or ''}",
                )
                executed.append({"action": action, "params": params, "result": result})
                handled.append(_tool_result_message(call_id, result))
            except ExcelLiveError as exc:
                # 실행 오류는 LLM에 그대로 알려 사용자에게 설명하게 한다
                handled.append(_tool_result_message(call_id, {"error": str(exc)}))
            except Exception as exc:  # pragma: no cover - 방어적 처리
                logger.exception("excel tool 실행 중 예상치 못한 오류: %s", action)
                handled.append(_tool_result_message(call_id, {"error": f"실행 오류: {exc}"}))

        if confirm_idx is not None and confirm_ctx is not None:
            # 승인 대기 CONFIRM까지만 assistant 메시지에 남긴다. 이후 tool_calls는 폐기하고
            # (남으면 미응답 상태가 되어 프로토콜 위반) 다음 라운드에서 LLM이 다시 계획하게 한다.
            messages.append(
                {
                    "role": "assistant",
                    "content": reply.get("content") or "",
                    "tool_calls": tool_calls[: confirm_idx + 1],
                }
            )
            messages.extend(handled)  # [0, confirm_idx) 구간의 결과
            return {
                "type": "approval",
                "action": confirm_ctx["action"],
                "params": confirm_ctx["params"],
                "sheet_name": confirm_ctx["sheet_name"],
                "reason": confirm_ctx["reason"],
                "executed": executed,
                "resume": {"messages": messages, "tool_call_id": confirm_ctx["call_id"]},
            }

        # CONFIRM 없음 — 전체 tool_calls + 결과를 반영하고 다음 라운드로
        messages.append(
            {
                "role": "assistant",
                "content": reply.get("content") or "",
                "tool_calls": tool_calls,
            }
        )
        messages.extend(handled)

    # 라운드 초과 — 마지막까지 tool_calls만 반복된 경우
    return {
        "type": "chat",
        "assistant_text": "요청을 처리하는 동안 함수 호출이 반복되어 중단했습니다. 명령을 더 구체적으로 나눠서 다시 시도해 주세요.",
        "executed": executed,
    }


async def run_excel_tool_turn(
    *,
    message: str,
    llm_service: LLMService,
    workbook_id: str | None = None,
    sheet_name: str | None = None,
    history: list[dict] | None = None,
) -> dict[str, Any]:
    """자연어 한 턴을 tool-calling 루프로 처리한다."""
    system_prompt = _SYSTEM_PROMPT_TEMPLATE.format(context=_build_excel_context())
    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    for turn in history or []:
        role = str(turn.get("role", "")).strip()
        content = str(turn.get("content", "") or "")
        if role in {"user", "assistant"} and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": message})

    return await _run_tool_loop(
        messages=messages,
        llm_service=llm_service,
        workbook_id=workbook_id,
        sheet_name=sheet_name,
        executed=[],
        rounds_left=MAX_TOOL_ROUNDS,
    )


async def resume_excel_tool_turn(
    *,
    resume: dict[str, Any],
    action: str,
    params: dict[str, Any],
    workbook_id: str | None,
    sheet_name: str | None,
    llm_service: LLMService,
) -> dict[str, Any]:
    """
    승인된 CONFIRM 작업을 실행하고, 그 결과를 LLM에 재주입해 루프를 이어간다 (B안).

    반환은 run_excel_tool_turn과 동일한 형식 — 다음 CONFIRM이 또 있으면 approval,
    없으면 chat(assistant_text)로 종료된다.
    """
    messages: list[dict[str, Any]] = resume["messages"]
    tool_call_id: str = resume["tool_call_id"]
    executed: list[dict[str, Any]] = []

    try:
        result = execute_excel_action(
            action=action,
            params=params,
            workbook_id=workbook_id,
            sheet_name=sheet_name,
        )
        _audit.log(
            action="excel.live.approval.executed",
            target=action,
            detail=f"workbook={workbook_id or ''}",
        )
        executed.append({"action": action, "params": params, "result": result})
        messages.append(_tool_result_message(tool_call_id, result))
    except ExcelLiveError as exc:
        # 실행 오류도 LLM에 전달해 자연어로 설명하게 한다
        messages.append(_tool_result_message(tool_call_id, {"error": str(exc)}))

    return await _run_tool_loop(
        messages=messages,
        llm_service=llm_service,
        workbook_id=workbook_id,
        sheet_name=sheet_name,
        executed=executed,
        rounds_left=MAX_TOOL_ROUNDS,
    )
