# oc-protocol — 와이어 프로토콜 SSOT

데스크톱(sidecar) ↔ 중계 서버(relay) ↔ 모바일(Flutter) 3자가 공유하는 계약의 **단일 소스**.

## 구조

```
packages/protocol/
├─ python/oc_protocol/     # Pydantic 모델 (SSOT) — sidecar/relay가 직접 import
│  ├─ envelope.py          #   Envelope(평문 라우팅) + RoutingHeader(relay 전용)
│  └─ frames.py            #   Frame discriminated union (payload)
├─ scripts/export_schema.py# Pydantic → JSON Schema 생성기
├─ schema/                 # 생성물: *.schema.json (Dart codegen 입력)
└─ dart/                   # 생성물: json_serializable Dart 모델 (Flutter, 추후)
```

## 핵심 설계

- **relay는 content-blind.** `Envelope`(평문)의 라우팅 헤더(`pairing_id`/`direction`/`seq`)만
  보고 원본 프레임을 그대로 전달한다. payload는 절대 해석하지 않으므로, 나중에 E2E 암호화를
  도입해 payload를 암호문 blob으로 바꿔도 relay 로직은 그대로다.
- **`seq`** 는 (pairing_id, direction)별 단조 증가 순번. 재연결 시 마지막 `Ack.ack_seq` 이후만
  재전송해 토큰 유실·중복을 막는다.
- **discriminated union**(`type` 필드)으로 프레임을 분기 — 새 프레임 추가가 안전하다.

## 스키마 재생성

```bash
cd packages/protocol/python
uv run python ../scripts/export_schema.py   # → ../schema/*.json
```

CI는 재생성 후 `git diff`가 비어야 통과(계약 drift 방지). Dart 모델은 이 schema에서만 생성하고
직접 수정하지 않는다.
