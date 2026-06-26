# Office Claw

개인정보 보호 중심의 로컬 AI 업무 에이전트. 모든 처리가 사용자 머신 안에서 끝나며, 외부 중계 서버가 없다.  
**Tauri 데스크탑 앱 + Python FastAPI 사이드카 + 로컬 LLM(Ollama/OpenClaw)** 3계층 구성.
앱 표시명은 **Team 503 AI**(구 ajou-ai)로 사용한다.

---

## 전체 구조

```
officeclaw/
├── src/                    # React 프론트엔드 (Vite + Tailwind)
├── src-tauri/              # Rust/Tauri 네이티브 레이어
├── python-sidecar/         # Python FastAPI 사이드카
├── scripts/                # 개발 보조 스크립트 (PowerShell)
├── docs/                   # Excel Live 명령 리스트 등 참고 자료
└── .github/workflows/      # CI (PR 검사 + 릴리스 빌드)
```

---

## 계층별 역할

### 1. Frontend — `src/`

React(Vite + Tailwind) 기반 Webview UI. URL 라우팅 없이 Zustand `currentPage` 상태로 화면 전환.

```
src/
├── main.jsx / App.jsx          # 앱 루트 (상태 폴링, 승인 다이얼로그)
├── store/
│   ├── appStore.js             # 전역 UI 상태 (currentPage, llmConfig, 온보딩 등)
│   └── statusStore.js          # 시스템 상태 (OpenClaw·Ollama·sidecar)
├── hooks/
│   ├── useStatusPoller.js      # 주기적 상태 갱신
│   └── useToast.js             # 토스트 상태·자동 닫힘 공용 훅
├── lib/
│   ├── api.js                  # 모든 Tauri invoke() 래퍼 (단일 진입점)
│   ├── statusManager.js        # 시스템 모듈 check/install/start 액션
│   ├── statusTokens.js         # 상태 표시 토큰·라벨
│   ├── localAISetupCore.js     # LocalAISetupWizard 단계·플랜 로직
│   ├── localAISetup.js         # localAISetupCore re-export (호환 레이어)
│   ├── localStack/             # 로컬 AI 스택 프리셋 (Qwen3+OpenClaw)
│   ├── errorMessages.js        # 에러 메시지 매핑
│   └── utils.js                # cn() 등 공용 유틸
└── components/
    ├── ui/                     # 공용 primitive (Button, Dialog, StatusDot 등)
    ├── layout/                 # Layout, Sidebar, StatusBar
    ├── dashboard/              # Dashboard
    ├── workspace/              # WorkspacePage (일반 채팅 + Excel Live 라우팅)
    ├── conversations/          # ConversationsPage
    ├── settings/               # SettingsHub, MessengerSettings, OllamaModelPicker
    ├── security/               # ApprovalDialog, SecurityDashboard
    ├── audit/                  # AuditLog
    ├── permissions/            # PermissionManager
    ├── credentials/            # CredentialsManager
    ├── onboarding/             # OnboardingWizard
    ├── guide/                  # LocalAISetupWizard, SetupGuide
    ├── updater/                # UpdateNotice
    └── cmdk/                   # CommandPalette, ShortcutHelp
```

**패턴:**
- 도메인 로직은 `src/lib/` 모듈이 소유, UI는 store 구독만
- 모든 Tauri IPC 호출은 `src/lib/api.js` 단일 파일 경유

---

### 2. Rust/Tauri — `src-tauri/src/`

프로세스 관리·보안·OS 통합 레이어.

```
src-tauri/src/
├── main.rs             # 진입점
├── lib.rs              # Tauri Builder + 상태 등록 + invoke 등록
├── ipc.rs              # 모든 #[tauri::command] — sidecar_request 헬퍼 기반 얇은 프록시
├── sidecar.rs          # Python 사이드카 spawn / health 폴링 / Bearer 토큰
├── openclaw.rs         # OpenClaw 게이트웨이 spawn
├── openclaw_cli.rs     # OpenClaw CLI 래퍼
├── ollama.rs           # Ollama 상태·모델 목록·config set
├── installer.rs        # 설치 명령 실시간 로그 스트리밍
├── shell.rs            # 로그인 셸 러너 (openclaw·ollama 공유, GUI PATH 우회)
├── tray.rs             # 시스템 트레이
├── keyring_svc.rs      # OS Keychain (Python keyring_service와 동일 저장소 공유)
└── audit.rs            # 감사 로그 (Python audit_service와 동일 파일 공유)
```

