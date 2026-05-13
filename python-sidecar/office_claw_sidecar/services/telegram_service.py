"""
TelegramService — Phase 1 + Phase 2 (Private-Claw) 텔레그램 에이전트.

단일 진입점: 모든 봇 로직은 이 클래스에 집중된다.
messenger/telegram.py는 자연어 패턴 정규식 유틸만 제공하며,
봇 빌드·폴링·명령 처리는 이 파일이 단독으로 담당한다.

아키텍처:
  메시지 수신
    → Phase 2 CommandAnalyzer로 코드 사전 분석 (SAFE/CONFIRM/DENIED)
    → Phase 1 워크스페이스 명령 우선 처리 (패턴 매칭)
    → IntentRouter로 tool 분류
    → ToolRegistry 권한 확인 (SAFE / CONFIRM / DENIED)
    → 도구 실행 → 결과 전송

지원 도구 (화이트리스트만):
  ws.list    — 워크스페이스 파일 목록
  ws.read    — 워크스페이스 파일 읽기
  ws.write   — 워크스페이스 파일 쓰기
  chat                  — 대화 히스토리 유지 멀티턴 채팅
  gmail.fetch_emails    — 받은 메일 목록 + 중요도 분류
  gmail.summarize_recent — 최근 중요 메일 요약
  document.generate     — 업무 문서 초안 작성
  status.check          — 시스템 상태 확인
  help                  — 명령어 목록

보안 (Phase 2):
  - 에이전트 생성 코드/스크립트는 CommandAnalyzer로 사전 분석
  - DENIED 등급은 즉시 차단, 감사 로그 기록
  - CONFIRM 등급은 텔레그램 InlineKeyboard로 [승인]/[거부] 요청
  - HITL 승인 타임아웃: 60초 (초과 시 자동 DENIED)
  - 파일 삭제, 셸 실행 등 DENIED 도구는 키워드 단계에서 즉시 차단
  - LLM이 임의로 도구를 추가할 수 없음 — ToolRegistry 화이트리스트 이중 검증
"""

from __future__ import annotations

import asyncio
import logging
import re
import tempfile
from collections import deque
from pathlib import Path
from typing import Any

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from office_claw_sidecar.services.audit_service import AuditService
from office_claw_sidecar.services.keyring_service import KeyringService
from office_claw_sidecar.services.gmail_service import GmailService
from office_claw_sidecar.services.filter_service import FilterService
from office_claw_sidecar.services.intent_router import IntentRouter
from office_claw_sidecar.services.tool_registry import PermissionLevel, get_tool
from office_claw_sidecar.services.llm_service import get_llm_service
from office_claw_sidecar.services import document_service
from office_claw_sidecar import sandbox
from office_claw_sidecar.analyzer import get_analyzer
from office_claw_sidecar.command_audit import get_command_audit_logger
# 자연어 패턴 유틸 — messenger/telegram.py가 단일 출처
from office_claw_sidecar.messenger.telegram import (
    _PATTERN_LIST,
    _extract_filename,
    _parse_write_command,
)

logger = logging.getLogger(__name__)
audit = AuditService()
keyring_svc = KeyringService()

BOT_TOKEN_KEY = "telegram_bot_token"
CHAT_ID_KEY = "telegram_chat_id"

# 대화 히스토리 최대 턴 수 (user + assistant 메시지 합산)
_MAX_HISTORY = 20

# Phase 2: HITL 승인 타임아웃 (초)
_HITL_TIMEOUT_SECONDS = 60


