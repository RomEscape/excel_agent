"""
messenger/slack.py — Phase 3 (Private-Claw) Slack 어댑터.

Slack Bolt 기반 어댑터.
공통 MessengerAdapter(base.py) 인터페이스를 구현한다.
보안 가드레일(analyze_and_guard)은 TelegramService와 동일한 패턴을 따른다.

의존성: slack-bolt>=1.18.0
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from pathlib import Path
from typing import Any

from office_claw_sidecar.messenger.base import MessengerAdapter

logger = logging.getLogger(__name__)

# HITL 승인 타임아웃 (초)
_HITL_TIMEOUT_SECONDS = 60


class SlackAdapter(MessengerAdapter):
    """
    Slack Bolt 기반 메신저 어댑터.

    slack-bolt 패키지의 AsyncApp을 사용하여 소켓 모드(Socket Mode)로 동작한다.
    모든 메시지는 analyze_and_guard()를 통해 보안 검증 후 처리된다.

    사용 전 환경 준비:
      - Slack App을 생성하고 Bot Token(xoxb-...) 발급
      - Socket Mode용 App Token(xapp-...) 발급
      - 필요 스코프: chat:write, files:write, app_mentions:read, im:read, im:write
    """

    def __init__(
        self,
        bot_token: str,
        app_token: str,
        allowed_user_ids: list[str] | None = None,
    ) -> None:
        """
        Parameters
        ----------
        bot_token:
            Slack Bot OAuth Token (xoxb-...).
        app_token:
            Slack App-Level Token for Socket Mode (xapp-...).
        allowed_user_ids:
            허용할 Slack 사용자 ID 목록 (Zero-Trust). None이면 모든 사용자 허용.
            Telegram의 allowed_chat_id와 동일한 보안 원칙 적용.
        """
        self._bot_token = bot_token
        self._app_token = app_token
        self._app: Any | None = None
        self._handler: Any | None = None
        self._running = False
        self._allowed_user_ids: frozenset[str] = (
            frozenset(allowed_user_ids) if allowed_user_ids else frozenset()
        )

        # HITL 승인 대기 맵: req_id → {"event": asyncio.Event, "approved": bool}
        self._pending_hitl: dict[str, dict[str, Any]] = {}

    # ── 시작 / 중지 ──────────────────────────────────────────────────────────────

    async def start(self) -> dict:
        """소켓 모드로 Slack 봇을 시작한다."""
        if self._running:
            return {"status": "already_running"}

        try:
            from slack_bolt.async_app import AsyncApp
            from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
        except ImportError:
            logger.error("[Slack] slack-bolt 패키지가 설치되어 있지 않습니다. pip install slack-bolt")
            return {"status": "error", "error": "slack-bolt 패키지 미설치"}

        self._app = AsyncApp(token=self._bot_token)
        self._register_handlers()
        self._handler = AsyncSocketModeHandler(self._app, self._app_token)

        self._running = True

        async def _run():
            try:
                await self._handler.start_async()
            except Exception as e:
                logger.error("[Slack] 봇 실행 오류: %s", e)
                self._running = False

        asyncio.create_task(_run())
        logger.info("[Slack] 봇 시작됨 (Socket Mode)")
        return {"status": "started"}

    async def stop(self) -> dict:
        """Slack 봇을 중지한다."""
        if not self._running or not self._handler:
            return {"status": "not_running"}

        try:
            await self._handler.close_async()
        except Exception as e:
            logger.error("[Slack] 봇 중지 오류: %s", e)

        self._running = False
        self._app = None
        self._handler = None
        logger.info("[Slack] 봇 중지됨")
        return {"status": "stopped"}

    # ── 메시지 전송 ───────────────────────────────────────────────────────────────

    async def send_message(self, channel_id: str, text: str) -> None:
        """텍스트 메시지를 Slack 채널/DM에 전송한다."""
        if not self._app:
            logger.warning("[Slack] 봇 미실행 상태에서 send_message 호출됨")
            return
        try:
            await self._app.client.chat_postMessage(channel=channel_id, text=text)
        except Exception as e:
            logger.error("[Slack] 메시지 전송 실패: %s", e)

    async def send_file(self, channel_id: str, file_path: str) -> None:
        """파일을 Slack 채널/DM에 전송한다."""
        if not self._app:
            logger.warning("[Slack] 봇 미실행 상태에서 send_file 호출됨")
            return
        path = Path(file_path)
        if not path.exists():
            logger.error("[Slack] 파일 없음: %s", file_path)
            return
        try:
            await self._app.client.files_upload_v2(
                channel=channel_id,
                file=str(path),
                filename=path.name,
            )
        except Exception as e:
            logger.error("[Slack] 파일 전송 실패: %s", e)

    # ── HITL 승인 요청 ────────────────────────────────────────────────────────────

    async def request_approval(
        self,
        channel_id: str,
        command: str,
        reason: str,
    ) -> bool:
        """
        Slack Block Kit 버튼으로 [승인] / [거부] 인터랙티브 메시지를 전송하고
        사용자 응답을 최대 60초 대기한다.

        Returns
        -------
        bool
            True → 승인, False → 거부 또는 타임아웃.
        """
        if not self._app:
            logger.warning("[Slack] 봇 미실행 상태 — 앱 UI 승인 대기로 전환")
            from office_claw_sidecar.services.telegram_service import _push_ui_approval
            return await _push_ui_approval(command=command, reason=reason, audit_id=-1)

        req_id = uuid.uuid4().hex[:8]
        event = asyncio.Event()
        self._pending_hitl[req_id] = {"event": event, "approved": False}

        cmd_preview = command.strip()[:300]
        if len(command.strip()) > 300:
            cmd_preview += "..."

        # Slack Block Kit interactive message
        blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f":warning: *보안 확인 요청*\n\n*사유:* {reason}",
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*명령 미리보기:*\n```{cmd_preview}```",
                },
            },
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f":timer_clock: {_HITL_TIMEOUT_SECONDS}초 내에 응답해 주세요.",
                    }
                ],
            },
            {
                "type": "actions",
                "block_id": f"hitl_{req_id}",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "승인"},
                        "style": "primary",
                        "action_id": f"hitl_approve_{req_id}",
                        "value": req_id,
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "거부"},
                        "style": "danger",
                        "action_id": f"hitl_reject_{req_id}",
                        "value": req_id,
                    },
                ],
            },
        ]

        try:
            await self._app.client.chat_postMessage(
                channel=channel_id,
                text="보안 확인 요청",
                blocks=blocks,
            )
        except Exception as e:
            logger.error("[Slack] HITL 메시지 전송 실패: %s", e)
            self._pending_hitl.pop(req_id, None)
            return False

        # 타임아웃 대기
        try:
            await asyncio.wait_for(event.wait(), timeout=_HITL_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            logger.info("[Slack] HITL req_id=%s 타임아웃 — 자동 거부", req_id)
            try:
                await self._app.client.chat_postMessage(
                    channel=channel_id,
                    text=f":hourglass: 승인 시간이 초과되었습니다. 명령이 자동으로 거부되었습니다.\n_{reason}_",
                )
            except Exception:
                pass
            self._pending_hitl.pop(req_id, None)
            return False

        entry = self._pending_hitl.pop(req_id, {})
        return entry.get("approved", False)

    # ── 핸들러 등록 ───────────────────────────────────────────────────────────────

    def _register_handlers(self) -> None:
        """Slack 이벤트 및 액션 핸들러를 등록한다."""
        if not self._app:
            return

        @self._app.event("message")
        async def handle_message(event: dict, say) -> None:
            """DM 및 채널 메시지 처리."""
            text = event.get("text", "")
            channel = event.get("channel", "")
            user = event.get("user", "")

            if not text or not channel or event.get("bot_id"):
                return

            # Zero-Trust: allowed_user_ids 화이트리스트 검증 (TelegramService와 동일 원칙)
            if self._allowed_user_ids and user not in self._allowed_user_ids:
                logger.warning("[Slack] 허용되지 않은 사용자: user=%s — 메시지 무시", user)
                return

            logger.info("[Slack] 메시지 수신: user=%s channel=%s", user, channel)

            # 공통 파이프라인: 코드 블록 분석 → 워크스페이스 명령 → 에이전트
            response = await self.process_message(
                channel_id=channel,
                user_id=user,
                text=text,
            )
            await say(channel=channel, text=response)

        @self._app.action(re.compile(r"hitl_(approve|reject)_.*"))
        async def handle_hitl_action(ack, action: dict, body: dict) -> None:
            """HITL 승인/거부 버튼 클릭 처리."""
            await ack()

            action_id: str = action.get("action_id", "")
            req_id = action.get("value", "")
            approved = action_id.startswith("hitl_approve_")

            entry = self._pending_hitl.get(req_id)
            if not entry:
                return

            entry["approved"] = approved
            entry["event"].set()

            status_text = ":white_check_mark: 승인됨" if approved else ":x: 거부됨"
            channel = body.get("container", {}).get("channel_id", "")
            if channel and self._app:
                try:
                    await self._app.client.chat_postMessage(
                        channel=channel, text=status_text
                    )
                except Exception:
                    pass


# 공통 유틸은 messenger/base.py에서 제공 (has_code_block, extract_code_blocks)
# 이 파일에서는 중복 정의하지 않는다.
