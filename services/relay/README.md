# oc-relay — 중계 서버 (content-blind WebSocket 릴레이)

데스크톱(sidecar)과 모바일(Flutter)이 **둘 다 아웃바운드로** relay에 접속하고(양쪽 NAT 뒤),
relay가 `pairing_id`로 두 소켓을 짝지어 프레임을 브리지한다.

## 왜 이 구조인가 (설계 리뷰 반영)

- **content-blind**: relay는 `oc_shared.parse_routing`으로 Envelope의 라우팅 헤더만 읽고
  payload는 해석/저장하지 않는다. 수신한 원본 텍스트를 그대로 전달 → E2E 도입 시 payload를
  암호문으로 바꿔도 relay 로직 불변. Zero-Trust: 평문·비밀은 사용자 기기를 떠나지 않는다.
- **아웃바운드 전용**: 데스크톱/모바일 모두 relay로 dial-out(인바운드 못 받음). NAT/방화벽 통과.
- **presence**: 상대 접속/이탈을 relay control 메시지(`{"control":"peer_status"}`)로 통지 —
  Envelope 프레임과 분리되어 content-blind를 깨지 않음.

## 엔드포인트

| 경로 | 용도 |
|---|---|
| `GET /health` | 헬스체크 |
| `POST /pair/start` | 데스크톱: `{pairing_id, code}` 발급(QR용) |
| `POST /pair/complete {code}` | 모바일: code로 1:1 바인딩 확정 |
| `WS /ws/desktop?pairing_id=…` | 데스크톱 레그 |
| `WS /ws/mobile?pairing_id=…` | 모바일 레그 |

## 실행 / 검증

```bash
cd services/relay
uv sync --extra dev
uv run pytest -q          # 라우팅/페어링 통합 테스트
uv run python -m oc_relay # 서버 기동 (PORT 기본 8787)
```

## MVP 한계 → 프로덕션 전 강화 항목

- 페어링 `code`에 짧은 TTL + 시도 rate-limit, QR에 데스크톱 공개키 + **SAS 육안 대조**(relay MITM 차단).
- 데스크톱↔모바일 **E2E 암호화**(X25519 키교환 → Noise/double-ratchet), 승인 프레임 MAC.
- 재연결 재개: 프레임 `seq` + `Ack` + `ReplayBuffer`(oc_shared) 연동, iOS 백그라운드는 APNs/FCM로 wake.
- 수평 확장: 인메모리 세션 → Redis pub/sub 백플레인(인스턴스 2개 이상/무중단 배포 시).
