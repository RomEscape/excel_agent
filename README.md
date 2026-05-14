# Office Claw — 아키텍처

개인정보 보호 중심의 로컬 AI 업무 에이전트. 모든 데이터 처리가 사용자 머신에서 끝나며 중계 서버를 두지 않는다. 데스크탑 앱(Tauri) + 로컬 FastAPI 사이드카 + 로컬 LLM(Ollama 또는 OpenClaw 게이트웨이) 3-계층 구성.

---

## 1. 전체 구성도

```
┌───────────────────────────────────────────────────────────────────────────┐
│                          Tauri Desktop App                                │
│                                                                           │
│  ┌─────────────────────────┐         ┌──────────────────────────────┐    │
│  │   Frontend (Webview)    │         │   Native Layer (Rust)         │    │
│  │   React + Vite + TW     │ ◄────► │   - System Tray                │    │
│  │   - app shell           │ invoke  │   - IPC commands (ipc.rs)      │    │
│  │   - feature modules     │  IPC   │   - Sidecar lifecycle           │    │
│  │   - zustand stores      │         │   - OpenClaw / Ollama          │    │
│  │   - UI primitives       │         │   - Installer (live log)       │    │
│  └─────────────────────────┘         └─────────────┬────────────────┘    │
│                                                    │                      │
│                                       HTTP (127.0.0.1, Bearer)            │
│                                                    │                      │
│  ┌─────────────────────────────────────────────────▼──────────────────┐  │
│  │              Python Sidecar (FastAPI, uvicorn)                      │  │
│  │                                                                     │  │
│  │   routers/   ──▶  services/  ──▶  models/   ──▶  sandbox/audit     │  │
│  │   (HTTP)         (도메인 로직)     (Pydantic)     (실행/감사)        │  │
│  └────┬────────────┬─────────────┬───────────────────────┬────────────┘  │
│       │            │             │                       │               │
└───────┼────────────┼─────────────┼───────────────────────┼───────────────┘
        │            │             │                       │
   ┌────▼────┐  ┌────▼────┐   ┌────▼────────┐    ┌────────▼─────────┐
   │  OS     │  │ Ollama  │   │  OpenClaw   │    │ Messengers /     │
   │ Keyring │  │ (local) │   │  Gateway    │    │ External APIs    │
   │         │  │         │   │  (local)    │    │ Telegram/Slack/  │
   │         │  │         │   │             │    │ Discord/Gmail    │
   └─────────┘  └─────────┘   └─────────────┘    └──────────────────┘
```

---

## 2. 계층별 책임

### 2.1 Frontend — `src/`

React 단일 페이지 앱. Vite로 번들되어 Tauri webview 안에서 동작한다.

| 디렉토리 | 책임 |
|---|---|
| `src/App.jsx`, `main.jsx` | 부트스트랩, 라우팅, 글로벌 provider |
| `src/components/layout/` | App shell (Sidebar, StatusBar, Layout) |
| `src/components/cmdk/` | 글로벌 명령 팔레트 + 단축키 안내 |
| `src/components/<domain>/` | 기능 모듈 — dashboard, conversations, workspace, email, excel, document, telegram, settings, security, audit, permissions, credentials, onboarding, guide, updater |
| `src/components/ui/` | 공용 primitive (Button, Dialog, Status, Toast 등) |
| `src/store/` | Zustand 전역 store (`appStore`, `statusStore`) |
| `src/lib/` | 도메인 로직 모듈 — `api.js`(IPC wrapper 단일 진입), `statusManager.js`(상태 액션), `statusTokens.js`(상태 토큰), `localAISetup.js`(설치 플랜), `updater.js`, `errorMessages.js` |
| `src/hooks/` | 재사용 hook (`useAsync`, `useStatusPoller`) |

**상태 흐름 원칙:** 도메인 로직과 상태는 `src/lib/*Manager.js` + `src/store/*Store.js`가 소유하고, UI 컴포넌트는 구독·조합만 한다. IPC 호출은 전부 `src/lib/api.js`에 모인 wrapper를 거친다.

### 2.2 Native Layer — `src-tauri/`

Rust로 작성된 Tauri 백엔드. 시스템 트레이, IPC 진입점, 외부 프로세스 라이프사이클을 담당한다.

| 모듈 | 역할 |
|---|---|
| `src/main.rs`, `lib.rs` | Tauri Builder · invoke handler 등록 · 시작 시 OpenClaw → Sidecar 순서로 spawn |
| `src/ipc.rs` | 프론트엔드에서 호출하는 모든 `#[tauri::command]` 정의 (credentials, chat, gmail, telegram, slack, discord, workspace, excel, document, agent, security, audit, backup, updater 등). 대부분은 사이드카에 HTTP로 프록시. |
| `src/sidecar.rs` | Python 사이드카 자식 프로세스 spawn / health check / Bearer 토큰 발급 |
| `src/openclaw.rs` | OpenClaw 게이트웨이 spawn-and-go + health-poll |
| `src/ollama.rs` | Ollama 데몬 상태 확인 · 모델 목록 조회 · 모델명 검증 |
| `src/installer.rs` | macOS Homebrew 기반 Ollama/OpenClaw 자동 설치, 실시간 로그 스트리밍 (frontend로 event emit), 취소 처리 |
| `src/tray.rs` | 시스템 트레이 메뉴, 창 표시/종료 |
| `build.rs`, `tauri.conf.json` | 빌드 설정, `externalBin`으로 사이드카 바이너리 번들 |

