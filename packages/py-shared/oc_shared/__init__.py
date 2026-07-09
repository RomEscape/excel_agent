"""oc_shared — sidecar와 relay가 공유하는 순수 파이썬 유틸.

여기에는 '도메인 로직'을 두지 않는다(마스킹·권한·LLM 호출 등은 sidecar 소유).
계약(oc_protocol)에 대한 얇은 함수만 담는다:
- codec  : Envelope 인코드/디코드, relay용 라우팅 헤더 파싱
- replay : (pairing_id, direction)별 seq 발급기 + 재연결 재개용 replay 버퍼
- auth   : relay 접속 인증용 페어링 토큰 서명/검증(HMAC)
"""

from .auth import sign_token, verify_token
from .codec import decode_envelope, encode_envelope, parse_routing
from .replay import ReplayBuffer, SeqCounter

__all__ = [
    "encode_envelope",
    "decode_envelope",
    "parse_routing",
    "SeqCounter",
    "ReplayBuffer",
    "sign_token",
    "verify_token",
]