async def _push_ui_approval(command: str, reason: str, audit_id: int) -> bool:
    """
    메신저 봇이 꺼져 있을 때 앱 UI의 _pending_ui_approvals 큐에 승인 요청을 등록하고
    응답을 최대 _HITL_TIMEOUT_SECONDS 초 대기한다.

    - 승인 응답 수신 → True
    - 거부 / 타임아웃 → False
    """
    from office_claw_sidecar.routers import security as sec_router
    import uuid

    approval_id = uuid.uuid4().hex
    resp_event: asyncio.Event = asyncio.Event()

    sec_router._pending_ui_approvals[approval_id] = {
        "command": command,
        "reason": reason,
        "audit_id": audit_id,
        "responded": False,
        "approved": False,
        "_event": resp_event,  # 내부 wakeup 용 (respond_to_approval에서 set)
    }
    logger.info("[HITL-UI] 앱 UI 승인 요청 등록: approval_id=%s reason=%s", approval_id, reason)

    try:
        await asyncio.wait_for(resp_event.wait(), timeout=_HITL_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        logger.info("[HITL-UI] approval_id=%s 타임아웃 — 자동 거부", approval_id)
        sec_router._pending_ui_approvals.pop(approval_id, None)
        return False

    entry = sec_router._pending_ui_approvals.pop(approval_id, {})
    return entry.get("approved", False)

_HELP_TEXT = (
    "🤖 *ajou-ai 에이전트*\n\n"
    "자연어로 말씀하시면 됩니다. 예시:\n\n"
    "📧 *이메일*\n"
    "• `메일 확인해줘`\n"
    "• `중요한 메일 요약해줘`\n\n"
    "📄 *문서 작성*\n"
    "• `회의록 작성해줘: [내용]`\n"
    "• `기획안 써줘: [핵심 내용]`\n"
    "• `보고서, 이메일 초안, 제안서도 가능`\n\n"
    "💬 *일반 대화*\n"
    "• 궁금한 것, 설명 요청 등 자유롭게\n\n"
    "⚙️ *명령어*\n"
    "• /status — 시스템 상태\n"
    "• /help — 이 도움말\n"
    "• /confirm — 대기 중인 작업 실행\n"
    "• /cancel — 대기 중인 작업 취소"
)


class TelegramService:
    """텔레그램 에이전트 — Intent Router + Tool Registry 기반."""

    def __init__(self) -> None:
        self._app: Application | None = None
        self._running = False
        self._gmail = GmailService()
        self._filter = FilterService()
        self._intent_router = IntentRouter()

        # chat_id → 대화 히스토리 (deque)
        self._history: deque[dict] = deque(maxlen=_MAX_HISTORY)

        # CONFIRM 대기 중인 작업: {"tool": str, "params": dict, "description": str}
        self._pending_action: dict[str, Any] | None = None

        # Phase 2: HITL 승인 대기 중인 요청 맵
        # key: callback_data prefix (예: "hitl_approve_<id>" / "hitl_reject_<id>")
        # value: {"event": asyncio.Event, "approved": bool, "audit_id": int, "command": str}
        self._pending_hitl: dict[str, dict[str, Any]] = {}

    # ── 설정 ─────────────────────────────────────────────────────────────────

    def _get_config(self) -> tuple[str, str]:
        token = keyring_svc.retrieve(BOT_TOKEN_KEY)
        chat_id = keyring_svc.retrieve(CHAT_ID_KEY)
        if not token:
            raise ValueError(
                "Telegram bot token이 설정되지 않았습니다. "
                "자격증명 관리에서 'telegram_bot_token'을 저장하세요."
            )
        if not chat_id:
            raise ValueError(
                "Telegram chat ID가 설정되지 않았습니다. "
                "자격증명 관리에서 'telegram_chat_id'를 저장하세요."
            )
        return token, chat_id

    def _is_authorized(self, update: Update, authorized_chat_id: str) -> bool:
        return str(update.effective_chat.id) == authorized_chat_id

    def is_running(self) -> bool:
        return self._running

    async def setup(self, token: str, chat_id: str | None = None) -> dict:
        """
        봇 토큰과 chat_id를 keyring에 저장하고 연결 테스트를 수행한다.

        Parameters
        ----------
        token:
            Telegram Bot API 토큰.
        chat_id:
            허용할 chat_id (선택). None이면 기존 값 유지.

        Returns
        -------
        dict
            {"ok": True, "bot_name": str} 또는 {"ok": False, "error": str}
        """
        import httpx

        # 연결 테스트: getMe API 호출
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"https://api.telegram.org/bot{token}/getMe"
                )
                data = resp.json()
        except Exception as e:
            return {"ok": False, "error": f"Telegram API 연결 실패: {e}"}

        if not data.get("ok"):
            desc = data.get("description", "알 수 없는 오류")
            return {"ok": False, "error": f"잘못된 봇 토큰: {desc}"}

        # 토큰 저장
        keyring_svc.store(BOT_TOKEN_KEY, token)
        if chat_id:
            keyring_svc.store(CHAT_ID_KEY, chat_id)

        bot_info = data.get("result", {})
        bot_username = bot_info.get("username", "")
        # bot_username을 keyring에 영속 저장 — status 엔드포인트에서 딥링크 생성에 사용
        if bot_username:
            keyring_svc.store("telegram_bot_username", f"@{bot_username}")
        audit.log("telegram_setup", f"bot={bot_username or '?'}")

        return {
            "ok": True,
            "bot_name": bot_info.get("first_name", ""),
            "bot_username": f"@{bot_username}" if bot_username else "",
        }

    # ── 도구 실행 ─────────────────────────────────────────────────────────────

    async def _run_tool(self, tool_name: str, params: dict) -> str:
        """tool_name에 해당하는 도구를 실행하고 텍스트 결과를 반환."""
        llm = get_llm_service()

        if tool_name == "chat":
            return await self._tool_chat(params, llm)

        if tool_name == "gmail.fetch_emails":
            return await self._tool_gmail_fetch(params)

        if tool_name == "gmail.summarize_recent":
            return await self._tool_gmail_summarize(llm)

        if tool_name == "document.generate":
            return await self._tool_document_generate(params, llm)

        if tool_name == "status.check":
            return await self._tool_status()

        if tool_name == "help":
            return _HELP_TEXT

        return "알 수 없는 도구입니다. /help 를 입력하세요."

    async def _tool_chat(self, params: dict, llm) -> str:
        """대화 히스토리를 포함해 LLM에 전달."""
        messages = list(self._history)
        reply = await llm.chat(messages)
        # 히스토리에 assistant 응답 추가
        self._history.append({"role": "assistant", "content": reply})
        return reply

    async def _tool_gmail_fetch(self, params: dict) -> str:
        max_results = int(params.get("max_results", 5))
        try:
            emails = self._gmail.fetch_recent_emails(max_results)
        except ValueError as e:
            return f"Gmail 미연결: {e}\n\n앱에서 Gmail을 먼저 연결해주세요."

        classified = self._filter.classify_emails(emails)
        if not classified:
            return "받은 메일이 없습니다."

        lines = []
        for i, e in enumerate(classified, 1):
            imp_icon = {"high": "🔴", "low": "⚪", "normal": "🔵"}.get(
                e.get("importance", "normal"), "🔵"
            )
            lines.append(f"{imp_icon} *{i}. {e['subject']}*\n   From: {e['from']}")
            if e.get("snippet"):
                lines.append(f"   _{e['snippet'][:80]}..._")

        audit.log("telegram_tool", "gmail.fetch_emails", f"count={len(classified)}")
        return "\n\n".join(lines)

    async def _tool_gmail_summarize(self, llm) -> str:
        try:
            emails = self._gmail.fetch_recent_emails(10)
        except ValueError as e:
            return f"Gmail 미연결: {e}\n\n앱에서 Gmail을 먼저 연결해주세요."

        classified = self._filter.classify_emails(emails)
        # 중요도 높은 것 우선, 없으면 첫 번째
        target = next(
            (e for e in classified if e.get("importance") == "high"), classified[0]
        ) if classified else None

        if not target:
            return "요약할 메일이 없습니다."

        try:
            body = self._gmail.get_email_body(target["id"])
        except Exception as e:
            return f"메일 본문 로드 실패: {e}"

        if not body.strip():
            body = target.get("snippet", "(본문 없음)")

        truncated = body[:3000]
        prompt = (
            f"다음 이메일을 한국어로 5줄 이내로 요약하세요.\n\n"
            f"제목: {target['subject']}\n"
            f"발신자: {target['from']}\n\n"
            f"---\n{truncated}\n---"
        )
        summary = await llm.chat([{"role": "user", "content": prompt}])
        audit.log("telegram_tool", "gmail.summarize_recent", target["id"])
        return f"📧 *{target['subject']}*\nFrom: {target['from']}\n\n{summary}"

    async def _tool_document_generate(self, params: dict, llm) -> str:
        doc_type = params.get("doc_type", "보고서")
        content = params.get("content", "")
        tone = params.get("tone", "공식적")
        length = params.get("length", "보통")

        if not content:
            return (
                "문서 내용을 알려주세요. 예시:\n"
                "`보고서 작성해줘: 3분기 매출이 전분기 대비 15% 증가했습니다.`"
            )

        try:
            draft = await document_service.generate_document(
                doc_type=doc_type,
                content=content,
                tone=tone,
                length=length,
                llm_service=llm,
            )
        except ValueError as e:
            return f"문서 생성 실패: {e}"

        audit.log("telegram_tool", "document.generate", doc_type)
        # Telegram 4096자 제한 대비 앞부분 전달
        header = f"📄 *{doc_type} 초안*\n\n"
        body = draft[:3800]
        if len(draft) > 3800:
            body += "\n\n_(앱에서 전체 내용 확인 및 다운로드 가능)_"
        return header + body

    async def _tool_status(self) -> str:
        import httpx

        gmail_ok = self._gmail.is_connected()
        llm = get_llm_service()

        ollama_ok = False
        ollama_models: list[str] = []
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                resp = await client.get("http://localhost:11434/api/tags")
                if resp.status_code == 200:
                    ollama_ok = True
                    ollama_models = [
                        m["name"] for m in resp.json().get("models", [])
                    ]
        except Exception:
            pass

        lines = [
            "⚙️ *시스템 상태*\n",
            f"Gmail: {'✅ 연결됨' if gmail_ok else '❌ 미연결'}",
            f"AI 엔진: {llm.current_provider}",
        ]
        if llm.current_provider == "ollama":
            lines.append(
                f"Ollama: {'✅ 실행 중' if ollama_ok else '❌ 미실행'}"
            )
            if ollama_models:
                lines.append(f"설치된 모델: {', '.join(ollama_models[:5])}")
        lines.append(f"봇: ✅ 실행 중")

        audit.log("telegram_tool", "status.check")
        return "\n".join(lines)

    # ── Phase 2: 명령 분석 + HITL ──────────────────────────────────────────

    async def analyze_and_guard(
        self,
        code: str,
        chat_id: str,
        lang: str = "auto",
    ) -> dict[str, Any]:
        """
        에이전트 생성 코드를 분석하고 등급에 따라 처리한다.

        Returns
        -------
        dict
            {
                "grade": "SAFE"|"CONFIRM"|"DENIED",
                "allowed": bool,       # 실행 허가 여부
                "reason": str,         # 한국어 사유
                "audit_id": int,       # 감사 로그 ID
            }
        """
        analyzer = get_analyzer()
        cmd_audit = get_command_audit_logger()
        result = analyzer.analyze(code, lang=lang)

        if result.grade == "DENIED":
            audit_id = cmd_audit.log(
                grade="DENIED",
                command=code,
                reason=result.reason,
                lang=lang,
                pattern=result.matched_pattern,
                approved=False,
                user_id=chat_id,
                source="telegram",
            )
            return {
                "grade": "DENIED",
                "allowed": False,
                "reason": result.reason,
                "audit_id": audit_id,
            }

        if result.grade == "CONFIRM":
            # 감사 로그에 일단 기록 (approved=None, 대기중)
            audit_id = cmd_audit.log(
                grade="CONFIRM",
                command=code,
                reason=result.reason,
                lang=lang,
                pattern=result.matched_pattern,
                approved=None,
                user_id=chat_id,
                source="telegram",
            )
            # HITL 승인 요청
            approved = await self._request_hitl_approval(
                chat_id=chat_id,
                command=code,
                reason=result.reason,
                audit_id=audit_id,
            )
            # 결과 업데이트
            cmd_audit.update_approval(audit_id, approved)
            return {
                "grade": "CONFIRM",
                "allowed": approved,
                "reason": result.reason if approved else f"사용자가 거부했습니다: {result.reason}",
                "audit_id": audit_id,
            }

        # SAFE
        audit_id = cmd_audit.log(
            grade="SAFE",
            command=code,
            reason=result.reason,
            lang=lang,
            pattern=result.matched_pattern,
            approved=None,
            user_id=chat_id,
            source="telegram",
        )
        return {
            "grade": "SAFE",
            "allowed": True,
            "reason": result.reason,
            "audit_id": audit_id,
        }

    async def _request_hitl_approval(
        self,
        chat_id: str,
        command: str,
        reason: str,
        audit_id: int,
    ) -> bool:
        """
        CONFIRM 등급 명령 발생 시 텔레그램으로 InlineKeyboard 승인 요청을 보내고
        사용자 응답을 최대 60초 대기한다.

        - [승인] → True
        - [거부] 또는 타임아웃 → False
        """
        if not self._app:
            logger.warning("[HITL] 봇이 실행 중이 아닙니다 — 앱 UI 승인 대기로 전환")
            return await _push_ui_approval(command=command, reason=reason, audit_id=audit_id)

        import uuid
        req_id = uuid.uuid4().hex[:8]
        approve_data = f"hitl_approve_{req_id}"
        reject_data = f"hitl_reject_{req_id}"

        event = asyncio.Event()
        self._pending_hitl[req_id] = {
            "event": event,
            "approved": False,
            "audit_id": audit_id,
            "command": command,
        }

        # 명령 미리보기 (200자 제한)
        cmd_preview = command.strip()[:200]
        if len(command.strip()) > 200:
            cmd_preview += "..."

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("승인", callback_data=approve_data),
                InlineKeyboardButton("거부", callback_data=reject_data),
            ]
        ])

        try:
            await self._app.bot.send_message(
                chat_id=chat_id,
                text=(
                    f"⚠️ *보안 확인 요청*\n\n"
                    f"*사유:* {reason}\n\n"
                    f"*명령 미리보기:*\n"
                    f"```\n{cmd_preview}\n```\n\n"
                    f"⏱ {_HITL_TIMEOUT_SECONDS}초 내에 응답해 주세요."
                ),
                parse_mode="Markdown",
                reply_markup=keyboard,
            )
        except Exception as e:
            logger.error("[HITL] 승인 요청 메시지 전송 실패: %s", e)
            self._pending_hitl.pop(req_id, None)
            return False

        # 타임아웃 대기
        try:
            await asyncio.wait_for(event.wait(), timeout=_HITL_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            logger.info("[HITL] req_id=%s 타임아웃 — 자동 거부", req_id)
            # 타임아웃 알림 전송
            try:
                await self._app.bot.send_message(
                    chat_id=chat_id,
                    text=(
                        f"⏰ 승인 시간이 초과되었습니다. 명령이 자동으로 거부되었습니다.\n"
                        f"_사유: {reason}_"
                    ),
                    parse_mode="Markdown",
                )
            except Exception:
                pass
            self._pending_hitl.pop(req_id, None)
            return False

        entry = self._pending_hitl.pop(req_id, {})
        return entry.get("approved", False)

    # ── 봇 시작 / 중지 ───────────────────────────────────────────────────────

    async def start(self) -> dict:
        if self._running:
            return {"status": "already_running"}

        token, chat_id = self._get_config()
        audit.log("telegram_start", "bot")

        self._app = Application.builder().token(token).build()

        # ── 핸들러 등록 ───────────────────────────────────────────────────────

        async def guard(update: Update) -> bool:
            """인가된 사용자인지 확인. 아니면 무시."""
            if not self._is_authorized(update, chat_id):
                audit.log("telegram_unauthorized", str(update.effective_chat.id))
                await update.message.reply_text("접근이 거부되었습니다.")
                return False
            return True

        # /help, /start
        async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
            if not await guard(update):
                return
            await update.message.reply_text(_HELP_TEXT, parse_mode="Markdown")

        # /status
        async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
            if not await guard(update):
                return
            result = await self._tool_status()
            await update.message.reply_text(result, parse_mode="Markdown")

        # /confirm — 대기 중인 CONFIRM 작업 실행
        async def cmd_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
            if not await guard(update):
                return
            if not self._pending_action:
                await update.message.reply_text("대기 중인 작업이 없습니다.")
                return
            action = self._pending_action
            self._pending_action = None
            await update.message.reply_text(f"실행 중: {action['description']}")
            try:
                result = await self._run_tool(action["tool"], action["params"])
                await update.message.reply_text(result[:4096], parse_mode="Markdown")
            except Exception as e:
                await update.message.reply_text(f"실행 실패: {e}")

        # /cancel — 대기 중인 작업 취소
        async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
            if not await guard(update):
                return
            if self._pending_action:
                self._pending_action = None
                await update.message.reply_text("작업이 취소되었습니다.")
            else:
                await update.message.reply_text("취소할 작업이 없습니다.")

        # 텍스트 메시지 — Intent Router를 통한 에이전트 처리
        async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
            if not await guard(update):
                return

            msg = update.message.text or ""
            if not msg.strip():
                return

            # 대화 히스토리에 사용자 메시지 추가
            self._history.append({"role": "user", "content": msg})
            audit.log("telegram_message", f"len={len(msg)}")

            # ── Phase 2: 사용자 메시지에 코드 블록이 포함된 경우 사전 분석 ──────────
            # 코드 블록(``` 또는 인라인 `)이 포함된 메시지는 보안 분석 대상
            has_code_block = bool(re.search(r"```|`[^`]+`", msg))
            if has_code_block:
                # 코드 블록 내용만 추출하여 분석
                code_to_analyze = _extract_code_from_message(msg)
                if code_to_analyze:
                    guard_result = await self.analyze_and_guard(
                        code=code_to_analyze,
                        chat_id=chat_id,
                        lang="auto",
                    )
                    if not guard_result["allowed"] and guard_result["grade"] == "DENIED":
                        await update.message.reply_text(
                            f"⛔ *보안 정책 위반 — 실행 차단됨*\n\n"
                            f"*사유:* {guard_result['reason']}",
                            parse_mode="Markdown",
                        )
                        return
                    if not guard_result["allowed"] and guard_result["grade"] == "CONFIRM":
                        # HITL 승인이 거부됨
                        await update.message.reply_text(
                            f"❌ 작업이 취소되었습니다.\n_{guard_result['reason']}_",
                            parse_mode="Markdown",
                        )
                        return
                    # SAFE 또는 CONFIRM 승인됨 → 계속 진행

            # Intent 분류
            llm = get_llm_service()
            intent = await self._intent_router.classify(msg, llm)
            tool_name = intent["tool"]

            # ── Phase 1: 워크스페이스 파일 명령 우선 처리 ──────────────────────────
            # 자연어 패턴은 messenger/telegram.py에서 단일 관리
            if _PATTERN_LIST.search(msg):
                result = _cmd_ws_list()
                for chunk in _split_message(result, 4096):
                    await update.message.reply_text(chunk)
                return

            if re.search(r"써줘|쓰기|저장|write|save", msg, re.IGNORECASE):
                filename, content = _parse_write_command(msg)
                # Phase 2: 워크스페이스 쓰기도 HITL CONFIRM 흐름 적용
                if filename and content:
                    guard_result = await self.analyze_and_guard(
                        code=f"# 파일 쓰기 요청\nopen('{filename}', 'w').write(content)",
                        chat_id=chat_id,
                        lang="python",
                    )
                    if not guard_result["allowed"]:
                        if guard_result["grade"] == "DENIED":
                            await update.message.reply_text(
                                f"⛔ 파일 쓰기가 차단되었습니다.\n_{guard_result['reason']}_",
                                parse_mode="Markdown",
                            )
                        else:
                            await update.message.reply_text(
                                f"❌ 파일 쓰기가 취소되었습니다.",
                            )
                        return
                result = _cmd_ws_write(filename or "", content or "")
                await update.message.reply_text(result)
                return

            if re.search(r"읽어|읽기|내용|read\b|cat\b", msg, re.IGNORECASE):
                filename = _extract_filename(msg)
                result = _cmd_ws_read(filename or "")
                for chunk in _split_message(result, 4096):
                    await update.message.reply_text(chunk)
                return

            # 샌드박스: DENIED 도구
            if tool_name == "DENIED":
                audit.log("telegram_denied", msg[:100])
                await update.message.reply_text(
                    "⛔ 보안 정책상 실행할 수 없는 요청입니다.\n"
                    "파일 삭제, 셸 실행 등의 작업은 지원하지 않습니다."
                )
                return

            tool_def = get_tool(tool_name)

            # 권한 확인
            if tool_def and tool_def.permission == PermissionLevel.CONFIRM:
                self._pending_action = {
                    "tool": tool_name,
                    "params": intent["params"],
                    "description": intent.get("reason", tool_name),
                }
                await update.message.reply_text(
                    f"⚠️ 이 작업은 확인이 필요합니다:\n"
                    f"_{intent.get('reason', tool_name)}_\n\n"
                    f"/confirm 으로 실행 | /cancel 로 취소",
                    parse_mode="Markdown",
                )
                return

            # SAFE — 바로 실행
            await update.message.reply_text("처리 중...")
            try:
                result = await self._run_tool(tool_name, intent["params"])
                # ── Phase 2: LLM 응답에 코드 블록이 포함된 경우 실행 전 분석 ────────
                code_in_response = _extract_code_from_message(result)
                if code_in_response:
                    guard_result = await self.analyze_and_guard(
                        code=code_in_response,
                        chat_id=chat_id,
                        lang="auto",
                    )
                    if not guard_result["allowed"]:
                        grade_label = "차단됨" if guard_result["grade"] == "DENIED" else "거부됨"
                        await update.message.reply_text(
                            f"⛔ 에이전트 응답 코드가 {grade_label}:\n_{guard_result['reason']}_\n\n"
                            f"코드를 수정하거나 다시 요청해 주세요.",
                            parse_mode="Markdown",
                        )
                        return
                # Telegram 메시지 길이 제한 (4096자)
                for chunk in _split_message(result, 4096):
                    await update.message.reply_text(chunk, parse_mode="Markdown")
            except Exception as e:
                logger.error("Tool execution error (%s): %s", tool_name, e)
                await update.message.reply_text(f"오류 발생: {e}")

        # 파일 업로드 → 요약 (기존 기능 유지)
        async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
            if not await guard(update):
                return

            doc = update.message.document
            if not doc:
                await update.message.reply_text("파일을 인식할 수 없습니다.")
                return

            file_name = doc.file_name or "unknown"
            if (doc.file_size or 0) > 1_000_000:
                await update.message.reply_text("파일이 너무 큽니다. (최대 1MB)")
                return

            await update.message.reply_text(f"'{file_name}' 파일 요약 중...")
            audit.log("telegram_file", file_name)

            try:
                tg_file = await doc.get_file()
                with tempfile.NamedTemporaryFile(
                    suffix=Path(file_name).suffix, delete=False
                ) as tmp:
                    await tg_file.download_to_drive(tmp.name)
                    tmp_path = tmp.name

                content = Path(tmp_path).read_text(encoding="utf-8", errors="replace")
                Path(tmp_path).unlink(missing_ok=True)

                if not content.strip():
                    await update.message.reply_text("파일에 텍스트 내용이 없습니다.")
                    return

                truncated = content[:4000]
                prompt = (
                    f"다음은 '{file_name}' 파일의 내용입니다. "
                    "한국어로 핵심 내용을 5줄 이내로 요약해주세요.\n\n"
                    f"---\n{truncated}\n---"
                )
                llm = get_llm_service()
                summary = await llm.chat([{"role": "user", "content": prompt}])
                await update.message.reply_text(f"📄 *{file_name} 요약:*\n\n{summary}", parse_mode="Markdown")
                audit.log("telegram_file_summary", file_name)
            except Exception as e:
                await update.message.reply_text(f"요약 실패: {e}")
                logger.error("File summary error: %s", e)

        # Phase 2: [승인] / [거부] InlineKeyboard 콜백 처리
        async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
            query = update.callback_query
            if not query:
                return

            # 인가 확인
            if not self._is_authorized(update, chat_id):
                await query.answer("접근이 거부되었습니다.", show_alert=True)
                return

            data = query.data or ""
            if data.startswith("hitl_approve_") or data.startswith("hitl_reject_"):
                prefix = "hitl_approve_" if data.startswith("hitl_approve_") else "hitl_reject_"
                req_id = data[len(prefix):]
                approved = data.startswith("hitl_approve_")

                entry = self._pending_hitl.get(req_id)
                if not entry:
                    await query.answer("이미 처리된 요청입니다.", show_alert=True)
                    return

                entry["approved"] = approved
                entry["event"].set()

                # 버튼 메시지 업데이트
                status_text = "✅ 승인됨" if approved else "❌ 거부됨"
                try:
                    await query.edit_message_reply_markup(reply_markup=None)
                    await query.answer(status_text)
                    await self._app.bot.send_message(
                        chat_id=chat_id,
                        text=(
                            f"{status_text}\n"
                            f"_{entry.get('command', '')[:100]}_"
                            if entry.get("command")
                            else status_text
                        ),
                        parse_mode="Markdown",
                    )
                except Exception as e:
                    logger.warning("[HITL] 콜백 응답 처리 오류: %s", e)
            else:
                await query.answer()

        # ── 핸들러 등록 ────────────────────────────────────────────────────────
        self._app.add_handler(CommandHandler(["start", "help"], cmd_help))
        self._app.add_handler(CommandHandler("status", cmd_status))
        self._app.add_handler(CommandHandler("confirm", cmd_confirm))
        self._app.add_handler(CommandHandler("cancel", cmd_cancel))
        self._app.add_handler(CallbackQueryHandler(handle_callback_query))
        self._app.add_handler(MessageHandler(filters.Document.ALL, handle_file))
        self._app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text)
        )

        self._running = True

        async def _run_bot():
            try:
                await self._app.initialize()
                await self._app.start()
                await self._app.updater.start_polling(drop_pending_updates=True)
                logger.info("Telegram agent started polling")
            except Exception as e:
                logger.error("Telegram bot error: %s", e)
                self._running = False

        asyncio.create_task(_run_bot())
        return {"status": "started"}

    async def stop(self) -> dict:
        if not self._running or not self._app:
            return {"status": "not_running"}

        audit.log("telegram_stop", "bot")
        try:
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()
        except Exception as e:
            logger.error("Error stopping Telegram bot: %s", e)

        self._running = False
        self._app = None
        self._history.clear()
        self._pending_action = None
        return {"status": "stopped"}


