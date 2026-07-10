"""중계 서버(relay) 연동 — 데스크톱 사이드카의 아웃바운드 WS 클라이언트.

역할: relay로 아웃바운드 WebSocket을 열고(데스크톱·모바일 둘 다 NAT 뒤), 모바일이 보낸
프레임을 받아 tool-calling 에이전트 루프(excel_tool_agent)에 넣고, 결과를 TokenDelta
스트림으로 되돌린다. CONFIRM 권한 도구는 ApprovalRequest/Response 왕복으로 처리한다.

설계:
- RelaySession : 프레임 디스패치 '순수' 로직 (소켓 무관, 단위 테스트 가능). 세션 상태 소유.
- RelayClient  : 전송 계층 — connect + 재연결(backoff). RelaySession에 raw를 넘긴다.
와이어 계약은 oc_protocol / oc_shared(SSOT)를 그대로 쓴다.

MVP 한계(후속 강화):
- 진짜 토큰 스트리밍 아님 — excel_tool_agent가 완성 문자열을 내므로 assistant_text를
  TokenDelta 1개 + StreamEnd로 감싼다(진짜 스트리밍은 ollama_service SSE 신설 필요).
- E2E 암호화·페어링 SAS·재연결 resume(ack 이후 재전송)은 미구현. ReplayBuffer는 준비만.
- 세션 히스토리는 연결 수명 동안만 유지(재연결 시 초기화).
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

import websockets
from oc_protocol import (
    Ack,
    AgentState,
    AgentStatus,
    ApprovalRequest,
    ApprovalResponse,
    ChatUserMsg,
    Direction,
    Envelope,
    ErrorFrame,
    Frame,
    Ping,
    Pong,
    StreamEnd,
    TokenDelta,
)
from oc_shared import ReplayBuffer, SeqCounter, decode_envelope, encode_envelope

from office_claw_sidecar.config import get_relay_config_path
from office_claw_sidecar.services.excel_tool_agent import (
    resume_excel_tool_turn,
    run_excel_tool_turn,
)
from office_claw_sidecar.services.llm_service import get_llm_service

logger = logging.getLogger(__name__)

DEFAULT_RELAY_URL = "http://127.0.0.1:8787"
_RECONNECT_BASE = 1.0
_RECONNECT_MAX = 30.0


# ── config (비밀 아님 — 비밀은 keyring) ─────────────────────────────────────


def load_relay_config() -> dict[str, Any]:
    path = get_relay_config_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("relay_config 로드 실패 (무시): %s", exc)
        return {}


def save_relay_config(cfg: dict[str, Any]) -> None:
    get_relay_config_path().write_text(
        json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8"
    )


# ── 승인 대기 상태 ──────────────────────────────────────────────────────────


@dataclass
class PendingApproval:
    request_id: str
    resume: dict[str, Any]
    action: str
    params: dict[str, Any]
    sheet_name: str | None
    stream_id: str


# ── 프레임 디스패치 (소켓 무관, 테스트 가능) ────────────────────────────────


class RelaySession:
    """한 pairing_id에 대한 프레임 왕복 로직.

    send_raw로 (인코드된) 프레임을 내보내고, handle_incoming으로 들어온 raw를 처리한다.
    소켓/재연결은 RelayClient가 담당하고, 이 클래스는 순수 로직만 갖는다.
    """

    def __init__(
        self,
        pairing_id: str,
        send_raw: Callable[[str], Awaitable[None]],
        *,
        llm_service: Any = None,
        workbook_id: str | None = None,
        sheet_name: str | None = None,
    ) -> None:
        self.pairing_id = pairing_id
        self._send_raw = send_raw
        self._llm = llm_service
        self.workbook_id = workbook_id
        self.sheet_name = sheet_name
        self._seq = SeqCounter()
        self._replay = ReplayBuffer()
        self._history: list[dict[str, str]] = []
        self._pending: dict[str, PendingApproval] = {}

    @property
    def _llm_service(self) -> Any:
        if self._llm is None:
            self._llm = get_llm_service()
        return self._llm

    async def _send(self, frame: Frame) -> None:
        env = Envelope(
            pairing_id=self.pairing_id,
            direction=Direction.to_mobile,
            seq=self._seq.next(),
            payload=frame,
        )
        raw = encode_envelope(env)
        self._replay.add(env.seq, raw)  # 재연결 resume 대비 (송신분 보관)
        await self._send_raw(raw)

    async def _status(self, state: AgentState) -> None:
        await self._send(AgentStatus(state=state))

    async def handle_incoming(self, raw: str) -> None:
        """relay에서 온 raw 텍스트 1건 처리 (presence control 또는 Envelope 프레임)."""
        try:
            obj = json.loads(raw)
        except ValueError:
            return
        if isinstance(obj, dict) and "control" in obj:
            logger.info("[relay] presence: %s", obj.get("state"))
            return
        try:
            env = decode_envelope(raw)
        except Exception as exc:  # noqa: BLE001 - 잘못된 프레임은 무시
            logger.debug("[relay] 프레임 디코드 실패 (무시): %s", exc)
            return
        await self.on_frame(env.payload)

    async def on_frame(self, frame: Frame) -> None:
        if isinstance(frame, ChatUserMsg):
            await self._on_chat(frame)
        elif isinstance(frame, ApprovalResponse):
            await self._on_approval_response(frame)
        elif isinstance(frame, Ping):
            await self._send(Pong(nonce=frame.nonce))
        elif isinstance(frame, Ack):
            self._replay.prune(frame.ack_seq)
        else:
            logger.debug("[relay] 처리 대상 프레임 아님(무시): %s", type(frame).__name__)

    async def _on_chat(self, msg: ChatUserMsg) -> None:
        await self._status(AgentState.thinking)
        history_snapshot = list(self._history)  # 현재 발화 제외한 이전 턴만 전달
        self._history.append({"role": "user", "content": msg.text})
        try:
            turn = await run_excel_tool_turn(
                message=msg.text,
                llm_service=self._llm_service,
                workbook_id=self.workbook_id,
                sheet_name=self.sheet_name,
                history=history_snapshot,
            )
        except Exception as exc:  # noqa: BLE001 - 에이전트 실패는 스트림 오류로 통지
            logger.exception("[relay] 에이전트 턴 실패")
            await self._fail_stream(msg.client_msg_id, str(exc))
            return
        await self._handle_turn(turn, stream_id=msg.client_msg_id)

    async def _on_approval_response(self, resp: ApprovalResponse) -> None:
        pending = self._pending.pop(resp.request_id, None)
        if pending is None:
            await self._send(
                ErrorFrame(code="unknown_approval", message="알 수 없는 승인 ID")
            )
            return
        if not resp.approved:
            await self._send(StreamEnd(stream_id=pending.stream_id, reason="aborted"))
            await self._status(AgentState.idle)
            return
        await self._status(AgentState.remote_controlling)
        try:
            turn = await resume_excel_tool_turn(
                resume=pending.resume,
                action=pending.action,
                params=pending.params,
                workbook_id=self.workbook_id,
                sheet_name=pending.sheet_name,
                llm_service=self._llm_service,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("[relay] 승인 재개 실패")
            await self._fail_stream(pending.stream_id, str(exc))
            return
        await self._handle_turn(turn, stream_id=pending.stream_id)

    async def _handle_turn(self, turn: dict[str, Any], *, stream_id: str) -> None:
        if turn.get("type") == "approval":
            request_id = uuid.uuid4().hex
            self._pending[request_id] = PendingApproval(
                request_id=request_id,
                resume=turn["resume"],
                action=turn["action"],
                params=turn["params"],
                sheet_name=turn.get("sheet_name"),
                stream_id=stream_id,
            )
            await self._send(
                ApprovalRequest(
                    request_id=request_id,
                    command=turn["action"],
                    reason=turn.get("reason") or "",
                )
            )
            # LLM 추론은 끝, 사용자 승인 대기 → idle
            await self._status(AgentState.idle)
            return

        # type == "chat": 완성 텍스트를 (MVP) TokenDelta 1개 + StreamEnd로 감싼다
        text = str(turn.get("assistant_text") or "")
        self._history.append({"role": "assistant", "content": text})
        await self._send(TokenDelta(stream_id=stream_id, index=0, text=text))
        await self._send(StreamEnd(stream_id=stream_id, reason="complete"))
        await self._status(AgentState.idle)

    async def _fail_stream(self, stream_id: str, error: str) -> None:
        await self._send(StreamEnd(stream_id=stream_id, reason="error", error=error))
        await self._status(AgentState.idle)


# ── 전송 계층 (아웃바운드 WS + 재연결) ──────────────────────────────────────


class RelayClient:
    """relay로 아웃바운드 WS를 열고 재연결을 관리한다.

    데스크톱은 인바운드를 받지 못하므로 부팅 시 relay로 dial-out한다. relay가 4401(미바인딩)
    로 닫으면(모바일이 아직 페어링 완료 전) backoff로 재시도하다가, 바인딩되면 연결된다.
    """

    def __init__(
        self,
        relay_url: str,
        pairing_id: str,
        *,
        workbook_id: str | None = None,
        sheet_name: str | None = None,
    ) -> None:
        self.relay_url = relay_url.rstrip("/")
        self.pairing_id = pairing_id
        self.workbook_id = workbook_id
        self.sheet_name = sheet_name
        self._stop = asyncio.Event()
        self.connected = False

    def _ws_url(self) -> str:
        base = self.relay_url
        if base.startswith("https://"):
            base = "wss://" + base[len("https://") :]
        elif base.startswith("http://"):
            base = "ws://" + base[len("http://") :]
        elif not base.startswith(("ws://", "wss://")):
            base = "ws://" + base
        return f"{base}/ws/desktop?pairing_id={self.pairing_id}"

    async def run(self) -> None:
        """중단(stop)될 때까지 연결을 유지하며 재연결(지수 backoff + jitter)한다."""
        attempt = 0
        while not self._stop.is_set():
            try:
                await self._connect_once()
                attempt = 0  # 정상 세션 후 backoff 리셋
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 모든 연결오류는 재연결로 흡수
                logger.info("[relay] 연결 종료/실패, 재연결 예정: %s", exc)
            if self._stop.is_set():
                break
            attempt += 1
            delay = min(_RECONNECT_MAX, _RECONNECT_BASE * (2 ** min(attempt, 6)))
            delay += random.uniform(0, delay * 0.25)  # jitter — thundering herd 방지
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass

    async def _connect_once(self) -> None:
        logger.info("[relay] 연결 시도: %s/ws/desktop", self.relay_url)
        async with websockets.connect(
            self._ws_url(), ping_interval=20, ping_timeout=20
        ) as ws:
            self.connected = True
            logger.info("[relay] 연결됨")
            session = RelaySession(
                self.pairing_id,
                ws.send,
                workbook_id=self.workbook_id,
                sheet_name=self.sheet_name,
            )
            try:
                async for raw in ws:
                    if isinstance(raw, bytes):
                        raw = raw.decode("utf-8", "ignore")
                    await session.handle_incoming(raw)
            finally:
                self.connected = False

    async def stop(self) -> None:
        self._stop.set()


# ── 라이프사이클 헬퍼 (main.py lifespan에서 사용) ───────────────────────────


async def start_relay_client(app: Any) -> bool:
    """config가 enabled+pairing_id면 RelayClient를 백그라운드 태스크로 기동한다.

    설정이 없거나 이미 실행 중이면 no-op. 기동하면 app.state에 client/task를 저장한다.
    반환: 새로 기동했는지 여부.
    """
    cfg = load_relay_config()
    if not cfg.get("enabled") or not cfg.get("pairing_id"):
        return False
    existing = getattr(app.state, "relay_task", None)
    if existing is not None and not existing.done():
        return False  # 이미 실행 중
    client = RelayClient(
        cfg.get("relay_url", DEFAULT_RELAY_URL),
        cfg["pairing_id"],
        workbook_id=cfg.get("workbook_id"),
        sheet_name=cfg.get("sheet_name"),
    )
    app.state.relay_client = client
    app.state.relay_task = asyncio.create_task(client.run())
    logger.info("[relay] 클라이언트 백그라운드 기동")
    return True


async def stop_relay_client(app: Any) -> None:
    """실행 중인 RelayClient를 정리한다(graceful — main.py shutdown / 재페어링)."""
    client = getattr(app.state, "relay_client", None)
    task = getattr(app.state, "relay_task", None)
    if client is not None:
        await client.stop()
    if task is not None:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
    app.state.relay_client = None
    app.state.relay_task = None
