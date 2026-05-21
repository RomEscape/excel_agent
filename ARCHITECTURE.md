# Office Claw — Architecture

코드 개발 시 참조할 구조 문서. README는 overview, 이 문서는 **새 기능을 어디에 둘지 / module boundary를 어떻게 지킬지** 판단용 reference.

새 기능은 `CLAUDE.md`의 modular OOP 원칙대로 **store + manager + UI primitive** 세트로 분리. 컴포넌트는 조합만. 새 도메인이 생기면 이 문서의 표에 한 줄 더 늘어나야 한다.

---

## 1. 전체 구성도

```
                            ┌────────────────────────────────┐
                            │   User on Messenger Client      │
                            │   Telegram / Slack / Discord    │
                            └────────────────┬───────────────┘
                                             │ message
                                             ▼
┌───────────────────────────────────────────────────────────────────────────┐
│                          Tauri Desktop App                                │
│                                                                           │
│  ┌─────────────────────────┐         ┌──────────────────────────────┐    │
│  │   Frontend (Webview)    │         │   Native Layer (Rust)         │    │
│  │   React + Vite + TW     │ ◄────► │   - System Tray                │    │
│  │   - 설정/상태/감사 UI    │ invoke  │   - IPC (ipc.rs)               │    │
│  │   - bot token / 승인 UI  │  IPC   │   - Sidecar lifecycle          │    │
│  │   - zustand stores      │         │   - OpenClaw (spawn / CLI)     │    │
│  │   - UI primitives       │         │   - Ollama / Installer         │    │
│  │                         │         │   - Keyring / Audit (Rust 평행) │    │
│  └─────────────────────────┘         └─────────────┬────────────────┘    │
│                                                    │                      │
│                                       HTTP (127.0.0.1, Bearer)            │
│                                                    │                      │
│  ┌─────────────────────────────────────────────────▼──────────────────┐  │
│  │              Python Sidecar (FastAPI, uvicorn)                      │  │
│  │                                                                     │  │
│  │  messenger bot listener ─▶ intent_router ─▶ agent / llm_service     │  │
│  │  routers/  →  services/  →  models/  →  sandbox / tool_registry    │  │
│  │                                              │                      │  │
│  │                                              └─▶ audit (JSONL)      │  │
│  └────┬────────────┬─────────────┬───────────────────────┬────────────┘  │
│       │            │             │                       │               │
└───────┼────────────┼─────────────┼───────────────────────┼───────────────┘
        │            │             │                       │
   ┌────▼────┐  ┌────▼────┐   ┌────▼────────┐    ┌────────▼─────────┐
   │  OS     │  │ Ollama  │   │  OpenClaw   │    │ Messenger /      │
   │ Keyring │  │ (local) │   │  Gateway    │    │ External APIs    │
   │         │  │         │   │  or CLI     │    │ Telegram/Slack/  │
   │         │  │         │   │  (local)    │    │ Discord/Gmail    │
   └─────────┘  └─────────┘   └─────────────┘    └──────────────────┘
```

---

## 2. Layer별 책임

### 2.1 Frontend — `src/`

React SPA, Vite로 번들되어 Tauri webview 안에서 동작. 사용자 대화는 messenger에서 일어나므로 **앱 UI의 1차 역할은 설정 · 상태 · 감사**.

| 디렉토리 | 책임 |
|---|---|
| `src/App.jsx`, `main.jsx` | Bootstrap, routing, global provider |
| `src/components/layout/` | App shell (Sidebar, StatusBar, Layout) |
| `src/components/cmdk/` | Command palette + 단축키 안내 |
| `src/components/<domain>/` | 기능 모듈 — dashboard, conversations, workspace, email, excel, document, telegram, settings, security, audit, permissions, credentials, onboarding, guide, updater |
| `src/components/ui/` | 공용 primitive (Button, Dialog, Status, Toast 등) |
| `src/store/` | Zustand store (`appStore`, `statusStore`) |
| `src/lib/` | Domain logic — `api.js`(IPC wrapper 단일 진입), `statusManager.js`, `statusTokens.js`, `localAISetup.js`, `updater.js`, `errorMessages.js` |
| `src/hooks/` | 재사용 hook (`useAsync`, `useStatusPoller`) |

**State flow 원칙:** Domain logic + state는 `src/lib/*Manager.js` + `src/store/*Store.js`가 소유. UI 컴포넌트는 구독 · 조합만. IPC 호출은 전부 `src/lib/api.js` wrapper 경유.

### 2.2 Native Layer — `src-tauri/`

Rust Tauri backend. System tray, IPC, 외부 프로세스 lifecycle, 그리고 보안 평행 경로(Keyring / Audit) 담당.

