"""
OpenClaw 게이트웨이 WebSocket 클라이언트.

OpenClaw 프로토콜 흐름 (v4 req/res/event):
  1. ws://127.0.0.1:18789 에 연결
  2. 서버의 {"type":"event","event":"connect.challenge"} 수신
  3. {"type":"req","method":"connect","params":...} 전송
  4. {"type":"res","ok":true,"payload":{"type":"hello-ok",...}} 수신
  5. sessions.* / skills.* / tools.* 메서드를 req/res 형태로 호출
  6. chat / session.* 이벤트를 라우터 호환 프레임으로 변환

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
import sys
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
        self._session_key_by_id: dict[str, str] = {}
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
            session_key = str(session_info.get("key", "")).strip()
            session_id = str(session_info.get("sessionId", "")).strip() or session_key
        else:
            session_key = await self._resolve_session_key(session_id)

        if not session_key:
            raise OpenClawError("유효한 세션 키를 확인할 수 없습니다")

        # 이벤트 스트리밍 수신을 위해 세션 메시지 구독
        await self._request(
            method="sessions.messages.subscribe",
            params={"key": session_key},
            timeout=10.0,
        )

        queue: asyncio.Queue = asyncio.Queue()
        self._session_queues[session_key] = queue

        try:
            send_result = await self._request(
                method="sessions.send",
                params={
                    "key": session_key,
                    "message": message,
                    "idempotencyKey": str(uuid.uuid4()),
                },
                timeout=30.0,
            )
            run_id = str(send_result.get("runId", "")).strip() or None

            # 응답 프레임 수신 (done 프레임이 오거나 타임아웃될 때까지)
            while True:
                try:
                    frame = await asyncio.wait_for(queue.get(), timeout=120.0)
                except asyncio.TimeoutError:
                    break

                # 다른 runId 이벤트가 섞여 들어오는 경우 필터링
                if run_id and frame.get("_runId") and frame.get("_runId") != run_id:
                    continue

                if frame.get("type") == "sessions.done":
                    break
                if frame.get("type") == "error":
                    raise OpenClawError(frame.get("message", "Unknown error"))

                yield frame
        finally:
            self._session_queues.pop(session_key, None)
            try:
                await self._request(
                    method="sessions.messages.unsubscribe",
                    params={"key": session_key},
                    timeout=5.0,
                )
            except Exception:
                pass

    async def create_session(self, model: str | None = None) -> dict:
        """새 OpenClaw 세션을 생성하고 세션 정보를 반환한다."""
        await self.ensure_connected()
        result = await self._request(
            method="sessions.create",
            params={**({"model": model} if model else {})},
            timeout=30.0,
        )
        session_id = str(result.get("sessionId", "")).strip()
        session_key = str(result.get("key", "")).strip()
        if session_id and session_key:
            self._session_key_by_id[session_id] = session_key
        return result

    async def list_sessions(self) -> list[dict]:
        """활성 세션 목록을 반환한다."""
        await self.ensure_connected()
        result = await self._request(
            method="sessions.list",
            params={},
            timeout=10.0,
        )
        sessions = result if isinstance(result, list) else result.get("sessions", [])
        for row in sessions:
            sid = str(row.get("sessionId", "")).strip()
            key = str(row.get("key", "")).strip()
            if sid and key:
                self._session_key_by_id[sid] = key
        return sessions

    async def install_skill(self, skill_name: str) -> dict:
        """ClawHub에서 스킬을 설치한다."""
        await self.ensure_connected()
        return await self._request(
            method="skills.install",
            params={
                "source": "clawhub",
                "slug": skill_name,
            },
            timeout=120.0,
        )

    async def list_tools(self, session_id: str) -> list[dict]:
        """세션에서 사용 가능한 도구 목록을 반환한다."""
        await self.ensure_connected()
        session_key = await self._resolve_session_key(session_id)
        if not session_key:
            return []

        # v4에서 tools.list 대신 tools.catalog/tools.effective를 사용
        catalog = await self._request(
            method="tools.catalog",
            params={},
            timeout=10.0,
        )
        groups = catalog.get("groups", []) if isinstance(catalog, dict) else []
        tools: list[dict] = []
        for group in groups:
            group_tools = group.get("tools", [])
            if isinstance(group_tools, list):
                tools.extend(group_tools)
        return tools

    async def get_catalog(self) -> list[dict]:
        """ClawHub 스킬 카탈로그를 반환한다 (추천 스킬 목록)."""
        await self.ensure_connected()
        result = await self._request(
            method="skills.search",
            params={"query": "gog", "limit": 20},
            timeout=30.0,
        )
        rows = result.get("results", []) if isinstance(result, dict) else []
        catalog = []
        for row in rows:
            slug = str(row.get("slug", "")).strip()
            if not slug:
                continue
            catalog.append(
                {
                    "name": slug,
                    "displayName": row.get("displayName", slug),
                    "description": row.get("summary", ""),
                    "recommended": True,
                }
            )
        return catalog or _default_catalog()

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

        # 1) server-initiated connect.challenge 대기
        challenge_nonce: str | None = None
        challenge_deadline = asyncio.get_event_loop().time() + 5.0
        while asyncio.get_event_loop().time() < challenge_deadline:
            timeout = max(0.1, challenge_deadline - asyncio.get_event_loop().time())
            raw_frame = await asyncio.wait_for(ws.recv(), timeout=timeout)
            frame = json.loads(raw_frame)
            if frame.get("type") == "event" and frame.get("event") == "connect.challenge":
                payload = frame.get("payload") or {}
                nonce = payload.get("nonce")
                if isinstance(nonce, str) and nonce.strip():
                    challenge_nonce = nonce.strip()
                    break

        if not challenge_nonce:
            await ws.close()
            raise OpenClawUnavailableError("핸드셰이크 실패: connect.challenge nonce 수신 불가")

        # 2) connect req 전송 (gateway protocol v4)
        auth_token = (os.environ.get("OPENCLAW_GATEWAY_TOKEN") or "").strip()
        if not auth_token:
            auth_token = self._load_gateway_token_from_openclaw_config() or ""
        cached_token = self._load_token()
        connect_params: dict = {
            "minProtocol": 4,
            "maxProtocol": 4,
            "client": {
                "id": "gateway-client",
                "version": "office-claw-sidecar/0.1.0",
                "platform": sys.platform,
                "mode": "backend",
            },
            "role": "operator",
            "scopes": ["operator.admin"],
        }
        if auth_token or cached_token:
            connect_params["auth"] = {"token": auth_token or cached_token}

        connect_id = str(uuid.uuid4())
        await ws.send(
            json.dumps(
                {
                    "type": "req",
                    "id": connect_id,
                    "method": "connect",
                    "params": connect_params,
                }
            )
        )

        # 3) connect 응답 대기
        hello_payload: dict | None = None
        handshake_deadline = asyncio.get_event_loop().time() + 10.0
        while asyncio.get_event_loop().time() < handshake_deadline:
            timeout = max(0.1, handshake_deadline - asyncio.get_event_loop().time())
            raw_frame = await asyncio.wait_for(ws.recv(), timeout=timeout)
            frame = json.loads(raw_frame)

            if frame.get("type") != "res" or frame.get("id") != connect_id:
                continue

            if not frame.get("ok"):
                await ws.close()
                error_msg = self._normalize_error_message(frame.get("error"))
                raise OpenClawUnavailableError(f"핸드셰이크 실패: {error_msg}")

            payload = frame.get("payload")
            hello_payload = payload if isinstance(payload, dict) else {}
            break

        if hello_payload is None:
            await ws.close()
            raise OpenClawUnavailableError("핸드셰이크 실패: connect 응답 타임아웃")

        # 새 디바이스 토큰 캐싱
        new_token = (
            hello_payload.get("auth", {}).get("deviceToken")
            if isinstance(hello_payload.get("auth"), dict)
            else None
        )
        if isinstance(new_token, str) and new_token.strip():
            self._device_token = new_token.strip()
            self._save_token(self._device_token)

        self._ws = ws
        self._connected = True
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
        """수신된 v4 req/res/event 프레임을 내부 큐/대기 future로 라우팅한다."""
        frame_type = frame.get("type", "")

        # req/res 응답 처리
        if frame_type == "res":
            req_id = frame.get("id")
            if req_id and req_id in self._pending:
                fut = self._pending.pop(req_id)
                if not fut.done():
                    if frame.get("ok"):
                        fut.set_result(frame.get("payload"))
                    else:
                        fut.set_exception(OpenClawError(self._normalize_error_message(frame.get("error"))))
            return

        if frame_type != "event":
            logger.debug("[openclaw] Unrouted non-event frame type=%s", frame_type)
            return

        event_name = frame.get("event", "")
        payload = frame.get("payload") if isinstance(frame.get("payload"), dict) else {}

        # chat 이벤트를 기존 agent router가 기대하는 sessions.* 프레임으로 변환
        if event_name == "chat":
            session_key = str(payload.get("sessionKey", "")).strip()
            if not session_key:
                return

            queue = self._session_queues.get(session_key)
            if queue is None:
                return

            normalized = self._normalize_chat_event(session_key, payload)
            if normalized:
                await queue.put(normalized)
                state = normalized.get("_state")
                if state in {"final", "aborted", "error"}:
                    await queue.put(
                        {
                            "type": "sessions.done",
                            "sessionId": normalized.get("sessionId", ""),
                            "sessionKey": session_key,
                            "_runId": normalized.get("_runId"),
                            "_state": state,
                        }
                    )
            return

        # tool 실행 결과 이벤트 매핑
        if event_name == "session.tool":
            session_key = str(payload.get("sessionKey", "")).strip()
            queue = self._session_queues.get(session_key)
            if queue is not None:
                await queue.put(
                    {
                        "type": "tool.result",
                        "sessionId": self._session_id_for_key(session_key),
                        "sessionKey": session_key,
                        **payload,
                    }
                )
            return

        # 승인 요청 이벤트 매핑
        if event_name == "exec.approval.requested":
            session_key = str(payload.get("sessionKey", "")).strip()
            queue = self._session_queues.get(session_key)
            if queue is not None:
                await queue.put(
                    {
                        "type": "exec.approval",
                        "sessionId": self._session_id_for_key(session_key),
                        "sessionKey": session_key,
                        "approvalId": payload.get("id"),
                        "toolName": payload.get("toolName", "unknown"),
                        "args": payload.get("args", {}) if isinstance(payload.get("args"), dict) else {},
                    }
                )
            return

        logger.debug("[openclaw] Unrouted event=%s", event_name)

    async def _send_frame(self, frame: dict) -> None:
        """
        하위 호환용 전송 함수.

        현재는 agent.py의 승인 응답(`exec.approval.response`) 경로만 사용한다.
        """
        frame_type = frame.get("type")
        if frame_type == "exec.approval.response":
            approval_id = str(frame.get("approvalId", "")).strip()
            approved = bool(frame.get("approved", False))
            if not approval_id:
                raise OpenClawError("approvalId가 비어 있어 승인 응답을 전송할 수 없습니다")
            await self._request(
                method="exec.approval.resolve",
                params={
                    "id": approval_id,
                    "decision": "approve" if approved else "reject",
                },
                timeout=15.0,
            )
            return

        raise OpenClawError(f"지원하지 않는 프레임 타입: {frame_type}")

    async def _request(self, method: str, params: dict, timeout: float = 30.0) -> dict:
        """게이트웨이 req/res 호출을 수행하고 payload(dict)를 반환한다."""
        if self._ws is None or not self._connected:
            raise OpenClawUnavailableError("게이트웨이에 연결되지 않았습니다")

        req_id = str(uuid.uuid4())
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[req_id] = future
        await self._ws.send(
            json.dumps(
                {
                    "type": "req",
                    "id": req_id,
                    "method": method,
                    "params": params,
                }
            )
        )

        try:
            payload = await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError as exc:
            self._pending.pop(req_id, None)
            raise OpenClawError(f"요청 타임아웃: {method}") from exc

        return payload if isinstance(payload, dict) else {}

    async def _resolve_session_key(self, session_id: str) -> str:
        """외부로 노출된 session_id(키 또는 UUID)를 session key로 정규화한다."""
        raw = (session_id or "").strip()
        if not raw:
            return ""
        if raw.startswith("agent:") or raw.startswith("global") or raw.startswith("unknown"):
            return raw

        cached = self._session_key_by_id.get(raw)
        if cached:
            return cached

        try:
            resolved = await self._request(
                method="sessions.resolve",
                params={"sessionId": raw},
                timeout=10.0,
            )
            key = str(resolved.get("key", "")).strip()
            sid = str(resolved.get("sessionId", "")).strip()
            if sid and key:
                self._session_key_by_id[sid] = key
            if key:
                return key
        except Exception:
            pass

        sessions = await self.list_sessions()
        for row in sessions:
            sid = str(row.get("sessionId", "")).strip()
            key = str(row.get("key", "")).strip()
            if sid and key:
                self._session_key_by_id[sid] = key
            if sid == raw and key:
                return key

        return ""

    def _session_id_for_key(self, session_key: str) -> str:
        for sid, key in self._session_key_by_id.items():
            if key == session_key:
                return sid
        return session_key

    def _normalize_chat_event(self, session_key: str, payload: dict) -> dict | None:
        """chat 이벤트 payload를 기존 router 호환 frame으로 변환한다."""
        state = str(payload.get("state", "")).strip()
        run_id = payload.get("runId")
        session_id = self._session_id_for_key(session_key)
        text = ""

        if state == "delta":
            text = str(payload.get("deltaText", "")).strip()
        else:
            text = self._extract_text_from_message(payload.get("message"))

        frame: dict = {
            "sessionId": session_id,
            "sessionKey": session_key,
            "_runId": run_id,
            "_state": state,
        }

        if state == "error":
            frame["type"] = "sessions.message"
            frame["content"] = (
                str(payload.get("errorMessage", "")).strip()
                or text
                or "OpenClaw chat 오류"
            )
            return frame

        frame["type"] = "sessions.message"
        if text:
            frame["content"] = text
        return frame

    def _extract_text_from_message(self, message: object) -> str:
        """OpenClaw message 객체에서 텍스트를 추출한다."""
        if isinstance(message, str):
            return message.strip()

        if not isinstance(message, dict):
            return ""

        if isinstance(message.get("text"), str):
            return str(message.get("text")).strip()

        parts: list[str] = []
        content = message.get("content")
        if isinstance(content, list):
            for item in content:
                if isinstance(item, str):
                    text = item.strip()
                    if text:
                        parts.append(text)
                elif isinstance(item, dict):
                    if item.get("type") == "text" and isinstance(item.get("text"), str):
                        text = item["text"].strip()
                        if text:
                            parts.append(text)

        return "\n".join(parts).strip()

    def _normalize_error_message(self, error: object) -> str:
        if isinstance(error, dict):
            msg = error.get("message")
            if isinstance(msg, str) and msg.strip():
                return msg.strip()
            code = error.get("code")
            if isinstance(code, str) and code.strip():
                return code.strip()
        return "Unknown error"

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

    def _load_gateway_token_from_openclaw_config(self) -> str | None:
        """
        ~/.openclaw/openclaw.json에서 gateway.auth.token을 읽는다.
        local-env.ps1과 동일한 우선순위 토큰 소스로 사용해 토큰 mismatch를 줄인다.
        """
        try:
            cfg_path = Path.home() / ".openclaw" / "openclaw.json"
            if not cfg_path.exists():
                return None
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            token = (
                ((cfg or {}).get("gateway") or {}).get("auth") or {}
            ).get("token")
            if isinstance(token, str):
                token = token.strip()
                if token:
                    return token
        except Exception as exc:
            logger.debug("[openclaw] openclaw.json 토큰 로드 실패: %s", exc)
        return None


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