# ── 워크스페이스 명령 헬퍼 ────────────────────────────────────────────────────
# 단일 진입점: 모든 워크스페이스 명령 로직은 이 파일에만 존재한다.
# messenger/telegram.py는 패턴 정규식 유틸만 제공.

def _cmd_ws_list(path: str = "") -> str:
    """파일 목록 조회 — sandbox.list_files() 래퍼."""
    try:
        entries = sandbox.list_files(path)
    except PermissionError:
        return "접근 거부: 워크스페이스 외부 경로입니다."
    except FileNotFoundError:
        return f"경로를 찾을 수 없습니다: {path or '/'}"
    except Exception as e:
        return f"파일 목록 조회 실패: {e}"

    if not entries:
        return "워크스페이스가 비어있습니다.\n경로: ~/PrivateClaw/Workspace"

    lines = ["워크스페이스 파일 목록:\n"]
    for entry in entries:
        prefix = "[폴더]" if entry["is_dir"] else "[파일]"
        size_str = ""
        if not entry["is_dir"]:
            kb = entry["size"] / 1024
            size_str = f" ({kb:.1f}KB)" if kb >= 1 else f" ({entry['size']}B)"
        lines.append(f"{prefix} {entry['name']}{size_str}")
    return "\n".join(lines)


def _cmd_ws_read(filename: str) -> str:
    """파일 읽기 — sandbox.read_file() 래퍼."""
    if not filename:
        return "파일명을 지정해주세요. 예: test.txt 내용 읽어줘"
    try:
        content = sandbox.read_file(filename)
    except PermissionError:
        return "접근 거부: 워크스페이스 외부 경로입니다."
    except FileNotFoundError:
        return f"파일을 찾을 수 없습니다: {filename}"
    except IsADirectoryError:
        return f"{filename}은 파일이 아닌 디렉토리입니다."
    except Exception as e:
        return f"파일 읽기 실패: {e}"

    if not content.strip():
        return f"{filename}: (빈 파일)"

    truncated = content[:4000]
    suffix = "\n\n...(파일이 너무 깁니다. 앞부분만 표시)" if len(content) > 4000 else ""
    return f"{filename} 내용:\n\n{truncated}{suffix}"


