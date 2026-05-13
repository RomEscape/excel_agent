# Office Claw Architecture

## 개요

Office Claw는 개인정보 보호 중심의 로컬 AI 업무 에이전트입니다.
모든 데이터 처리가 사용자의 로컬 머신에서 수행되며, 클라우드 서버를 경유하지 않습니다.

## 아키텍처

```
┌─────────────────────────────────────────────────┐
│                  Tauri Desktop App               │
│  ┌──────────┐    ┌──────────────────────────┐   │
│  │ Frontend │    │    Rust Backend           │   │
│  │ HTML/JS  │───>│  - System Tray            │   │
│  │          │    │  - IPC Proxy (reqwest)     │   │
│  └──────────┘    │  - Sidecar Lifecycle      │   │
│                  └──────────┬───────────────┘   │
│                             │ HTTP (127.0.0.1)   │
│                  ┌──────────▼───────────────┐   │
│                  │  Python Sidecar (FastAPI)  │   │
│                  │  - Keyring Service         │   │
│                  │  - Ollama Client           │   │
│                  │  - Claude Client           │   │
│                  │  - Audit Logger            │   │
│                  └──────────┬───────────────┘   │
│                             │                    │
└─────────────────────────────┼────────────────────┘
                              │
              ┌───────────────┼───────────────┐
              │               │               │
    ┌─────────▼──┐  ┌────────▼───┐  ┌────────▼───────┐
    │  OS Keyring │  │   Ollama   │  │  External APIs  │
    │  (Cred Mgr) │  │  (Local)   │  │ (Gmail/Slack/   │
    │             │  │            │  │  Telegram)       │
    └─────────────┘  └────────────┘  └─────────────────┘
```

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