| 모듈 | 역할 |
|---|---|
| `src/main.rs`, `lib.rs` | Tauri Builder · invoke handler 등록 · 시작 시 OpenClaw → Sidecar 순서로 spawn |
| `src/ipc.rs` | 모든 `#[tauri::command]` 정의 — 대부분 sidecar로 HTTP proxy |
| `src/sidecar.rs` | Python sidecar 자식 프로세스 spawn / health check / Bearer token 발급 |
| `src/openclaw.rs` | OpenClaw Gateway spawn-and-go + health-poll |
| `src/openclaw_cli.rs` | OpenClaw **CLI subprocess wrapper** — WS gateway 미가용 시 대안 경로 |
| `src/ollama.rs` | Ollama daemon 상태 / model 목록 / 모델명 검증 |
| `src/installer.rs` | macOS Homebrew 기반 Ollama/OpenClaw 자동 설치, 실시간 log streaming (event emit), 취소 처리 |
| `src/keyring_svc.rs` | OS Keyring Rust 평행 경로 — Python `keyring_service`와 **같은 OS Keychain** 공유, `rustCredential*` IPC로 노출 |
| `src/audit.rs` | Audit log Rust 평행 경로 — Python `command_audit`와 **같은 `audit.jsonl`** 공유, `rustAudit*` IPC로 노출 |
| `src/tray.rs` | System tray menu, 창 표시/종료 |
| `build.rs`, `tauri.conf.json` | Build 설정, `externalBin`으로 sidecar binary bundle |

**State 보관:** `tauri::State<Mutex<...>>`로 모듈별 (`OpenClawState`, `InstallerState`, `SidecarState`).

**Rust ↔ Python 보안 평행:** Keyring · Audit 두 도메인은 Python sidecar의 동명 서비스와 같은 OS Keychain · 파일(`audit.jsonl`, `credentials_registry.json`) 공유. 신규 코드는 Rust 경로(`rustCredential*`, `rustAudit*`) 우선 사용, Python 측은 자체 라우터 안에서 자기 서비스 계속 사용.

### 2.3 Python Sidecar — `python-sidecar/`

FastAPI + uvicorn. Domain business logic (LLM 호출, messenger, document, security, audit) 전부 여기서. `127.0.0.1` only, Bearer token auth.

```
office_claw_sidecar/
├── main.py            # FastAPI app + lifespan (시작 시 whitelist/bot 자동 시작)
├── config.py          # 앱 데이터 디렉토리 경로
├── analyzer.py        # 코드/명령 정적 분석 (sandbox whitelist 검사)
├── sandbox.py         # Tool 실행 sandbox
├── command_audit.py   # Audit log (JSONL) — Rust audit.rs와 같은 파일 공유
├── chat_history.py    # Chat session 영속화
├── backup.py          # Backup/restore
├── routers/           # FastAPI endpoints (HTTP layer)
│   ├── health, credentials, llm, audit, telegram
│   ├── workspace, settings, maintenance
│   ├── agent, skills, security        # OpenClaw agent + security dashboard
│   ├── slack, discord, permissions    # multi messenger + 권한
│   ├── chat, backup                   # 영속화/backup
│   └── legacy                         # gmail/excel/document → 410 Gone
├── services/          # Domain service (재사용 가능 logic)
│   ├── keyring_service     # OS 보안 저장소 wrapper — Rust keyring_svc.rs와 같은 keychain 공유
│   ├── llm_service / claude_service / ollama_service
│   ├── openclaw_client     # OpenClaw Gateway HTTP client
│   ├── telegram_service / intent_router
│   ├── audit_service / masking_service / filter_service
│   ├── document_service / excel_service / gmail_service  # legacy 직접 처리 (deprecated)
│   └── tool_registry       # 권한 level (SAFE/CONFIRM/DENIED) + runtime whitelist
├── messenger/         # Messenger adapter (공통 ABC + Slack/Discord/Telegram)
├── models/            # Pydantic model (approval, audit, credential, llm, masking)
└── utils/             # platform 감지 등 helper
```

**Module boundary:** router는 HTTP I/O only, service는 domain logic, model은 데이터 형상. Router 간 직접 호출 금지 — service 경유.

### 2.4 외부 의존성

| 대상 | 통신 방식 | 용도 |
|---|---|---|
| **OpenClaw Gateway** | localhost HTTP/WS (spawn) | Claude 기반 agent runtime |
| **OpenClaw CLI** | subprocess stdin/stdout (`openclaw_cli.rs`) | WS gateway 미가용/실패 시 대안 |
| **Ollama** | localhost HTTP (`brew services start ollama`) | 로컬 LLM 추론 (OpenClaw 미실행 시 fallback) |
| **OS Keyring** | `keyring` lib (Python) / `keyring_svc.rs` (Rust) | macOS Keychain / Windows Credential Manager / Linux Secret Service. Bot token · API key 보관 |
| **Messengers** | 각 SDK (`python-telegram-bot`, `slack-bolt`, `discord.py`) | 사용자 메신저 명령 → sidecar → AI |
| **Office APIs** | `google-api-python-client` 외 | Gmail/Calendar (legacy, OpenClaw tool 호출로 이전 중) |

---

## 3. 핵심 데이터 흐름

### 3.1 Messenger → AI agent (main path)