def _cmd_ws_write(filename: str, content: str) -> str:
    """파일 쓰기 — sandbox.write_file() 래퍼."""
    if not filename:
        return "파일명을 지정해주세요. 예: notes.txt 써줘: 내용"
    if not content:
        return "저장할 내용을 입력해주세요. 예: notes.txt 써줘: 내용"
    try:
        sandbox.write_file(filename, content)
    except PermissionError:
        return "접근 거부: 워크스페이스 외부 경로입니다."
    except Exception as e:
        return f"파일 저장 실패: {e}"
    return f"{filename} 저장 완료."


# ── 유틸 ─────────────────────────────────────────────────────────────────────

def _extract_code_from_message(text: str) -> str | None:
    """
    메시지에서 코드 블록 내용을 추출한다.

    - 트리플 백틱(``` ... ```) 블록 우선 추출
    - 인라인 백틱(` ... `) 블록은 단일 라인 이상일 때만 추출
    - 추출된 코드가 없으면 None 반환

    Parameters
    ----------
    text:
        검사할 텍스트 (메신저 메시지 또는 LLM 응답).

    Returns
    -------
    str | None
        추출된 코드 (여러 블록이면 줄바꿈으로 결합), 없으면 None.
    """
    # 트리플 백틱 블록 — ```lang\ncode\n``` 형태
    triple_pattern = re.compile(r"```(?:\w+)?\n?(.*?)```", re.DOTALL)
    triple_matches = triple_pattern.findall(text)
    if triple_matches:
        return "\n\n".join(m.strip() for m in triple_matches if m.strip())

    # 인라인 백틱 — `code` 형태 (공백 포함 10자 이상만 의미 있는 코드로 간주)
    inline_pattern = re.compile(r"`([^`]{10,})`")
    inline_matches = inline_pattern.findall(text)
    if inline_matches:
        return "\n".join(m.strip() for m in inline_matches if m.strip())

    return None


def _split_message(text: str, limit: int) -> list[str]:
    """긴 텍스트를 Telegram 메시지 길이 제한에 맞게 분할."""
    if len(text) <= limit:
        return [text]
    chunks = []
    while text:
        chunks.append(text[:limit])
        text = text[limit:]
    return chunks