**흐름:** Frontend `invoke()` → `ipc.rs` → `sidecar.rs` HTTP 프록시 → Python 사이드카 (Bearer 토큰 인증)

---

### 3. Python FastAPI 사이드카 — `python-sidecar/`

실제 AI·데이터 처리 로직 담당. 빌드 시 PyInstaller로 단일 exe 생성 → `src-tauri/binaries/`에 배치.

```
python-sidecar/
├── pyproject.toml / uv.lock        # 의존성 (uv 권장)
├── requirements.txt                # pip 단일 진입점(루트 통합)
├── build_sidecar.py                # PyInstaller 빌드 스크립트
├── office_claw_sidecar.spec        # PyInstaller spec
│
├── office_claw_sidecar/
│   ├── main.py                     # FastAPI 앱 + 라우터 등록
│   ├── config.py                   # 플랫폼별 data dir
│   ├── analyzer.py                 # SAFE/CONFIRM/DENIED 명령 분석
│   ├── sandbox.py                  # 워크스페이스 샌드박스
│   ├── backup.py / chat_history.py / command_audit.py
│   │
│   ├── routers/                    # HTTP API 엔드포인트
│   │   ├── health.py               # GET /health
│   │   ├── agent.py                # POST /agent/chat (OpenClaw 경유 AI 대화)
│   │   ├── excel_live.py           # POST /excel-live/command|approval (COM 실시간 제어)
│   │   ├── llm.py                  # LLM 설정 조회/변경
│   │   ├── workspace.py            # 워크스페이스 파일 관리
│   │   ├── credentials.py          # OS Keyring CRUD
│   │   ├── audit.py                # 감사 로그 조회
│   │   ├── security.py             # 마스킹·차단 통계
│   │   ├── permissions.py          # 도구 권한 관리
│   │   ├── telegram.py / slack.py / discord.py  # 메신저 라우터
│   │   ├── chat.py                 # 채팅 세션 영속화
│   │   ├── skills.py               # OpenClaw 스킬
│   │   ├── backup.py               # 백업
│   │   └── settings.py / maintenance.py
│   │
│   ├── services/                   # 비즈니스 로직
│   │   ├── llm_service.py / ollama_service.py / claude_service.py
│   │   ├── openclaw_client.py      # OpenClaw WebSocket 클라이언트
│   │   ├── tool_registry.py        # 도구 권한 레지스트리 (SAFE/CONFIRM/DENIED)
│   │   ├── excel_live_service.py   # xlwings COM 제어
│   │   ├── excel_live_agent.py     # 자연어 → Excel 명령 파싱
│   │   ├── telegram_service.py     # 텔레그램 봇
│   │   ├── gmail_service.py        # Gmail OAuth (텔레그램 봇에서 사용)
│   │   ├── document_service.py     # 문서 생성 (텔레그램 봇에서 사용)
│   │   ├── keyring_service.py / audit_service.py
│   │   ├── filter_service.py / masking_service.py
│   │   └── intent_router.py
│   │
│   ├── models/                     # Pydantic 스키마
│   │   └── credential.py / approval.py / llm.py / masking.py
│   │
│   ├── messenger/                  # 메신저 어댑터
│   │   ├── base.py
│   │   ├── telegram.py / slack.py / discord_adapter.py
│   │
│   └── local_stack/
│       └── presets.py              # JS localStack과 동기화된 프리셋
│
└── tests/                          # pytest
    ├── test_health.py
    ├── test_local_ai_flow.py
    ├── test_local_stack_presets.py
    ├── test_sidecar_enhancements.py
    ├── test_sprint3.py ~ test_sprint5.py
    └── test_excel_live_{agent,router,service}.py
```

**등록된 라우터 prefix 목록:**

