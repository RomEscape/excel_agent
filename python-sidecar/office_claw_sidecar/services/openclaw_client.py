"""
OpenClaw 게이트웨이 WebSocket 클라이언트.

OpenClaw 프로토콜 흐름:
  1. ws://127.0.0.1:18789 에 연결
  2. {"type":"connect","role":"operator","scopes":["read","write"]} 전송
  3. 서버로부터 {"type":"connected","deviceToken":"..."} 수신 → 토큰 캐싱
  4. 세션 생성: {"type":"sessions.create","model":"..."}
  5. 메시지 전송: {"type":"sessions.send","sessionId":"...","message":"..."}
  6. 응답 스트리밍: 여러 {"type":"sessions.message",...} 프레임 수신

주의사항:
  - OpenClaw 실제 API는 이 구현 시점(2026-04-09) 기준 추정 스펙이다.
  - openclaw_client.py 는 게이트웨이가 응답하지 않을 경우 graceful fallback을
    지원한다 (calling code에서 OpenClawUnavailableError를 잡아 처리).
  - 재연결: 지수 백오프 (1s, 2s, 4s, 8s, 최대 16s)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import AsyncIterator

logger = logging.getLogger(__name__)

# ── 예외 ─────────────────────────────────────────────────────────────────────

class OpenClawUnavailableError(Exception):
    """게이트웨이가 실행 중이 아니거나 연결할 수 없을 때 발생."""


class OpenClawError(Exception):
    """게이트웨이에서 오류 응답을 받았을 때 발생."""


# ── 클라이언트 ───────────────────────────────────────────────────────────────

class OpenClawClient:
    """
    OpenClaw 게이트웨이 WebSocket 클라이언트.

    싱글톤으로 사용할 것을 권장한다 (get_client() 사용).
    """

    def __init__(self, port: int = 18789) -> None:
        self._port = port
        self._ws = None           # websockets.WebSocketClientProtocol
        self._device_token: str | None = None
        self._connected = False
        self._lock = asyncio.Lock()
        self._pending: dict[str, asyncio.Future] = {}
        self._session_queues: dict[str, asyncio.Queue] = {}
        self._listener_task: asyncio.Task | None = None
        self._token_path = _token_cache_path()

    # ── 공개 인터페이스 ──────────────────────────────────────────────────────

    async def ensure_connected(self) -> None:
        """연결되어 있지 않으면 연결을 시도한다. 실패 시 OpenClawUnavailableError."""
        async with self._lock:
            if self._connected and self._ws is not None:
                return
            await self._connect_with_backoff()

    async def send_message(
        self,
        message: str,
        session_id: str | None = None,
    ) -> AsyncIterator[dict]:
        """
        세션에 메시지를 전송하고 응답 프레임을 비동기 이터레이터로 반환한다.

        session_id가 None이면 새 세션을 자동 생성한다.
        """
        await self.ensure_connected()

        if session_id is None:
            session_info = await self.create_session()
            session_id = session_info["sessionId"]

        queue: asyncio.Queue = asyncio.Queue()
        self._session_queues[session_id] = queue

        try:
            await self._send_frame({
                "type": "sessions.send",
                "sessionId": session_id,
                "message": message,
            })

            # 응답 프레임 수신 (done 프레임이 오거나 타임아웃될 때까지)
            while True:
                try:
                    frame = await asyncio.wait_for(queue.get(), timeout=120.0)
                except asyncio.TimeoutError:
                    break

                if frame.get("type") == "sessions.done":
                    break
                if frame.get("type") == "error":
                    raise OpenClawError(frame.get("message", "Unknown error"))

                yield frame
        finally:
            self._session_queues.pop(session_id, None)

    async def create_session(self, model: str | None = None) -> dict:
        """새 OpenClaw 세션을 생성하고 세션 정보를 반환한다."""
        await self.ensure_connected()

        req_id = str(uuid.uuid4())
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[req_id] = future

        await self._send_frame({
            "type": "sessions.create",
            "requestId": req_id,
            **({"model": model} if model else {}),
        })

        try:
            result = await asyncio.wait_for(future, timeout=30.0)
        except asyncio.TimeoutError:
            self._pending.pop(req_id, None)
            raise OpenClawError("세션 생성 타임아웃")

        return result

    async def list_sessions(self) -> list[dict]:
        """활성 세션 목록을 반환한다."""
        await self.ensure_connected()

        req_id = str(uuid.uuid4())
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[req_id] = future

        await self._send_frame({
            "type": "sessions.list",
            "requestId": req_id,
        })

        try:
            result = await asyncio.wait_for(future, timeout=10.0)
        except asyncio.TimeoutError:
            self._pending.pop(req_id, None)
            return []

        return result if isinstance(result, list) else result.get("sessions", [])

    async def install_skill(self, skill_name: str) -> dict:
        """ClawHub에서 스킬을 설치한다."""
        await self.ensure_connected()

        req_id = str(uuid.uuid4())
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[req_id] = future

        await self._send_frame({
            "type": "skills.install",
            "requestId": req_id,
            "skillName": skill_name,
        })

        try:
            return await asyncio.wait_for(future, timeout=120.0)
        except asyncio.TimeoutError:
            self._pending.pop(req_id, None)
            raise OpenClawError(f"스킬 설치 타임아웃: {skill_name}")

    async def list_tools(self, session_id: str) -> list[dict]:
        """세션에서 사용 가능한 도구 목록을 반환한다."""
        await self.ensure_connected()

        req_id = str(uuid.uuid4())
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[req_id] = future

        await self._send_frame({
            "type": "tools.list",
            "requestId": req_id,
            "sessionId": session_id,
        })

        try:
            result = await asyncio.wait_for(future, timeout=10.0)
        except asyncio.TimeoutError:
            self._pending.pop(req_id, None)
            return []

        return result if isinstance(result, list) else result.get("tools", [])

    async def get_catalog(self) -> list[dict]:
        """ClawHub 스킬 카탈로그를 반환한다 (추천 스킬 목록)."""
        await self.ensure_connected()

        req_id = str(uuid.uuid4())
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[req_id] = future

        await self._send_frame({
            "type": "skills.catalog",
            "requestId": req_id,
        })

        try:
            result = await asyncio.wait_for(future, timeout=30.0)
        except asyncio.TimeoutError:
            self._pending.pop(req_id, None)
            return _default_catalog()

        return result if isinstance(result, list) else result.get("skills", _default_catalog())

    def is_connected(self) -> bool:
        """게이트웨이와 연결 중인지 여부를 반환한다."""
        return self._connected

    async def close(self) -> None:
        """연결을 닫는다."""
        self._connected = False
        if self._listener_task:
            self._listener_task.cancel()
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

    # ── 내부 구현 ────────────────────────────────────────────────────────────

    async def _connect_with_backoff(self) -> None:
        """지수 백오프로 게이트웨이에 연결 시도한다."""
        delay = 1.0
        max_delay = 16.0
        max_attempts = 5

        for attempt in range(1, max_attempts + 1):
            try:
                await self._do_connect()
                return
            except OpenClawUnavailableError:
                if attempt >= max_attempts:
                    raise
                logger.warning(
                    "[openclaw] 연결 실패 (시도 %d/%d), %.0fs 후 재시도...",
                    attempt,
                    max_attempts,
                    delay,
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, max_delay)

    async def _do_connect(self) -> None:
        """실제 WebSocket 연결 + 핸드셰이크를 수행한다."""
        try:
            import websockets  # type: ignore[import]
        except ImportError as exc:
            raise OpenClawUnavailableError(
                "websockets 패키지가 설치되지 않았습니다: pip install websockets"
            ) from exc

        uri = f"ws://127.0.0.1:{self._port}"
        try:
            ws = await websockets.connect(
                uri,
                open_timeout=5,
                ping_interval=20,
                ping_timeout=10,
            )
        except (OSError, ConnectionRefusedError, Exception) as exc:
            raise OpenClawUnavailableError(
                f"OpenClaw 게이트웨이에 연결할 수 없습니다 ({uri}): {exc}"
            ) from exc

        self._ws = ws

        # 핸드셰이크: operator 역할로 연결
        device_token = self._load_token()
        handshake: dict = {
            "type": "connect",
            "role": "operator",
            "scopes": ["read", "write"],
        }
        if device_token:
            handshake["deviceToken"] = device_token

        await ws.send(json.dumps(handshake))

        # connected 응답 대기 (5초 타임아웃)
        try:
            raw_resp = await asyncio.wait_for(ws.recv(), timeout=5.0)
        except asyncio.TimeoutError as exc:
            await ws.close()
            raise OpenClawUnavailableError("핸드셰이크 타임아웃") from exc

        resp = json.loads(raw_resp)
        if resp.get("type") != "connected":
            await ws.close()
            raise OpenClawUnavailableError(
                f"핸드셰이크 실패: {resp}"
            )

        # 새 토큰 캐싱
        new_token = resp.get("deviceToken")
        if new_token:
            self._device_token = new_token
            self._save_token(new_token)

        self._connected = True

        # 백그라운드 메시지 수신 루프 시작
        self._listener_task = asyncio.create_task(self._listener_loop())
        logger.info("[openclaw] Gateway connected (port %d)", self._port)

    async def _listener_loop(self) -> None:
        """게이트웨이로부터 프레임을 지속적으로 수신하는 루프."""
        ws = self._ws
        try:
            async for raw in ws:
                try:
                    frame = json.loads(raw)
                except json.JSONDecodeError:
                    logger.warning("[openclaw] Non-JSON frame: %s", raw[:200])
                    continue

                await self._dispatch_frame(frame)
        except Exception as exc:
            logger.warning("[openclaw] Listener loop ended: %s", exc)
        finally:
            self._connected = False
            # 대기 중인 모든 Future에 오류 전달
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(OpenClawUnavailableError("연결이 끊어졌습니다"))
            self._pending.clear()

    async def _dispatch_frame(self, frame: dict) -> None:
        """수신된 프레임을 requestId 또는 sessionId 기준으로 라우팅한다."""
        frame_type = frame.get("type", "")
        req_id = frame.get("requestId")
        session_id = frame.get("sessionId")

        # requestId 기반 응답 (create_session, install_skill 등)
        if req_id and req_id in self._pending:
            fut = self._pending.pop(req_id)
            if not fut.done():
                fut.set_result(frame)
            return

        # sessionId 기반 스트리밍 응답
        if session_id and session_id in self._session_queues:
            await self._session_queues[session_id].put(frame)
            return

        # 알 수 없는 프레임
        logger.debug("[openclaw] Unrouted frame type=%s", frame_type)

    async def _send_frame(self, frame: dict) -> None:
        """WebSocket으로 JSON 프레임을 전송한다."""
        if self._ws is None or not self._connected:
            raise OpenClawUnavailableError("게이트웨이에 연결되지 않았습니다")
        await self._ws.send(json.dumps(frame))

    def _load_token(self) -> str | None:
        """캐시 파일에서 디바이스 토큰을 로드한다."""
        try:
            if self._token_path.exists():
                data = json.loads(self._token_path.read_text())
                return data.get("deviceToken")
        except Exception:
            pass
        return None

    def _save_token(self, token: str) -> None:
        """디바이스 토큰을 캐시 파일에 저장한다."""
        try:
            self._token_path.parent.mkdir(parents=True, exist_ok=True)
            self._token_path.write_text(
                json.dumps({"deviceToken": token, "savedAt": time.time()})
            )
        except Exception as exc:
            logger.warning("[openclaw] 토큰 저장 실패: %s", exc)


# ── 싱글톤 ──────────────────────────────────────────────────────────────────

_client: OpenClawClient | None = None


def get_client(port: int = 18789) -> OpenClawClient:
    """OpenClawClient 싱글톤을 반환한다."""
    global _client
    if _client is None or _client._port != port:
        _client = OpenClawClient(port=port)
    return _client


# ── 유틸 ─────────────────────────────────────────────────────────────────────

def _token_cache_path() -> Path:
    """
    디바이스 토큰 캐시 파일 경로 (크로스플랫폼).

    config.py의 get_data_dir()을 사용하여 OS별 표준 경로를 반환한다:
    - macOS:   ~/Library/Application Support/office_claw/openclaw_token.json
    - Windows: %LOCALAPPDATA%/office_claw/openclaw_token.json
    - Linux:   ~/.local/share/office_claw/openclaw_token.json

    기존 XDG 경로에 토큰이 있는 사용자를 위해 1회 마이그레이션을 수행한다.
    """
    from office_claw_sidecar.config import get_data_dir
    import shutil

    new_path = get_data_dir() / "openclaw_token.json"

    # 1회 마이그레이션: 구 XDG 경로 → 신 OS 표준 경로
    if not new_path.exists():
        old_path = (
            Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
            / "office-claw"
            / "openclaw_token.json"
        )
        if old_path.exists():
            try:
                new_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(old_path), str(new_path))
                logger.info("[openclaw] 토큰 캐시 마이그레이션: %s -> %s", old_path, new_path)
            except Exception as exc:
                logger.warning("[openclaw] 토큰 캐시 마이그레이션 실패: %s", exc)

    return new_path


def _default_catalog() -> list[dict]:
    """OpenClaw 카탈로그 조회 실패 시 사용할 기본 추천 스킬 목록."""
    return [
        {
            "name": "gog-gmail",
            "displayName": "Gmail (GOG)",
            "description": "Gmail 읽기, 검색, 전송",
            "category": "이메일",
            "recommended": True,
        },
        {
            "name": "gog-sheets",
            "displayName": "Google Sheets (GOG)",
            "description": "스프레드시트 읽기, 쓰기, 분석",
            "category": "생산성",
            "recommended": True,
        },
        {
            "name": "excel-automation",
            "displayName": "Excel 자동화",
            "description": "Excel 파일 분석, 보고서 생성",
            "category": "생산성",
            "recommended": True,
        },
    ]