```
[Messenger User] ── message ──▶ python-telegram-bot / slack-bolt / discord.py
                                    │
                            telegram_service · intent_router
                                    │
                            ▶ intent 분류 후 agent.chat (또는 tool 직접 실행)
                                    │
                            OpenClaw Gateway (HTTP/WS) 또는 Ollama 호출
                                    │
                            tool 호출 시 tool_registry 검사
                            → SAFE: 즉시 실행
                            → CONFIRM: messenger inline keyboard 또는 app UI dialog로 승인 대기
                            → DENIED: 거부
                                    │
                            결과 → messenger 회신 + command_audit.jsonl 기록
```

### 3.2 Desktop app IPC (설정 · 상태 · 관리)

```
[User in webview]
    │  React onClick
    ▼
src/lib/api.js  ─── invoke('rust_credential_set', ...) ───▶  Rust ipc::*
                                                                 │
                          ┌──────────────────────────────────────┼──────────────────┐
                          ▼                                      ▼                  ▼
                 Rust 평행 경로                          reqwest POST           즉시 처리
                 (keyring_svc / audit)                  sidecar /<route>       (tray 등)
                                                        (Bearer)
```

Desktop app은 LLM chat UI를 직접 호스팅하지 않는다. Bot token 입력, 권한 whitelist, audit log 조회, approval dialog, backup/update 등이 IPC의 주 용도.

### 3.3 Startup 부트 시퀀스

1. `Tauri Builder.setup` — `OpenClawState`, `InstallerState` 등 모듈 상태 등록
2. async spawn — `openclaw::spawn_openclaw` 자식 프로세스 + health poll (실패해도 비치명)
3. async spawn — `sidecar::spawn_sidecar` (PyInstaller 번들), 랜덤 포트 + Bearer token 발급
4. sidecar lifespan — temp 파일 정리 → whitelist 로드 → 저장된 bot token 있으면 messenger bot 자동 시작
5. `tray::setup_tray` — system tray 등록
6. webview mount — React 앱 → `statusManager`가 OpenClaw / Sidecar / Ollama status polling

---

## 4. 보안 모델

| 원칙 | 구현 |
|---|---|
| **Credential Isolation** | OS Keyring (`services/keyring_service.py`, `src-tauri/src/keyring_svc.rs`). Token/key 평문 저장 금지 |
| **No-Middleman** | 외부 API는 사용자 PC에서 직접 호출. 자체 서버 없음 |
| **Local-only Networking** | Sidecar / Gateway는 `127.0.0.1` only. 외부 인입 불가 |
| **Bearer Auth** | Tauri ↔ Sidecar 통신은 시작 시 발급된 임시 Bearer token 필요. `verify_auth` dependency로 모든 router 보호 |
| **Tool Sandbox** | `tool_registry` 등록된 tool만 실행 가능. SAFE/CONFIRM/DENIED 3-level 권한 |
| **Approval Flow** | CONFIRM 작업은 messenger(inline keyboard) 또는 app UI 승인 필요 — `routers/security.py`, `components/security/ApprovalDialog.jsx` |
| **PII Masking** | `masking_service`로 응답 전 민감정보 mask. 통계는 `security_stats`로 노출 |
| **Audit Log** | 모든 command/tool 호출 JSONL 기록 (`command_audit.py` + `audit.rs` 공유). UI는 `AuditLog.jsx` |

---

## 5. 새 도메인 추가 체크리스트

`CLAUDE.md`의 modular OOP 표에 한 줄 늘리는 작업.

1. **Rust 모듈 필요?** — 자식 프로세스 / OS 자원 / system tray 등 OS 권한 필요하면 `src-tauri/src/<domain>.rs` 추가. State는 `tauri::State<Mutex<...>>`. IPC는 `ipc.rs`에 thin wrapper로만.
2. **Sidecar 모듈** — Domain logic은 `services/<domain>_service.py`, HTTP I/O는 `routers/<domain>.py`, 데이터 형상은 `models/<domain>.py`. Router 간 직접 호출 금지.
3. **Frontend 모듈 세트** — `store/<domain>Store.js` (Zustand) + `lib/<domain>Manager.js` (액션) + 필요 시 `hooks/use<Domain>*.js`. UI 컴포넌트는 구독 · 조합만.
4. **IPC wrapper** — 모든 `invoke()`는 `src/lib/api.js`로. 컴포넌트 직접 호출 금지.
5. **Audit / 보안** — Tool 실행이면 `tool_registry`에 권한 level 등록, 호출 시 `audit_service`로 기록.
6. **Status 표시** — 외부 프로세스/서비스 의존이면 `statusManager`에 모듈 추가, `StatusBar` · `Dashboard`가 구독.

---

## 6. 참고 문서

- `CLAUDE.md` — Modular OOP 규칙, 한국어 컨벤션, CI mirror 명령
- `docs/OPENCLAW_USAGE.md` — OpenClaw Gateway 사용 가이드
- `docs/PYINSTALLER_BUILD_GUIDE.md` — Sidecar 패키징
- `docs/RUST_MIGRATION_PLAN.md` — Rust 보안 layer 평행 경로 진행 상황