**상태 보관**: `tauri::State<Mutex<...>>`로 모듈별 상태 보관 (`OpenClawState`, `InstallerState`, `SidecarState`).

### 2.3 Python Sidecar — `python-sidecar/`

FastAPI + uvicorn. 도메인 비즈니스 로직(LLM 호출, 메신저, 문서 처리, 보안, 감사)이 모두 여기서 돈다. 127.0.0.1에만 바인딩하고 Bearer 토큰으로 인증한다.

```
office_claw_sidecar/
├── main.py            # FastAPI 앱 + lifespan(시작 시 화이트리스트/봇 자동 시작)
├── config.py          # 앱 데이터 디렉토리 경로
├── analyzer.py        # 코드/명령 정적 분석 (샌드박스 화이트리스트 검사)
├── sandbox.py         # 도구 실행 샌드박스
├── command_audit.py   # 명령 감사 로그(JSONL)
├── chat_history.py    # 채팅 세션 영속화
├── backup.py          # 백업/복원
├── routers/           # FastAPI 엔드포인트 (HTTP 레이어)
│   ├── health, credentials, llm, audit, telegram
│   ├── workspace, settings, maintenance
│   ├── agent, skills, security        # OpenClaw 에이전트 + 보안 대시보드
│   ├── slack, discord, permissions    # 멀티 메신저 + 권한
│   ├── chat, backup                   # 영속화/백업
│   └── legacy                         # gmail/excel/document → 410 Gone 안내
├── services/          # 도메인 서비스 (재사용 가능 로직)
│   ├── keyring_service     # OS 보안 저장소 wrapper
│   ├── llm_service / claude_service / ollama_service
│   ├── openclaw_client     # OpenClaw 게이트웨이 HTTP 클라이언트
│   ├── telegram_service / intent_router
│   ├── audit_service / masking_service / filter_service
│   ├── document_service / excel_service / gmail_service  # legacy 직접 처리 (deprecated)
│   └── tool_registry       # 권한 레벨(SAFE/CONFIRM/DENIED) + 런타임 화이트리스트
├── messenger/         # 메신저 어댑터 (공통 ABC + Slack/Discord/Telegram)
├── models/            # Pydantic 모델 (approval, audit, credential, llm, masking)
└── utils/             # platform 감지 등 헬퍼
```

**모듈 경계:** router는 HTTP I/O만, service는 도메인 로직, model은 데이터 형상. 한 라우터가 다른 라우터 함수를 직접 호출하지 않고 service를 거친다.

### 2.4 외부 의존성

| 대상 | 통신 방식 | 용도 |
|---|---|---|
| **OpenClaw Gateway** | localhost HTTP (자식 프로세스로 spawn) | Claude 기반 에이전트 런타임. 사이드카가 client로 호출 |
| **Ollama** | localhost HTTP (`brew services start ollama`) | 로컬 LLM 추론. 사이드카가 직접 호출 (OpenClaw 미실행 시 fallback) |
| **OS Keyring** | `keyring` 라이브러리 | macOS Keychain / Windows Credential Manager / Linux Secret Service. 봇 토큰·API 키 보관 |
| **Messengers** | 각 SDK (`python-telegram-bot`, `slack-bolt`, `discord.py`) | 사용자가 메신저로 명령 → 사이드카 → AI 실행 |
| **Office APIs** | `google-api-python-client` 외 | Gmail/Calendar (legacy, OpenClaw 에이전트가 도구로 호출하는 방향으로 이전 중) |

---

## 3. 핵심 데이터 흐름

### 3.1 사용자 채팅 → AI 실행

```
[User in webview]
    │  React onClick
    ▼
src/lib/api.js  ─── invoke('agent_chat', ...) ───▶  Rust ipc::agent_chat
                                                         │ reqwest POST
                                                         ▼
                                                 sidecar /agent/chat (Bearer)
                                                         │
                                          routers/agent.py → services/llm_service
                                                         │
                                          OpenClaw Gateway 또는 Ollama 호출
                                                         │
                                          tool 호출 시 tool_registry 검사
                                          → SAFE: 즉시 실행
                                          → CONFIRM: 텔레그램 승인 대기 (또는 UI 폴백)
                                          → DENIED: 거부
                                                         │
                                          command_audit.py 로 JSONL 기록
                                                         │
                                          응답 streaming → 프론트엔드 갱신
```

