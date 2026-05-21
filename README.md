# Office Claw

민감 정보 보호 + 로컬 AI 업무 에이전트. 모든 작업은 사용자 PC에서 끝나고 중계서버를 두지 않는다. Desktop app + local FastAPI sidecar + local LLM 3-tier 아키텍쳐 구성.

사용자는 **메신저(Telegram / Slack / Discord)** 로 명령을 보낸다. Desktop app은 bot listener, tool 실행, approval UI, audit log를 담당 — 앱 안에서 LLM과 직접 채팅하는 화면은 없다.

상세 구조는 [`ARCHITECTURE.md`](./ARCHITECTURE.md) 참조.

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

## 2. 핵심 데이터 흐름

### 2.1 Messenger → AI agent 실행 (main path)

```
[Messenger User] ── message ──▶ python-telegram-bot / slack-bolt / discord.py
                                      │
                              telegram_service · intent_router
                                      │
                              ▶ intent 분류 후 agent.chat 호출 (또는 tool 직접 실행)
                                      │
                              OpenClaw Gateway 또는 Ollama 호출
                                      │
                              tool 호출 시 tool_registry 검사
                              → SAFE: 즉시 실행
                              → CONFIRM: messenger inline keyboard 또는 app UI dialog로 승인 대기
                              → DENIED: 거부
                                      │
                              결과 → messenger 회신 + command_audit.jsonl 기록
```

### 2.2 Startup 부트 시퀀스

1. `Tauri Builder.setup` — `OpenClawState`, `InstallerState` 등 모듈 상태 등록
2. async spawn — `openclaw::spawn_openclaw` 자식 프로세스 + health poll (실패해도 비치명)
3. async spawn — `sidecar::spawn_sidecar` (PyInstaller 번들), 랜덤 포트 + Bearer token 발급
4. sidecar lifespan — temp 파일 정리 → whitelist 로드 → 저장된 bot token 있으면 messenger bot 자동 시작
5. `tray::setup_tray` — system tray 등록
6. webview mount — React 앱 → `statusManager`가 OpenClaw / Sidecar / Ollama status polling

---

## 3. Build & Deploy

| 단계 | 도구 | 산출물 |
|---|---|---|
| Frontend bundle | Vite | `dist/` (Tauri webview load) |
| Python sidecar packaging | PyInstaller (`build_sidecar.py`, `office_claw_sidecar.spec`) | `src-tauri/binaries/office-claw-sidecar-<target-triple>` |
| Desktop app build | `cargo tauri build` | `.app` / `.dmg` / `.msi` / `.AppImage` |
| Auto-update | `tauri-plugin-updater` + `release.yml` | GitHub Releases 서명된 update manifest |
| CI (PR check) | `.github/workflows/pr-check.yml` | python-check(ruff+pytest) / frontend-check(npm ci+test) / rust-check(fmt+clippy) |
| Dev run | `npm run tauri:dev` | Vite dev server + cargo tauri dev (hot reload) |

빌드 상세: `docs/PYINSTALLER_BUILD_GUIDE.md`. OpenClaw 사용: `docs/OPENCLAW_USAGE.md`.

---

## 4. 디렉토리

```
office_claw/
├── src/                       # React frontend
├── src-tauri/                 # Tauri Rust backend
│   ├── src/                   # ipc / sidecar / openclaw / openclaw_cli / ollama / installer / keyring_svc / audit / tray
│   ├── binaries/              # 번들 sidecar binary (PyInstaller 산출물)
│   ├── capabilities/          # Tauri ACL manifest
│   └── tauri.conf.json
├── python-sidecar/            # FastAPI sidecar
│   ├── office_claw_sidecar/
│   ├── tests/
│   └── pyproject.toml
├── docs/                      # 추가 문서
├── scripts/                   # dev.sh, dev.ps1
├── .github/workflows/         # CI/CD
├── ARCHITECTURE.md            # 상세 아키텍처 (layer / security model) — 개발 시 참고
└── CLAUDE.md                  # 프로젝트 규칙 (modular OOP + 한국어 컨벤션)
```