| prefix | 파일 | 용도 |
|--------|------|------|
| (루트) | health.py | `/health` 헬스체크 |
| `/llm` | llm.py | LLM 설정 |
| `/agent` | agent.py | AI 에이전트 채팅 |
| `/excel-live` | excel_live.py | Excel COM 실시간 제어 |
| `/workspace` | workspace.py | 파일 관리 |
| `/credentials` | credentials.py | OS Keyring |
| `/audit` | audit.py | 감사 로그 |
| `/security` | security.py | 마스킹·차단 |
| `/permissions` | permissions.py | 도구 권한 |
| `/telegram` `/slack` `/discord` | 메신저 | 메신저 연동 |
| `/chat` | chat.py | 채팅 세션 |
| `/skills` | skills.py | OpenClaw 스킬 |
| `/backup` | backup.py | 백업 |
| `/settings` `/maintenance` | 설정·유지보수 |

---

### 4. 스크립트 — `scripts/`

| 파일 | 용도 |
|------|------|
| `dev.ps1` | Windows 개발 시작 (사이드카 백그라운드 + tauri dev) |
| `dev.sh` | macOS/Linux 개발 시작 |
| `local-env.ps1` | OpenClaw 토큰 환경변수 로드 |
| `start-local-stack.ps1` | OpenClaw 게이트웨이 + 사이드카 기동 |
| `verify-local-stack.ps1` | 로컬 스택 헬스체크 |

---

### 5. 문서

프로젝트 문서는 다음 3개로 통합 관리한다. (이전의 `docs/` 세부 설계·설치·OpenClaw 사용법 문서는 본 README로 흡수)

| 파일 | 내용 |
|------|------|
| `README.md` | 프로젝트 개요·구조·빠른 시작 (본 문서) |
| `CLAUDE.md` | 개발 규칙·CI 사전 체크·코드 컨벤션 |
| `개발일지.md` | 개발 로그 (append-only) |

`docs/`에는 Excel Live 명령 참고용 `excel-live-command-list.txt`만 남아 있다.

---

## 빠른 시작

### 비개발자 사용 (권장: Windows EXE 배포본)

비개발자에게는 소스코드 실행보다 **릴리스 EXE 배포**가 가장 안전하고 쉽다.

1. GitHub Releases에서 최신 Windows 패키지(`Team 503 AI`) 다운로드
2. 압축 해제 후 `Team 503 AI.exe` 실행
3. 첫 실행 시 `LocalAISetupWizard`에서 의존성/Ollama/OpenClaw 자동 점검
4. 워크스페이스에서 Excel 파일 열고 채팅으로 바로 작업

운영 권장 구성:
- 배포 채널: GitHub Releases (버전 태그별 아카이브)
- 실행 파일: Tauri 앱 EXE + 번들된 Python sidecar 바이너리
- 사용자 관점: 설치/실행 후 Wizard만 통과하면 개발 도구 없이 사용 가능

### 개발 환경 (Windows)

```powershell
# 1. 의존성 설치
npm ci
cd python-sidecar && uv sync --extra dev && cd ..

# (대안) pip 환경이면 루트에서 단일 파일 설치
pip install -r requirements.txt

# 2. 사이드카 빌드 (최초 1회, 코드 변경 시 재실행)
cd python-sidecar && uv run --extra dev python build_sidecar.py && cd ..

# 3. 앱 실행 (Rust + Vite + Tauri Webview)
npm run tauri:dev
```

> **UI만 빠르게 보려면:** `npm run dev` (Tauri 없이 Vite 단독 실행, invoke() 호출 실패함)

### 지금 바로 테스트해볼 질문 10개

