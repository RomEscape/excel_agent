# oc-shared — sidecar↔relay 공용 순수 파이썬

`oc-protocol` 계약 위에서 동작하는 **얇은 함수 모음**. 도메인 로직(마스킹·권한·LLM)은 절대
여기 두지 않는다 — 그건 sidecar 소유다(CLAUDE.md 모듈 규칙).

| 모듈 | 역할 |
|---|---|
| `codec` | Envelope ↔ WS 텍스트. `parse_routing`은 relay 전용(payload 무시, content-blind) |
| `replay` | `SeqCounter`(seq 발급) + `ReplayBuffer`(재연결 시 ack 이후만 재전송) |
| `auth` | 페어링 토큰 HMAC 서명/검증(relay 접속 인증 — E2E 기밀성과는 별개) |

sidecar와 relay가 각자 `[tool.uv.sources]`로 이 패키지를 editable 참조한다.
