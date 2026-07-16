"""재연결 재개 — seq 발급기 + bounded replay 버퍼.

모바일이 백그라운드 전환·네트워크 플랩으로 재연결할 때, 마지막으로 받은 Ack.ack_seq
이후 프레임만 재전송하면 토큰 유실·중복·순서 뒤바뀜을 막을 수 있다. 송신측은 보낸 프레임을
seq와 함께 버퍼에 남겨두고, 상대의 ack를 받으면 그 이하를 폐기한다. 메모리 경계를 위해
최대 크기로 제한한다.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Deque, List, Tuple


class SeqCounter:
    """(pairing_id, direction) 단위 단조 증가 seq 발급기."""

    def __init__(self, start: int = 0) -> None:
        self._seq = start

    def next(self) -> int:
        self._seq += 1
        return self._seq

    @property
    def current(self) -> int:
        return self._seq


@dataclass
class ReplayBuffer:
    """seq→raw 프레임의 링 버퍼(최근 maxlen개만 유지)."""

    maxlen: int = 256
    _buf: Deque[Tuple[int, str]] = field(default_factory=deque)

    def add(self, seq: int, raw: str) -> None:
        self._buf.append((seq, raw))
        while len(self._buf) > self.maxlen:
            self._buf.popleft()

    def since(self, ack_seq: int) -> List[Tuple[int, str]]:
        """ack_seq 초과분(재전송 대상)을 seq 오름차순으로 반환."""
        return [(s, r) for (s, r) in self._buf if s > ack_seq]

    def prune(self, ack_seq: int) -> None:
        """ack_seq 이하(수신 확인분)를 버려 메모리를 회수한다."""
        while self._buf and self._buf[0][0] <= ack_seq:
            self._buf.popleft()

    def __len__(self) -> int:
        return len(self._buf)
