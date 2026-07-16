"""세션 라우팅 — pairing_id → (desktop_ws, mobile_ws).

인메모리 단일 프로세스 MVP. 멀티 인스턴스로 확장할 때는 이 레지스트리를 Redis pub/sub
백플레인으로 교체한다(라우팅 인터페이스는 유지 → 상위 코드 불변).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Literal, Optional

from fastapi import WebSocket

Role = Literal["desktop", "mobile"]


def _peer_role(role: Role) -> Role:
    return "mobile" if role == "desktop" else "desktop"


@dataclass
class Session:
    pairing_id: str
    desktop: Optional[WebSocket] = None
    mobile: Optional[WebSocket] = None

    def get(self, role: Role) -> Optional[WebSocket]:
        return self.desktop if role == "desktop" else self.mobile

    def set(self, role: Role, ws: Optional[WebSocket]) -> None:
        if role == "desktop":
            self.desktop = ws
        else:
            self.mobile = ws

    def peer(self, role: Role) -> Optional[WebSocket]:
        return self.get(_peer_role(role))


class SessionRegistry:
    def __init__(self) -> None:
        self._sessions: Dict[str, Session] = {}

    def connect(self, pairing_id: str, role: Role, ws: WebSocket) -> Session:
        sess = self._sessions.setdefault(pairing_id, Session(pairing_id))
        sess.set(role, ws)
        return sess

    def disconnect(self, pairing_id: str, role: Role) -> Optional[WebSocket]:
        """role 소켓을 제거하고, (통지용으로) 남아있는 상대 소켓을 반환."""
        sess = self._sessions.get(pairing_id)
        if sess is None:
            return None
        sess.set(role, None)
        peer = sess.peer(role)
        if sess.desktop is None and sess.mobile is None:
            self._sessions.pop(pairing_id, None)
        return peer

    def peer(self, pairing_id: str, role: Role) -> Optional[WebSocket]:
        sess = self._sessions.get(pairing_id)
        return sess.peer(role) if sess else None
