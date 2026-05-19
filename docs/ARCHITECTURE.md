# Office Claw Architecture

## 개요

Office Claw는 개인정보 보호 중심의 로컬 AI 업무 에이전트입니다.
모든 데이터 처리가 사용자의 로컬 머신에서 수행되며, 클라우드 서버를 경유하지 않습니다.

## 아키텍처 (2026-05)

**핵심 시나리오**: 사용자는 모바일에서 텔레그램/디스코드/슬랙 봇으로 대화하고, 그 메시지가 OpenClaw 게이트웨이의 입력으로 전달된다. OpenClaw는 *로컬 Ollama 모델*을 LLM 엔진으로 사용. Tauri 앱은 (1) Ollama 데몬 설치/모델 다운로드 보조, (2) Python sidecar·OpenClaw 게이트웨이 라이프사이클, (3) 보안 wrapper(자격증명/감사로그) 역할.

```
모바일 사용자
    │
    ▼ 텔레그램·Discord·Slack
┌──────────────────────────────────────────────────────────┐
│              Python Sidecar (FastAPI, Tauri가 spawn)       │
│              ┌──────────────────────┐                      │
│              │ 봇 라우터 + intent     │                      │
│              │ + PII 마스킹           │                      │
│              └─────────┬────────────┘                      │
│                        │                                    │
│              ┌─────────▼────────────┐                      │
│              │ OpenClaw 클라이언트     │                      │
│              └─────────┬────────────┘                      │
└────────────────────────┼───────────────────────────────────┘
                         │ WebSocket :18789
                         ▼
                  ┌──────────────┐
                  │  OpenClaw    │     ─── LLM 호출
                  │  Gateway     │ ───>   Ollama :11434
                  │  (Node)      │       (로컬 모델, OpenClaw
                  └──────────────┘        엔진 역할)


┌──────────────────────────────────────────────────────────┐
│                Tauri Desktop App (Rust 코어)               │
│  ┌──────────┐  ┌────────────────────────────────────┐    │
│  │ React UI │─>│  Rust Backend                       │    │
│  │ (설정·   │  │  - System Tray, Auto-updater        │    │
│  │  대시보드│  │  - openclaw.rs  (게이트웨이 라이프사이클)│    │
│  │  ·설치 │  │  - ollama.rs    (설치/상태/모델 pull) │    │
│  │  마법사)│  │  - installer.rs (brew/npm wrappers) │    │
│  │          │  │  - sidecar.rs   (Python 자식프로세스) │    │
│  │          │  │  - keyring_svc.rs (OS creds)        │    │
│  │          │  │  - audit.rs (JSONL append-only)     │    │
│  └──────────┘  └────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────┘
       │            │              │              │
       ▼            ▼              ▼              ▼
  OS Keyring   audit.jsonl   Python Sidecar   Ollama / OpenClaw
  (Cred Mgr)   (공유 파일)    (Tauri가 spawn)   바이너리 (사용자 PC)
```

### 데이터 핵심 흐름

1. **메신저 봇 메시지 → LLM 응답** (제품의 주된 사용 경로):
   ```
   사용자 → 텔레그램 → Python 봇 라우터 → PII 마스킹
        → openclaw_client → WS → OpenClaw 게이트웨이
        → 로컬 Ollama 모델 → 응답 역방향
   ```
2. **자격증명**: Python·Rust 어느 쪽이든 OS Keychain 동일 namespace(`office_claw`)에 저장. 레지스트리 파일(`credentials_registry.json`)도 공유.
3. **감사 로그**: Python·Rust 모두 같은 `audit.jsonl`에 append.

## 보안 4원칙

1. **Credential Isolation**: OS 수준의 보안 저장소 사용 (Windows Credential Manager / macOS Keychain / Linux Secret Service)
2. **No-Middleman**: 중계 서버 없이 사용자 앱이 직접 API 호출
3. **Local Indexing**: 로컬 Vector DB에서만 데이터 인덱싱
4. **Audit Logs**: 모든 데이터 접근 내역을 로컬 JSONL 파일에 기록

## IPC 통신

- Tauri ↔ Python Sidecar: localhost HTTP (랜덤 포트)
- 시작 시 생성된 Bearer 토큰으로 인증
- 127.0.0.1에만 바인딩하여 외부 접근 차단

## 디렉토리 구조

- `src-tauri/`: Tauri Rust 백엔드
- `src/`: 프론트엔드 (HTML/CSS/JS)
- `python-sidecar/`: Python FastAPI 사이드카
- `scripts/`: 개발/빌드 스크립트
- `docs/`: 문서