> **빠른 스모크 테스트용**  
> 아래 문장을 앱 내 에이전트 채팅에 그대로 넣어 동작을 확인하세요.
>
> 1) [하] `열린 통합문서 목록 보여줘`  
>    예상: 열린 파일 개수와 파일명이 채팅에 표시됨
> 2) [하] `A1:C10 조회해줘`  
>    예상: 읽은 범위 주소와 행/열 개수가 표시됨
> 3) [하] `C3에 120 입력해줘`  
>    예상: 승인 후 `C3` 셀이 `120`으로 변경됨
> 4) [하] `B9 값만 읽어줘`  
>    예상: `B9` 단일 셀 값이 읽혀 채팅에 표시됨
> 5) [중] `B2:D2에 이름,수량,금액 입력`  
>    예상: 승인 후 `B2:D2`에 3개 값이 한 번에 입력됨
> 6) [중] `H8 999 set`  
>    예상: 영어 compact 명령이 파싱되어 `H8=999`로 반영됨
> 7) [중] `A열에서 50 이상인 셀만 노란색 배경 적용`  
>    예상: 조건에 맞는 셀만 노란색으로 강조되고 변경 개수가 표시됨
> 8) [중] `D:D 컬럼에서 0 이하 숫자는 파란색 표시`  
>    예상: 0 이하 값만 파란색으로 강조됨
> 9) [상] `J1에 수식 =SUM(A1:A10) 적용`  
>    예상: 승인 후 `J1`에 SUM 수식이 설정됨
> 10) [상] `K2:K20에 formula =IF(A2>0,"Y","N") set`  
>    예상: 범위 전체에 IF 수식이 적용됨

### LocalAISetupWizard (Windows 자동 설치 흐름)

- Ollama가 없으면 Wizard가 `winget`으로 자동 설치를 시도
- 설치 후 Ollama 프로세스를 자동 실행
- 선택 모델(`qwen3:4b`, `qwen3:8b`)을 자동 pull
- AI 대화 테스트에서 OpenClaw 게이트웨이 503 발생 시 자동 재기동 후 재시도
- `npm run tauri:dev` 환경에서 sidecar가 꺼져 있으면 dev 포트(`19532`)에 자동 기동 후 재시도

---

## Excel Live 질문 예시

아래 예시는 실제 파서/테스트에 반영된 문장들이다.

- `A1:C10 조회해줘`
- `C3에 120 입력해줘`
- `B2:D2에 이름,수량,금액 입력`
- `A열에서 50 이상인 셀만 노란색 배경 적용`
- `B2:D5 범위에 경계선 적용해줘`
- `선택한 범위에 테두리 넣어줘`
- `B10에 테두리 넣어줘`
- `J1에 수식 =SUM(A1:A10) 적용`
- `H8 999 set`

> 경계선 기본값은 가시성을 위해 **검정 medium 보더**로 적용된다.  
> 더 얇게 원하면 `얇게 경계선 적용`처럼 지시하면 된다.

### 범위 참조 삽입(워크스페이스 채팅)

- 채팅 패널의 `범위 참조 삽입` 버튼을 누르면 현재 Excel 선택 범위가 입력창에 삽입된다.
- 삽입 형식: `[[EXCEL_RANGE:A1:C3]]` + TSV 미리보기 블록
- 이후 `이 범위`, `해당 범위`, `복사한 범위`처럼 말해도 선택 범위를 기준으로 처리된다.
- 명령 해석 실패 시에는 `열린 통합문서 목록`으로 조용히 폴백하지 않고, 명확한 오류(HTTP 400)로 반환한다.

전체 67개 검증 입력 세트는 루트 파일에서 바로 확인 가능:

- `TEST_INPUT_COMMANDS_EXCEL_LIVE.txt`

### CI 사전 체크 (PR 전 필수)

```powershell
# Rust
cd src-tauri && cargo fmt --check && cargo clippy --all-targets -- -D warnings

# Python
cd python-sidecar && uvx ruff check . && uv run pytest -q

# Frontend
npm run test:unit --if-present
```

---

## 핵심 설계 원칙

1. **상태는 모듈이 소유** — 도메인 로직·상태는 `lib/` 모듈 안에, UI는 store 구독만
2. **표시와 데이터 분리** — `src/lib/*.js` (데이터) → `src/components/ui/*.jsx` (primitive) → 도메인 UI (조합)
3. **중복 fetch 없음** — 같은 데이터는 중앙 store/manager 1곳에서 관리
4. **IPC 단일 진입점** — 모든 Tauri invoke는 `src/lib/api.js`를 경유
5. **보안 계층** — Keyring·Audit은 Rust 경로(`keyring_svc.rs`, `audit.rs`) 우선 사용
