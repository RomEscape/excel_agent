"""
messenger/discord_adapter.py — Phase 3 (Private-Claw) Discord 어댑터.

discord.py 기반 어댑터. (파일명: discord_adapter.py — discord 패키지명 충돌 방지)
TelegramAdapter, SlackAdapter와 동일한 MessengerAdapter 인터페이스를 구현한다.
보안 가드레일(analyze_and_guard) 패턴은 TelegramService를 그대로 따른다.

의존성: discord.py>=2.3.0
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path
from typing import Any

from office_claw_sidecar.messenger.base import MessengerAdapter

logger = logging.getLogger(__name__)

# HITL 승인 타임아웃 (초)
_HITL_TIMEOUT_SECONDS = 60


class DiscordAdapter(MessengerAdapter):
    """
    discord.py 기반 메신저 어댑터.

    Discord Bot Token을 사용하여 게이트웨이에 연결한다.
    allowed_guild_id를 지정하면 해당 서버에서 온 명령만 처리한다 (보안 강화).
    승인 요청은 discord.ui.View + Button을 사용한다.

    사용 전 환경 준비:
      - Discord Developer Portal에서 봇 생성 후 토큰 발급
      - Bot Permissions: Send Messages, Read Message History, Add Reactions
      - Server Members Intent, Message Content Intent 활성화
    """

    def __init__(
        self,
        token: str,
        allowed_guild_id: str | None = None,
        allowed_user_ids: list[str] | None = None,
    ) -> None:
        """
        Parameters
        ----------
        token:
            Discord Bot Token.
        allowed_guild_id:
            허용할 서버(Guild) ID (선택). None이면 모든 서버에서 동작.
        allowed_user_ids:
            허용할 Discord 사용자 ID 목록 (Zero-Trust). None이면 모든 사용자 허용.
            TelegramService의 allowed_chat_id와 동일한 보안 원칙 적용.
        """
        self._token = token
        self._allowed_guild_id = allowed_guild_id
        self._allowed_user_ids: frozenset[str] = (
            frozenset(allowed_user_ids) if allowed_user_ids else frozenset()
        )
        self._client: Any | None = None
        self._running = False

        # HITL 승인 대기 맵: req_id → {"event": asyncio.Event, "approved": bool}
        self._pending_hitl: dict[str, dict[str, Any]] = {}

    # ── 시작 / 중지 ──────────────────────────────────────────────────────────────

    async def start(self) -> dict:
        """Discord 봇을 시작한다."""
        if self._running:
            return {"status": "already_running"}

        try:
            import discord
        except ImportError:
            logger.error("[Discord] discord.py 패키지가 설치되어 있지 않습니다. pip install discord.py")
            return {"status": "error", "error": "discord.py 패키지 미설치"}

        intents = discord.Intents.default()
        intents.message_content = True

        self._client = _PrivateClawDiscordClient(
            adapter=self,
            allowed_guild_id=self._allowed_guild_id,
            allowed_user_ids=self._allowed_user_ids,
            intents=intents,
        )
        self._running = True

        async def _run():
            try:
                await self._client.start(self._token)
            except Exception as e:
                logger.error("[Discord] 봇 실행 오류: %s", e)
                self._running = False

        asyncio.create_task(_run())
        logger.info("[Discord] 봇 시작됨")
        return {"status": "started"}

    async def stop(self) -> dict:
        """Discord 봇을 중지한다."""
        if not self._running or not self._client:
            return {"status": "not_running"}

        try:
            await self._client.close()
        except Exception as e:
            logger.error("[Discord] 봇 중지 오류: %s", e)

        self._running = False
        self._client = None
        logger.info("[Discord] 봇 중지됨")
        return {"status": "stopped"}

    # ── 메시지 전송 ───────────────────────────────────────────────────────────────

    async def send_message(self, channel_id: str, text: str) -> None:
        """텍스트 메시지를 Discord 채널에 전송한다."""
        if not self._client:
            logger.warning("[Discord] 봇 미실행 상태에서 send_message 호출됨")
            return
        try:
            channel = self._client.get_channel(int(channel_id))
            if not channel:
                channel = await self._client.fetch_channel(int(channel_id))
            await channel.send(text)
        except Exception as e:
            logger.error("[Discord] 메시지 전송 실패: %s", e)

    async def send_file(self, channel_id: str, file_path: str) -> None:
        """파일을 Discord 채널에 전송한다."""
        if not self._client:
            logger.warning("[Discord] 봇 미실행 상태에서 send_file 호출됨")
            return
        path = Path(file_path)
        if not path.exists():
            logger.error("[Discord] 파일 없음: %s", file_path)
            return
        try:
            import discord
            channel = self._client.get_channel(int(channel_id))
            if not channel:
                channel = await self._client.fetch_channel(int(channel_id))
            await channel.send(file=discord.File(str(path)))
        except Exception as e:
            logger.error("[Discord] 파일 전송 실패: %s", e)

    # ── HITL 승인 요청 ────────────────────────────────────────────────────────────

    async def request_approval(
        self,
        channel_id: str,
        command: str,
        reason: str,
    ) -> bool:
        """
        Discord UI View + Button으로 [승인] / [거부] 버튼을 전송하고
        사용자 응답을 최대 60초 대기한다.

        Returns
        -------
        bool
            True → 승인, False → 거부 또는 타임아웃.
        """
        if not self._client:
            logger.warning("[Discord] 봇 미실행 상태 — 앱 UI 승인 대기로 전환")
            from office_claw_sidecar.services.telegram_service import _push_ui_approval
            return await _push_ui_approval(command=command, reason=reason, audit_id=-1)

        req_id = uuid.uuid4().hex[:8]
        event = asyncio.Event()
        self._pending_hitl[req_id] = {"event": event, "approved": False}

        cmd_preview = command.strip()[:300]
        if len(command.strip()) > 300:
            cmd_preview += "..."

        embed_text = (
            f"**보안 확인 요청**\n\n"
            f"**사유:** {reason}\n\n"
            f"**명령 미리보기:**\n```\n{cmd_preview}\n```\n\n"
            f":timer: {_HITL_TIMEOUT_SECONDS}초 내에 응답해 주세요."
        )

        try:

            channel = self._client.get_channel(int(channel_id))
            if not channel:
                channel = await self._client.fetch_channel(int(channel_id))

            view = _build_approval_view(req_id=req_id, adapter=self)
            await channel.send(content=embed_text, view=view)
        except Exception as e:
            logger.error("[Discord] HITL 메시지 전송 실패: %s", e)
            self._pending_hitl.pop(req_id, None)
            return False

        # 타임아웃 대기
        try:
            await asyncio.wait_for(event.wait(), timeout=_HITL_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            logger.info("[Discord] HITL req_id=%s 타임아웃 — 자동 거부", req_id)
            try:
                channel = self._client.get_channel(int(channel_id))
                if channel:
                    await channel.send(
                        f":hourglass: 승인 시간이 초과되었습니다. 명령이 자동으로 거부되었습니다.\n_{reason}_"
                    )
            except Exception:
                pass
            self._pending_hitl.pop(req_id, None)
            return False

        entry = self._pending_hitl.pop(req_id, {})
        return entry.get("approved", False)


# ── Discord 클라이언트 내부 구현 ──────────────────────────────────────────────────

class _PrivateClawDiscordClient:
    """
    discord.py Client 래퍼.

    on_message 이벤트에서 보안 분석(analyze_and_guard 패턴)을 실행한다.
    """

    def __init__(
        self,
        adapter: "DiscordAdapter",
        allowed_guild_id: str | None,
        allowed_user_ids: "frozenset[str]",
        intents: Any,
    ) -> None:
        import discord

        self._adapter = adapter
        self._allowed_guild_id = allowed_guild_id
        self._allowed_user_ids = allowed_user_ids

        class _Bot(discord.Client):
            async def on_ready(inner_self) -> None:
                logger.info("[Discord] 봇 로그인: %s", inner_self.user)

            async def on_message(inner_self, message: Any) -> None:
                if message.author == inner_self.user:
                    return
                # DM은 guild가 None — guild 필터를 건너뛰고 처리
                if allowed_guild_id and message.guild is not None:
                    if str(message.guild.id) != allowed_guild_id:
                        return
                # Zero-Trust: allowed_user_ids 화이트리스트 검증
                if allowed_user_ids and str(message.author.id) not in allowed_user_ids:
                    logger.warning("[Discord] 허용되지 않은 사용자: user_id=%s — 메시지 무시", message.author.id)
                    return
                await adapter._on_message(message)

        self._bot = _Bot(intents=intents)

    async def start(self, token: str) -> None:
        await self._bot.start(token)

    async def close(self) -> None:
        await self._bot.close()

    def get_channel(self, channel_id: int) -> Any:
        return self._bot.get_channel(channel_id)

    async def fetch_channel(self, channel_id: int) -> Any:
        return await self._bot.fetch_channel(channel_id)


# ── on_message 처리 ───────────────────────────────────────────────────────────

async def _on_message_handler(adapter: "DiscordAdapter", message: Any) -> None:
    """Discord 메시지 처리 — 보안 분석 + 응답."""
    text = message.content or ""
    channel_id = str(message.channel.id)
    user_id = str(message.author.id)

    if not text.strip():
        return

    # 공통 파이프라인: 코드 블록 분석 → 워크스페이스 명령 → 에이전트
    response = await adapter.process_message(
        channel_id=channel_id,
        user_id=user_id,
        text=text,
    )
    await message.channel.send(response)


# Adapter에 _on_message 메서드 주입
DiscordAdapter._on_message = _on_message_handler  # type: ignore[attr-defined]


# ── discord.ui.View + Button ─────────────────────────────────────────────────

def _build_approval_view(req_id: str, adapter: "DiscordAdapter") -> Any:
    """
    discord.ui.View 인스턴스를 생성하여 반환한다.

    discord.py가 설치되어 있을 때만 호출된다.
    """
    import discord

    class ApprovalView(discord.ui.View):
        def __init__(self) -> None:
            super().__init__(timeout=float(_HITL_TIMEOUT_SECONDS))

        @discord.ui.button(label="승인", style=discord.ButtonStyle.success)
        async def approve(
            self, interaction: discord.Interaction, button: discord.ui.Button
        ) -> None:
            entry = adapter._pending_hitl.get(req_id)
            if entry and not entry["event"].is_set():
                entry["approved"] = True
                entry["event"].set()
                for item in self.children:
                    item.disabled = True  # type: ignore[attr-defined]
                await interaction.response.edit_message(view=self)
                await interaction.followup.send(":white_check_mark: 승인되었습니다.")

        @discord.ui.button(label="거부", style=discord.ButtonStyle.danger)
        async def reject(
            self, interaction: discord.Interaction, button: discord.ui.Button
        ) -> None:
            entry = adapter._pending_hitl.get(req_id)
            if entry and not entry["event"].is_set():
                entry["approved"] = False
                entry["event"].set()
                for item in self.children:
                    item.disabled = True  # type: ignore[attr-defined]
                await interaction.response.edit_message(view=self)
                await interaction.followup.send(":x: 거부되었습니다.")

    return ApprovalView()


# 공통 유틸은 messenger/base.py에서 제공 (has_code_block, extract_code_blocks)
# 이 파일에서는 중복 정의하지 않는다.