### 3.2 메신저 → AI 자율 실행

```
[Telegram User] ── 메시지 ──▶ python-telegram-bot
                                    │
                            telegram_service (intent_router)
                                    │
                            ▶ 의도 분류 후 agent.chat 호출 (또는 직접 도구 실행)
                                    │
                            tool_registry 검사 → CONFIRM 시 사용자에게 인라인 키보드로 확인
                                    │
                            실행 결과 → 메신저로 회신 + command_audit.log
```

### 3.3 시작 시 부트 시퀀스

1. **Tauri Builder.setup**: `OpenClawState`, `InstallerState` 등록
2. **async spawn**: `openclaw::spawn_openclaw` → 자식 프로세스 띄우고 health poll (실패해도 비치명)
3. **async spawn**: `sidecar::spawn_sidecar` → PyInstaller로 번들된 `office-claw-sidecar` 바이너리 실행, 랜덤 포트 + Bearer 토큰 발급
4. **sidecar lifespan**: 임시 파일 정리 → 화이트리스트 로드 → 저장된 봇 토큰 있으면 텔레그램 봇 자동 시작
5. **tray::setup_tray**: 시스템 트레이 아이콘/메뉴 등록
6. **webview**: React 앱 마운트 → `statusManager`가 OpenClaw/Sidecar/Ollama 상태 폴링 시작

---

## 4. 보안 모델

| 원칙 | 구현 |
|---|---|
| **Credential Isolation** | OS Keyring 사용 (`services/keyring_service.py`). 토큰/키를 파일이나 환경변수에 평문 저장하지 않음 |
| **No-Middleman** | 모든 외부 API는 사용자 머신에서 직접 호출. 자체 서버 없음 |
| **Local-only Networking** | 사이드카/게이트웨이는 `127.0.0.1`에만 바인딩. 외부 인입 불가 |
| **Bearer Auth** | Tauri ↔ Sidecar 통신은 시작 시 발급된 임시 Bearer 토큰 필요. `verify_auth` 의존성으로 모든 라우터 보호 |
| **Tool Sandbox** | `tool_registry`에 등록된 도구만 실행 가능. SAFE/CONFIRM/DENIED 3단계 권한 |
| **Approval Flow** | CONFIRM 등급 작업은 텔레그램 메신저 (또는 앱 UI) 승인 필요 — `routers/security.py`, `components/security/ApprovalDialog.jsx` |
| **PII Masking** | `masking_service`로 응답 전 민감정보 마스킹. 마스킹 통계는 `security_stats`로 노출 |
| **Audit Log** | 모든 명령/도구 호출은 JSONL로 로컬 기록 (`command_audit.py`). UI는 `AuditLog.jsx`에서 조회 |

---

## 5. 빌드 & 배포

| 단계 | 도구 | 산출물 |
|---|---|---|
| Frontend 번들 | Vite | `dist/` (Tauri가 webview에 로드) |
| Python 사이드카 패키징 | PyInstaller (`build_sidecar.py`, `office_claw_sidecar.spec`) | `src-tauri/binaries/office-claw-sidecar-<target-triple>` |
| 데스크탑 앱 빌드 | `cargo tauri build` | `.app` / `.dmg` / `.msi` / `.AppImage` (플랫폼별) |
| 자동 업데이트 | `tauri-plugin-updater` + `release.yml` | GitHub Releases에 서명된 업데이트 매니페스트 게시 |
| CI (PR 검증) | `.github/workflows/pr-check.yml` | python-check(ruff+pytest) / frontend-check(npm ci+test) / rust-check(fmt+clippy) |
| 개발 실행 | `npm run tauri:dev` | Vite dev server + cargo tauri dev (자동 hot reload) |

자세한 빌드 가이드는 `docs/PYINSTALLER_BUILD_GUIDE.md`, OpenClaw 사용은 `docs/OPENCLAW_USAGE.md` 참조.

---

## 6. 디렉토리 한눈에

```
office_claw/
├── src/                       # React frontend
├── src-tauri/                 # Tauri Rust backend + native bridges
│   ├── src/                   # Rust 소스 (ipc/sidecar/openclaw/ollama/installer/tray)
│   ├── binaries/              # 번들 사이드카 바이너리 (PyInstaller 산출물)
│   ├── capabilities/          # Tauri ACL 매니페스트
│   └── tauri.conf.json
├── python-sidecar/            # FastAPI 사이드카
│   ├── office_claw_sidecar/   # 패키지 본체
│   ├── tests/                 # pytest (health, local AI, sprint suites)
│   └── pyproject.toml
├── docs/                      # 추가 문서
├── scripts/                   # 개발 스크립트 (dev.sh, dev.ps1)
├── .github/workflows/         # CI/CD (pr-check, release)
└── CLAUDE.md                  # 프로젝트 규칙 (모듈/객체지향 + 한국어 컨벤션)
```
