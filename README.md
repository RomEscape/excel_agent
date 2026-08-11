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
├── docs/                   # 설계·설치 문서
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
│   └── useAsync.js
├── lib/
│   ├── api.js                  # 모든 Tauri invoke() 래퍼 (단일 진입점)
│   ├── statusManager.js        # 시스템 모듈 check/install/start 액션
│   ├── statusTokens.js         # 상태 표시 토큰·라벨
│   ├── localAISetupCore.js     # LocalAISetupWizard 단계·플랜 로직
│   ├── localAISetup.js         # localAISetupCore re-export (호환 레이어)
│   ├── localStack/             # 로컬 AI 스택 프리셋 (Qwen3+OpenClaw)
│   ├── updater.js              # Tauri 자동 업데이트 연동
│   ├── errorMessages.js        # 에러 메시지 매핑
│   └── utils.js                # cn() 등 공용 유틸
└── components/
    ├── ui/                     # 공용 primitive (Button, Dialog, StatusDot 등 15개)
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
    ├── cmdk/                   # CommandPalette, ShortcutHelp
    └── email/ excel/ document/ telegram/  # 도메인 모듈 (UI only)
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
├── ipc.rs              # 모든 #[tauri::command] — sidecar HTTP 프록시
├── sidecar.rs          # Python 사이드카 spawn / health 폴링 / Bearer 토큰
├── openclaw.rs         # OpenClaw 게이트웨이 spawn
├── openclaw_cli.rs     # OpenClaw CLI 래퍼
├── ollama.rs           # Ollama 상태·모델 목록·config set
├── installer.rs        # 설치 명령 실시간 로그 스트리밍
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
│   │   ├── settings.py / maintenance.py
│   │   └── legacy.py               # /gmail·/excel·/document → 410 Gone 안내
│   │
│   ├── services/                   # 비즈니스 로직
│   │   ├── llm_service.py / ollama_service.py / claude_service.py
│   │   ├── openclaw_client.py      # OpenClaw WebSocket 클라이언트
│   │   ├── tool_registry.py        # 도구 권한 레지스트리 (SAFE/CONFIRM/DENIED)
│   │   ├── excel_live_service.py   # Excel 엔진 선택기(file/xlwings)
│   │   ├── excel_live_file_service.py # 기본 엔진(openpyxl, 파일 기반 편집)
│   │   ├── excel_live_executor.py  # Planner 단계 실행/검증/재시도 오케스트레이션
│   │   ├── excel_live_plan_validator.py / excel_live_plan_critic.py
│   │   ├── excel_live_agent.py     # 자연어 → Excel 명령 파싱(quick-action + LLM)
│   │   ├── telegram_service.py     # 텔레그램 봇
│   │   ├── gmail_service.py        # Gmail OAuth (텔레그램 봇에서 사용)
│   │   ├── excel_service.py        # openpyxl 파일 분석 (텔레그램 봇에서 사용)
│   │   ├── document_service.py     # 문서 생성 (텔레그램 봇에서 사용)
│   │   ├── keyring_service.py / audit_service.py
│   │   ├── filter_service.py / masking_service.py
│   │   └── intent_router.py
│   │
│   ├── models/                     # Pydantic 스키마
│   │   └── credential.py / audit.py / approval.py / llm.py / masking.py
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
| `/gmail` `/excel` `/document` | legacy.py | 410 Gone (이전 API) |

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

### 5. 문서 — `docs/`

| 파일 | 내용 |
|------|------|
| `ARCHITECTURE.md` | 계층·IPC·보안 상세 설계 |
| `DEPENDENCIES.md` | 전체 의존성 설치 표 |
| `WINDOWS_DESKTOP_SETUP.md` | Windows 설치·실행 가이드 |
| `OPENCLAW_USAGE.md` | OpenClaw 사용법 |
| `OPENCLAW_CLI_WRAPPER.md` | CLI 래퍼 문서 |
| `PYINSTALLER_BUILD_GUIDE.md` | 사이드카 빌드 가이드 |
| `RUST_MIGRATION_PLAN.md` | Keyring/Audit Rust 이전 계획 |
| `EXCEL_LIVE_AGENT_MVP.md` | Excel Live 에이전트 MVP 명세 |
| `EXCEL_LIVE_ROUTER_DATASET_SCHEMA.md` | 라우터 의도/슬롯 데이터셋 스키마 |
| `EXCEL_LIVE_ROUTER_DATASET_SAMPLE.json` | 멀티턴 평가용 샘플 데이터셋 |
| `EXCEL_COMPLEX_ORACLE_SCHEMA.md` | 복잡 작업 오라클(대화/실행/결과) 스키마 |
| `local-stack/GEMMA4_OPENCLAW.md` | Gemma4 로컬 스택 상세 |

---

## 포함 기술 스택 (현재 운영)

- 데스크톱: `Tauri v2` + `Rust`
- 프론트: `React` + `Vite` + `Zustand` + `Tailwind`
- 사이드카 API: `FastAPI` + `Pydantic` + `Uvicorn`
- Excel 엔진:
  - 기본(`EXCEL_LIVE_ENGINE=auto`): 실행 중인 Excel에 열린 문서가 있으면 `xlwings`, 없으면 `file`.
    열려 있는 파일은 OS가 잠그기 때문에 `file` 엔진으로는 저장 자체가 불가능하다.
  - `EXCEL_LIVE_ENGINE=file`: `openpyxl` 파일 직접 편집. Excel이 설치돼 있지 않아도 동작한다.
  - `EXCEL_LIVE_ENGINE=xlwings`: Windows COM / macOS appscript로 실행 중인 Excel 제어
- LLM/에이전트:
  - 로컬 추론: `Ollama`
  - 게이트웨이: `OpenClaw`
  - 플래너: quick-action 규칙 + LLM 파서 하이브리드
  - 실행 안정화: Planner/Executor/Validator/Critic + 승인 게이트
- 품질/학습 루프:
  - 통합 로그: `logs/all_events.jsonl`
  - 하네스: 사용자 피드백 수집 + 실패 리플레이
  - distillation: `excel_distill.v1` 포맷 + A.X 7B QLoRA 파이프라인

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
# 1. 원클릭 준비 (툴 자동 설치 시도 + 의존성 설치 + 기본 빌드)
.\scripts\setup.ps1

# (선택) 자동 툴 설치 끄기
.\scripts\setup.ps1 -NoAutoInstallTools

# (선택) 빌드 단계 건너뛰기(의존성만 설치)
.\scripts\setup.ps1 -SkipBuild

# 2. 앱 실행 (Rust + Vite + Tauri Webview)
npm run tauri:dev
```

### 개발 환경 (macOS / Linux)

```bash
# 1. 원클릭 준비 (툴 자동 설치 시도 + 의존성 설치 + 기본 빌드)
bash ./scripts/setup.sh

# (선택) 자동 툴 설치 끄기
bash ./scripts/setup.sh --no-auto-install-tools

# (선택) 빌드 단계 건너뛰기(의존성만 설치)
bash ./scripts/setup.sh --skip-build

# (선택) sidecar 빌드 제외
bash ./scripts/setup.sh --no-build-sidecar

# 2. 앱 실행
npm run tauri:dev
```

> Excel Live 실편집은 **macOS + Microsoft Excel Desktop**에서 동작한다.  
> Linux는 Excel Desktop 자동화 런타임이 없어 Excel Live 실시간 편집 대상이 아니다.

> `requirements.txt`는 pip 전용 파일이라 npm/cargo를 담을 수 없다.  
> 전체 준비는 위 통합 스크립트를 사용하면 된다.  
> `setup` 스크립트는 `node`/`cargo`/`python3`(플랫폼별) 미설치 시 OS 패키지 매니저(Windows: winget, macOS: brew/rustup, Linux: apt 가능 시)로 자동 설치를 시도한다.
> 또한 `OPENCLAW_HOME`, `CARGO_HOME`, `NPM_CONFIG_PREFIX`를 사용자 홈 기준으로 자동 고정/생성해 디렉토리 경로 이슈를 줄인다.
> Windows에서는 `link.exe`가 없으면 `Microsoft.VisualStudio.2022.BuildTools`(C++ workload + SDK) 자동 설치를 시도한다.

### 자주 나는 오류 자동 완화

- `Port 1420 is already in use`
  - `npm run dev` 전에 `scripts/ensure-dev-port.mjs`가 1420 점유 프로세스를 자동 정리한다.
- `link.exe not found`
  - `.\scripts\setup.ps1`가 MSVC Build Tools를 자동 설치 시도하고, `link.exe` 경로를 PATH에 주입한다.

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
- 선택 모델(`skt/A.X-4.0-Light:latest`, `qwen3:8b`)을 자동 pull
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
- `매출 높은 순으로 정렬해줘`
- `완료된 것만 따로 보고 싶어`
- `중복된 거 지워줘`
- `피벗 같은 걸로 월별 매출 정리해줘`
- `월별 매출 그래프로 만들어줘`
- `이상한 값 있는지 점검해줘`
- `D2:D50 수식 결과 값 확인해줘`

> 경계선 기본값은 가시성을 위해 **검정 medium 보더**로 적용된다.  
> 더 얇게 원하면 `얇게 경계선 적용`처럼 지시하면 된다.

### 범위 참조 삽입(워크스페이스 채팅)

- 채팅 패널의 `범위 참조 삽입` 버튼을 누르면 현재 Excel 선택 범위가 입력창에 삽입된다.
- 삽입 형식: `[[EXCEL_RANGE:A1:C3]]` + TSV 미리보기 블록
- 이후 `이 범위`, `해당 범위`, `복사한 범위`처럼 말해도 선택 범위를 기준으로 처리된다.
- 명령 해석 실패 시에는 `열린 통합문서 목록`으로 조용히 폴백하지 않는다.
- 엑셀 작업 의도가 감지되는 러프 문장은 우선 follow-up 질문으로 전환해 작업 조건을 수집한다.
- 실행 안정화 레이어:
  - `/excel-live/command`는 Planner/Executor 경로(`excel_live_executor.py`)로 단계 실행/검증/재시도를 고정한다.
  - LLM이 `5*5 표 만들어줘`를 `write_range`(불완전 파라미터)로 잘못 만들면 서버가 `create_table(rows=5, cols=5)`로 자동 보정한다.
  - `여기에 테두리`처럼 모호 지시어는 `context_range`를 우선 사용하고, 마지막 성공 범위를 workbook 단위로 기억해 후속 턴에서 재사용한다.
  - `정렬/필터/중복/피벗/차트/검증`은 세션 기반 멀티턴 슬롯필링으로 동작한다.
  - `수식`도 세션 기반 멀티턴 슬롯필링으로 동작한다(`multiply`, `tax`, `gap`, `countif`, `if_compare`, `vlookup`).
  - 편집 액션은 실행 전 복구용 사본을 자동 생성한다(가능 시 `officeclaw_backups/`).
  - 실행/검증 실패 시 가능한 범위에서 자동 롤백을 시도하고, 응답에 `auto_rollbacks`/`recovery_backup` 정보를 포함한다.
  - 사후조건 검증(`services/excel_result_verifier.py`)은 실행기의 성공 보고를 믿지 않고 워크북을 다시 읽는다.
    `write_range`는 값이 실제로 그 셀에 들어갔는지, `clear_range`는 범위가 실제로 비었는지 대조한다.
    (숫자 타입 변화·날짜 표현 차이·수식 셀은 오탐을 막으려고 비교에서 제외한다.)
  - 재계획 시 `failed_action`/`failed_args`/`failed_error`를 프롬프트에 붙여, 같은 인자로 같은 실패를 반복하지 않게 한다.
    - 예: `정렬해줘` → `어떤 열 기준으로 정렬할까요?` → `매출 열 기준 높은 순`
    - 예: `그래프로 만들어줘` → `선/막대/원형 중 어떤 차트?` → `선 그래프`
    - 예: `중복 지워줘` → `어떤 기준으로 중복 판단?` → `전화번호 기준`
    - 예: `완료 건수 세어줘` → `어떤 열 기준으로 셀까요?` → `B열 상태에서 완료 개수`

### 플래너 에스컬레이션 사다리 (2026-08-10)

`/excel-live/command`의 계획 수립은 **모델 한 대에 걸지 않는다.** 로컬 7B가 못 푼 요청을
사용자에게 되묻는 대신 위 단계로 올린다. 소유 모듈은 `services/excel_planner_escalation.py`다.

| 단계 | 이름 | 무엇을 하나 | 언제 넘어가나 |
|---|---|---|---|
| 0 | 규칙 | `parse_command_rule_based` · `_build_quick_action_plan` — LLM 없이 결정적 처리 | 확신 있는 매칭이 없을 때 |
| 1 | 로컬 플래너 | 파인튜닝된 A.X 7B (`ax7bplanner-*`) | 계획이 **실행 직전 검증**을 통과하지 못할 때 |
| 2 | 자가 수정 | 검증기가 낸 오류 문구를 프롬프트에 붙여 로컬에 1회 재시도 | 여전히 검증 실패 |
| 3 | 강한 모델 | Claude 등으로 승격 (`get_strong_llm_service`) | 여전히 검증 실패 |
| 4 | 되묻기 | 그때서야 사용자에게 질문 | — |

설계상 중요한 점 세 가지:

- **검증 실패도 승격 사유다.** JSON 파싱만 보면 "그럴듯하지만 실행 못 하는 계획"이 통과한다.
  바인딩·검증까지 통과해야 성공으로 친다.
- **활성 프로바이더를 갈아끼우지 않는다.** 3단계는 이 호출에서만 다른 서비스를 쓴다.
  싱글턴을 바꾸면 그 사이 다른 요청까지 클라우드로 새어 나간다.
- **키가 없으면 3단계를 조용히 건너뛴다.** 오프라인·로컬 전용 사용자가 막히면 안 된다.
  `OFFICECLAW_DISABLE_STRONG_PLANNER=1`로 강제로 끌 수 있고, `OFFICECLAW_STRONG_MODEL`로 모델을 지정한다.

이미 규칙 계획이나 슬롯 의도가 잡혀 있으면 2·3단계를 건너뛴다 — 어차피 폴백이 답할
요청에 LLM을 더 태우면 지연만 늘어난다.

#### 실패가 다음 학습 데이터가 된다

승격·최종 실패는 전부 `logs/planner_escalations.jsonl`에 적재된다.
로컬이 틀리고 상위 단계가 맞힌 순간이 가장 값진 증류 샘플이다.

```bash
# 큐 → 학습 후보 + 사람이 볼 미해결 목록
python scripts/build_sft_from_escalations.py \
    --output ../datasets/distill/excel_escalation_harvest_v1.jsonl \
    --unsolved-output ../logs/planner_unsolved.jsonl
```

되묻기로 끝난 턴은 정답으로 수확하지 않는다 — 그걸 학습하면 "어려우면 물어봐라"를
강화하게 되는데 원하는 건 그 반대다.

### 턴 트레이스 — 실패 원인 추적 (2026-08-11)

`/excel-live/command` 한 턴이 어디서 깨졌는지 가르는 로그다. 요청·관측·계획·실행·검증이
`logs/chat_log.jsonl`에 **턴당 JSON 한 줄**로 모인다. 소유 모듈은 `services/decision_trace.py`,
읽기는 `services/trace_report.py`다.

```powershell
python scripts/show_turns.py            # 최근 5턴을 사람이 읽는 형태로
python scripts/show_turns.py --failed   # 실패한 턴만
python scripts/show_turns.py --summary  # 실패 유형 집계
python scripts/show_turns.py --prompt   # LLM에 준 프롬프트와 원본 응답까지
python scripts/show_turns.py --human    # 사람이 친 명령만 (테스트 제외)
```

테스트가 만든 턴도 누적할 수 있다. 기본값은 임시 디렉터리이고(실행마다 660여 턴이
실제 로그에 쌓이면 사람이 읽어야 할 기록이 묻힌다), 들여다볼 때만 켠다.

```powershell
$env:OFFICE_CLAW_TRACE_TESTS = "1"; uv run pytest -q
python scripts/show_turns.py --log ../logs/test-runs/chat_log.jsonl --failed
```

```
[USER]        C3에 120 입력해줘
[OBSERVATION] sheet=매출 used_range=A1:C3   headers=월, 지역, 금액
[ROUTE]       quick_rule:miss → planner:local → verify:failed×2 → replan:1 → final:failed
[PLAN]        excel_live.write_range {"start_cell": "C3", "values_2d": [[120]]}
[EXECUTION]   excel_live.write_range → ok
              [VERIFY] 실패 — write_value_mismatch:C3 셀에 120를 쓰려 했으나 777가 들어 있습니다
[FINAL]       검증 실패 · 재계획도 실패
```

- `routes`는 이 턴이 지나간 갈림길이다. 규칙으로 처리됐는지 플래너를 탔는지, 몇 번째 티어까지 올라갔는지, 재계획했는지가 한 줄로 보인다.
- 결론(`final:ok` / `final:failed` / `final:asked_back` / `final:approval_required`)은 라우터가 어디서 반환하든 반드시 붙는다.
- 실행 오류·플래너 파싱 실패·검증 실패·재계획 누락은 자동 분류한다. **인자 오류는 자동 분류하지 않는다** — 사용자 의도를 알아야 하므로 OBSERVATION과 PLAN을 나란히 보고 사람이 판정한다.
- `--prompt`는 프롬프트 전체가 아니라 **그 턴에 모델이 실제로 본 통합문서 상태**를 보여 준다. 앞 4천여 자는 매 턴 똑같은 액션 목록이라, 통째로 넣으면 정작 필요한 시트 정보가 길이 제한에 잘린다. 모델이 없는 열 이름을 지어냈을 때 "안 보여 줬다"와 "보여 줬는데 무시했다"를 가르는 데 쓴다.

### 명령 진단 배터리 — 반복해서 원인을 가른다 (2026-08-11)

턴 트레이스가 **한 턴**을 펼친다면, 이쪽은 같은 명령을 **여러 번** 태워 놓고 접는다.
한 번 돌려 나온 실패를 코드에서 찾기 시작하면, 실제 원인이 모델의 변덕일 때 끝없이
헤맨다. 그래서 집계가 결정적 결함과 비결정적 결함을 먼저 가른다.

```powershell
python scripts/run_command_diagnostics.py               # 12케이스 × 3회
python scripts/run_command_diagnostics.py -n 5          # 5회 반복
python scripts/run_command_diagnostics.py --case 차트    # 일부만
python scripts/run_command_diagnostics.py --analyze-all # 쌓인 실행 전부 합쳐 분석
```

실행마다 `logs/diagnostics/<실행id>.jsonl`에 턴을 남기고 같은 이름의 `.report.json`에
집계를 남긴다. **덮어쓰지 않으므로 이력이 쌓인다.** 개별 턴은
`show_turns.py --log logs/diagnostics/<실행id>.jsonl`로 펼친다. 소유 모듈은
`tests/excel_e2e/command_battery.py`(실행)와 `services/trace_digest.py`(집계)다.

케이스는 다섯 부류로 갈린다.

| 상태 | 뜻 | 어디를 봐야 하나 |
|---|---|---|
| 성공이라 했지만 요청한 일을 안 함 | 시스템은 성공 판정, 요청한 액션은 미실행 | 플래너 (가장 위험) |
| 들쭉날쭉 | 같은 입력에 판정이 갈림 | 모델의 변덕 — 코드부터 뒤지면 안 됨 |
| 항상 깨짐 | 매번 같게 실패 | 코드. 재현해서 고치면 된다 |
| 항상 되물음/승인대기 | 파일은 그대로 | 되물음이 타당한지 사람이 판정 |
| 항상 됨 | — | — |

되물음과 승인 대기를 성공에 세지 않는 것이 중요하다. 에러는 아니지만 파일은 그대로라,
성공으로 세면 이행률이 부풀려진다.

**113턴(3라운드) 실측**: 깨진 턴 0건, 들쭉날쭉 0건 — 파이프라인은 결정적이다.
대신 `차트`가 3/3으로 `silent_wrong`에 걸린다. "막대 차트 만들어줘"에 `pivot_table`만
실행하고 `[VERIFY] 통과 · [FINAL] 성공`으로 끝낸다. 검증기는 **실행한 액션**의
사후조건만 보므로 이 실패를 구조적으로 볼 수 없다.

`silent_wrong` 판정 기준(`expect_action`)은 턴이 `source.expect`로 직접 들고 다닌다.
쌓인 로그를 나중에 다시 읽어도 같은 판정이 재현된다. 다만 **액션 이름만** 본다 —
인자가 깨진 경우(예: 머리글에 문장 절반이 들어감)는 아직 못 잡는다.

### 검증기 변이 수트 (2026-08-11)

검증기가 **잘못된 최종 상태를 잡아내는지** 재는 벤치마크다. 계획도 인자도 맞고
실행기도 성공을 보고하는데 파일만 틀린 상황을 만들어, 검증기가 이를 통과시키는
비율(false pass)과 멀쩡한 작업을 막는 비율(false fail)을 같이 본다.

```powershell
python scripts/run_verifier_suite.py          # V0·V1·V2 전부 + logs/에 저장
python scripts/run_verifier_suite.py --diff   # 단계 간 변화만
```

| 단계 | 내용 | false pass | false fail |
|---|---|---|---|
| V0 | 검증 강화 이전 | 12/12 (100%) | 0/2 (0%) |
| V1 | + `write_range` 상태 검증 | 6/12 (50%) | 0/2 (0%) |
| V2 | + `clear_range` 상태 검증 | 1/12 (8%) | 0/2 (0%) |

- 변이는 `write_range` 7종(wrong_value·missing_cell·partial_write·shifted_range·extra_write·wrong_shape·narrow_address), `clear_range` 5종(no_clear·partial_clear·wrong_range_clear·value_remains·formula_remains).
- **false fail을 같이 보는 이유**: 검증기가 항상 실패를 반환하면 false pass는 0%가 되지만 멀쩡한 작업까지 롤백되어 에이전트가 망가진다.
- 결과는 `logs/verifier_baseline.json`·`verifier_after_write_range.json`·`verifier_after_clear_range.json`에 케이스별(요청·기대 상태·실제 상태·검증 판정·정답 판정·분류)로 보존된다.
- 아직 못 잡는 변이는 `extra_write` 하나 — 요청 범위 밖 부수 피해는 실행 전 전체 스냅샷이 있어야 보인다. `tests/test_verifier_mutants.py`의 `KNOWN_BLIND_SPOTS`가 이 목록을 고정한다.

액션 전반의 넓이는 `scripts/run_verifier_gap.py`가 따로 본다(정렬·필터·차트 포함
10종). 검증기를 손대면 둘 다 돌린다.

### 승인은 단계가 아니라 계획 단위 (2026-08-11)

같은 계획을 두 경로로 태워 결과를 나란히 놓는다. **direct**(`approve:true` 단일
호출 — 실행 루프가 전부 도는 대조군)와 **gated**(승인 요청 → `/excel-live/approval`
— 프론트가 실제로 타는 경로)다.

```powershell
python scripts/run_approval_gate.py --save after-plan-approval
python scripts/run_approval_gate.py --diff baseline after-plan-approval
```

| 지표 | 수정 전 | 수정 후 |
|---|---|---|
| 계획 이행률 | 50.0% (10단계 중 5단계 소실) | **100%** |
| 승인 경로 파일 정합 | 3/5 | **5/5** |
| 롤백 소실 | 1/1 | **0/1** |

- 한때 실행 루프는 계획을 먼저 훑어 **첫 CONFIRM 단계에서 반환**했다. 그 하나만
  `_pending_approvals`에 담기고 나머지는 사라졌다. `post_approval`이
  `_execute_action`을 직접 불러 검증도 롤백도 재계획도 지나가지 않았다.
- 지금은 계획 확정 시점의 컨텍스트(`PlanExecution`)를 통째로 보관했다가, 승인되면
  `_execute_plan_and_respond()`로 이어 붙인다. `/command`와 `/approval`이 **같은
  실행 루프**를 탄다.
- 재개할 때 **플래너를 다시 부르지 않는다.** 승인 후 재계획하면 사용자가 승인한
  것과 다른 계획이 실행될 수 있다.
- 승인 다이얼로그는 실행할 단계를 전부 나열한다. 첫 단계만 보여주고 계획 전체를
  승인받는 것은 승인이 아니다.
- 손실은 종류가 다르다 — **data**(값이 빈다) · **formatting**(값은 맞고 서식만
  사라진다) · **verification**(파일도 응답도 정상으로 보인다). 뒤로 갈수록 위험해서
  세 가지를 따로 센다.
- `real_create_table_flow` 케이스는 계획을 스텁하지 않는다. 라우터가 스스로
  `[create_table, write_range]`를 만드는, 실제 사용자 경로 그대로다.

승격 게이트는 액션 이름만 채점하고, 검증기 변이 수트는 계획을 실행기에 직접
주입해 승인 게이트를 건너뛴다. 이 측정이 그 사각지대를 덮는다.

### 플래너 응답 JSON 파싱 (2026-08-11)

LLM이 돌려준 텍스트에서 계획 JSON을 꺼내는 일은 **두 겹**으로 막는다.

- **예방** — 플래너·매크로 분해 호출에는 `json_only=True`를 붙인다. Ollama의
  `response_format={"type":"json_object"}`로 디코딩 자체가 JSON 문법에 묶인다.
  Claude는 대응 옵션이 없어 무시한다. 그래서 이건 요청이지 보장이 아니다.
- **방어** — `services/llm_json.py`가 중괄호 균형을 세어 최상위 오브젝트를 꺼낸다.
  문자열 안의 중괄호는 세지 않고, 사고 블록(`<think>`)이 있으면 마지막 것 뒤만 남긴다.

예전에는 `re.search(r"\{.*\}", raw, re.DOTALL)` 하나였다. 첫 `{`부터 **마지막** `}`
까지를 통째로 집는 탐욕 매칭이라, 오브젝트가 둘 이상이거나 JSON 뒤에 중괄호를 포함한
문장이 오면 깨진다. 기본 플래너는 맨 JSON만 뱉어 걸리지 않았지만, 설정에서 모델을
바꿀 수 있는 이상 그 전제에 기대면 안 된다.

`json_only`를 켠 것이 계획을 바꾸지는 않는지 같은 모델로 A/B 한다.

```powershell
cd python-sidecar
uv run python scripts/ab_json_only.py --limit 40   # logs/ab_json_only.json
uv run python scripts/probe_json_format.py         # 서버가 response_format을 받는지
```

`ax7bplanner-v5r` 40건 기준 켬/끔 모두 정확도 32/40, 계획이 갈린 케이스 0건이었다.

### Excel 호출은 전담 스레드 하나에서만 (2026-08-11)

xlwings COM 호출은 `max_workers=1` executor 하나로만 나간다. `asyncio.to_thread`가
아닌 이유는 COM 객체가 만들어진 스레드에 묶이기 때문이다 — 호출마다 다른 워커에
떨어지면 새 문제가 생긴다. 스레드가 하나라 직렬화도 저절로 되므로 예전의 큐 락은
제거했다.

- `async` 핸들러는 `_run_in_excel_queue_async`로 **await** 한다. 동기로 부르면 COM이
  도는 내내 이벤트 루프가 붙잡혀 `/health` 폴링이 막히고, UI는 사이드카가 죽은 것으로
  본다. 고치기 전 측정에서 3.2초짜리 명령 동안 `/health`는 한 번만, 그것도 3.4초
  걸려 답했다.
- `sync` 라우트 핸들러는 동기판을 쓴다. FastAPI가 이미 스레드풀에서 돌리므로 루프는
  막지 않지만, 아파트먼트 고정을 위해 같은 전담 스레드로 넘긴다.
- 큐 대기 상한(`EXCEL_LIVE_QUEUE_TIMEOUT_SECONDS`, 기본 180초)은 이제 대기와 실행을
  합쳐서 잰다. 매달린 COM 호출을 끊을 방법이 없으면 상한이 의미가 없다.

```powershell
cd python-sidecar
uv run pytest tests/test_event_loop_block.py -q
```

두 번째 테스트는 옛 동작을 일부러 되살려 측정이 '막힘'을 잡아내는지 확인한다. 통과하는
테스트가 실은 아무것도 재지 않는 경우를 막는다.

### 타임아웃과 재전송 정책 (2026-08-11)

`src/lib/requestPolicy.js`가 "얼마나 기다릴지"와 "다시 보내도 되는지"를 함께 소유한다.
둘이 얽혀 있어서다 — 상한을 서버보다 짧게 잡아 놓고 타임아웃에 재시도하면 같은 편집이
두 번 실행되는데, 둘 중 하나만 봐서는 그 조합이 위험한지 보이지 않는다.

**계층은 안쪽이 짧고 바깥이 길다.** 바깥이 먼저 포기하면 UI는 실패라고 말하는데 서버는
계속 편집한다.

| 계층 | 상한 | 정의 위치 |
|---|---|---|
| Python COM 큐 | 180초 | `EXCEL_LIVE_QUEUE_TIMEOUT_SECONDS` |
| Rust IPC | 200초 | `ipc.rs`의 `EXCEL_QUEUE_TIMEOUT` |
| 프론트 엑셀 명령 | 210초 | `requestPolicy.js`의 `EXCEL_REQUEST_TIMEOUT_MS` |

세 값 중 하나를 바꾸면 나머지도 같이 올려야 한다. 순서는 단위 테스트가 지킨다.

**재전송은 서버가 일을 시작하지 않았음이 확실할 때만** 한다. 프론트 타임아웃은 진행
중인 요청을 취소하지 못하므로, 타임아웃 뒤 재전송은 같은 편집을 두 번 하는 길이다.

- `connection refused` / `error sending request` / `http 503` → 다시 보낸다
- 타임아웃 → `repeatable: true`인 요청(대화 등)만 다시 보낸다
- 엑셀 명령은 `repeatable: false`. 옵션을 안 적으면 기본이 `false`다

45초가 지나면 라벨을 "오래 걸리고 있습니다"로 바꾸되 계속 기다린다. 오래 걸리는 것과
실패한 것은 다른 일이다.

알려진 구멍: 사이드카에 요청 단위 마감이 없고(180초는 큐 제출 하나 기준), 멱등 키도
없다. 그래서 "서버는 끝냈는데 응답만 유실된" 경우를 구분할 수 없어 재전송을 막는 쪽을
택했다.

### 플래너 승격 게이트 (2026-08-11)

새 플래너 모델은 **고정 평가셋 154건에서 기준선을 이겨야만** Ollama 태그로 승격된다.
직전 v2→v3 승격은 21건 중 1건 차이로 이뤄졌고, 같은 리포트에서 p95 지연이 69%
늘어난 것은 확인되지 않았다. 그 재발을 막는 장치다.

```powershell
# 학습·GGUF 변환·Ollama 등록을 마친 뒤 (학습 중 실행 금지 — VRAM 경합)
.\scripts\run-planner-eval.ps1 -Candidate ax7bplanner-v5r:latest
```

평가셋(`datasets/eval/planner_eval_v1.jsonl`)의 통합문서와 문장은 **학습 자산과
공유하지 않는다.** 같은 템플릿 생성기로 만들면 암기력을 재게 되기 때문에, 6종의
통합문서를 새로 만들고 문장은 전부 손으로 썼다. 학습 데이터와 문장이 겹치면
`test_planner_eval_set.py`가 실패한다.

| 분류 | 건수 | 무엇을 재는가 |
|---|---|---|
| `core` | 52 | 매일 쓰는 동작 |
| `rare` | 32 | 학습 예제가 적었던 액션 |
| `clarify_yes` | 18 | 되물어야 정답인 모호한 요청 |
| `clarify_no` | 20 | **되물으면 오답** — 과잉 질문 탐지 |
| `multi` | 12 | 두 단계 이상 |
| `colloquial` | 20 | 구어체·오타·생략 |

승격 조건은 `python-sidecar/config/planner_gate_thresholds.json`에 근거와 함께 있다.
그중 두 가지가 설계상 중요하다.

- **되묻기는 총량이 아니라 방향으로 본다.** 모호할 때 묻는 것(`clarify_recall`)은
  올라야 하고, 안 물어도 될 때 묻는 것(`over_clarify_rate`)만 막는다.
- **분류별 퇴보를 따로 막는다**(`max_category_drop_pp`). 전체 점수가 올라도 `core`가
  5%p 넘게 떨어지면 승격되지 않는다 — 흔한 동작을 깎아 희귀 동작을 얻는 건
  개선이 아니다.

액션 이름만 채점한다는 한계가 있다. 파라미터까지 맞는지는
`run_command_battery.py`(라이브 Excel)로 따로 봐야 한다.

**Windows PowerShell 5.1은 BOM 없는 `.ps1`을 cp949로 읽는다.** 한글 주석·문자열이
깨지면서 닫는 따옴표까지 삼켜 `ParserError`로 죽으므로, `scripts/*.ps1`은 반드시
UTF-8 **BOM 포함**으로 저장한다.

첫 실행 결과(v3 기준선 vs v5r, `logs/eval_gate_ax7bplanner-v5r-latest.json`):
승격 불가. 되묻기 재현율은 0% → 100%로 올랐지만 `parse_gain`이 +2.6pp(기준 +5.0pp)에
그쳤고 `multi`가 41.7%p 떨어졌다. 다만 `core` 회귀 대부분은 실력 저하가 아니라
`sort_range`/`sort_rows` 라벨 충돌이었다 — 아래 참조.

#### 겹치는 액션이 채점을 망친다

`sort_range`와 `sort_rows`는 둘 다 등록된 액션이고 예시 트리거가 사실상 같다
("오름차순 정렬"이 양쪽에 있다). 학습셋에도 36 : 37로 반반 들어가 있어 모델이
어느 쪽을 낼지는 동전 던지기다. v3은 `sort_range`, v5r은 `sort_rows`에 안착했고,
평가셋 정답이 `sort_range`라서 v5r만 12건을 잃었다.

이름만 다른 문제가 아니다. 라우터 배선이 다르다.

| | `sort_range` | `sort_rows` |
|---|---|---|
| 실패 시 롤백 스냅샷 | 뜬다 | **안 뜬다** |
| 기준 열 모호하면 되묻기 | 한다 | **안 한다** |

즉 모델이 `sort_rows`로 기울면 정렬은 되지만 되돌리기와 되묻기를 잃는다. 새
액션을 추가할 때는 기존 액션과 트리거가 겹치지 않는지, 겹친다면 안전 배선
(`_ROLLBACK_SNAPSHOT_ACTIONS` · `_AMBIGUITY_SENSITIVE_SLOTS`)이 같은지 확인한다.

### Excel Live 지원 액션 (2026-07-07)

- 기본: `list_workbooks`, `select_workbook`, `read_range`, `write_range`, `create_table`, `set_formula`, `save_workbook`
- 서식/강조: `highlight_by_condition`, `fill_range`, `apply_border`
- 정리/정돈: `clear_range` (선택/지정 범위 내용 비우기, 서식 유지)
- 분석/정리 확장:
  - `sort_range` (정렬)
  - `filter_rows` (조건 필터)
  - `dedupe_rows` (중복 제거)
  - `pivot_table` (집계표 생성)
  - `create_chart` (차트 생성)
  - `validate_data` (빈값/음수/이상치/날짜 범위 점검)
  - `verify_formula_result` (수식 적용 결과 값 검증: 개수/합계/평균/샘플)
- 고급 실행 확장:
  - `protect_sheet` (시트 보호/잠금: 수식 셀 잠금 + 입력 가능 범위 지정)
  - `set_data_validation` (입력 제한/드롭다운/숫자·날짜 범위 제한)
  - `consolidate_sheets` (여러 시트를 하나로 통합)
  - `consolidate_workbooks_from_folder` (폴더 내 여러 파일 통합)
  - `refresh_power_query` (연결/쿼리 RefreshAll)
  - `run_vba_macro` (VBA 매크로 실행)
  - `compare_ranges` (두 범위 diff 생성)
  - `forecast_linear` (단순 선형 추세 예측)

### 러프 입력 UX 원칙

- 사용자가 대충 말하면, 에이전트가 실행 전에 **필수 기준을 되묻는다**.
- 필수 기준이 채워지면 즉시 실행 계획을 확정한다.
- 해석 실패 시 조용히 다른 작업으로 폴백하지 않는다.
- 엑셀 의도 러프 입력은 우선 질문으로 되돌려 슬롯을 채운다(완전 무관 입력만 HTTP 400).
- 목표는 “엑셀 지식이 없어도 자연어로 작업 완료”다.
- 복잡 계산은 `수식 적용 -> 결과 검증` 2단계 계획으로 실행 가능하다.
  - 예: `수량이랑 단가 곱해서 D열 금액 계산식 넣고 결과 확인해줘`
  - 숫자형 수식 검증이 실패(`numeric_cells == 0`)하면 `IFERROR` 래핑으로 자동 재시도 1회를 수행한 뒤 결과를 반환한다.

### 함수명을 모르는 사용자 확장 범위 (2026-07-07)

- 표/양식: `가계부 표 만들어줘`, `근태 관리표 만들어줘`, `회의록 양식 만들어줘`
- 서식/정리: `보기 좋게 정리해줘`, `제목 행 고정해줘`, `입력칸/계산칸 구분해줘`
- 데이터 클리닝: `중복 없애줘`, `빈칸 채워줘`, `전화번호 형식 맞춰줘`
- 텍스트 처리: `이름이랑 전화번호 나눠줘`, `이메일 도메인만 뽑아줘`, `여러 칸 내용을 합쳐줘`
- 날짜/기간: `이번 달 데이터만 보여줘`, `마감까지 며칠 남았는지 계산해줘`, `근속연수 계산해줘`
- 계산/함수 도입: `조건에 맞는 값만 더해줘`, `이름 넣으면 정보 나오게 해줘`, `오류 나면 확인 필요라고 나오게 해줘`
- 분석/집계/시각화: `부서별로 정리해줘`, `월별 매출 요약해줘`, `그래프로 만들어줘`, `대시보드 만들어줘`
- 검증/보호/입력제한: `틀린 값 있는지 봐줘`, `수식칸 못 건드리게 해줘`, `드롭다운 선택으로 만들어줘`
- 자동화: `매번 하는 작업 자동화해줘`, `버튼 누르면 정리되게 해줘`, `데이터 추가 시 자동 반영`

아주 모호한 1차 발화(`정리해줘`, `계산해줘`, `비교해줘`, `문제 찾아줘`)는 아래를 되묻는 방식으로 고정한다.

- 기준 열/범위
- 비교 기준(전월/전년/다른 시트)
- 출력 위치(원본 수정/새 시트)
- 자동화 방식(버튼/자동 반영)

추가 멀티턴(신규 갭 보강) 예시:

- `수식 칸은 못 건드리게 하고 입력칸만 수정 가능하게`
  - -> 보호 방식 확인(수식셀 잠금 여부) -> 입력 허용 범위 확인 -> `protect_sheet`
- `상태는 선택해서 입력하게`
  - -> 대상 열/범위 확인 -> 목록값 확인(완료/진행중/지연...) -> `set_data_validation`
- `파일 여러 개 합쳐줘`
  - -> 폴더 경로 확인 -> 원본 시트명/패턴 확인 -> 출력 시트 확인 -> `consolidate_workbooks_from_folder`
- `두 시트 차이 찾아줘`
  - -> 좌/우 시트 확인 -> 비교 범위 확인 -> 결과 시트 여부 확인 -> `compare_ranges`
- `다음 달 매출 예측해줘`
  - -> 원본 범위 확인 -> 예측 기간(horizon) 확인 -> 출력 위치 확인 -> `forecast_linear`

Excel Live 테스트 세트는 간소화된 문서 + 러프 스모크 스크립트로 운영:

- `엑셀 작업 예시.md`
- `python-sidecar/scripts/smoke_excel_live_nl.py`
- `python-sidecar/scripts/smoke_excel_ko_hard_tasks.py` (한국어 고난도 작업 E2E/엔진 점검)
- `datasets/excel_complex_scenarios_v1.json` (복잡 작업 30시나리오 팩)
- `python-sidecar/scripts/verify_excel_complex_scenarios.py` (오라클 기반 자동 검증)

한국어 입력 우선 distillation 샘플 생성 예시:

```bash
cd python-sidecar
uv run python scripts/build_excel_distill_jsonl.py \
  --all-events ../logs/all_events.jsonl \
  --preferred-locale ko \
  --drop-non-preferred-locale \
  --output ../logs/excel_distill_ko_only_sample.jsonl \
  --limit-per-source 200 \
  --stats
```

최근 한국어 고난도 스모크 결과(2026-07-21):
- `korean_command_e2e_hard_tasks`: `7/7` 성공
- `execution_hard_tasks`: `9/9` 성공

최신 종합 재검증(2026-07-21, 로컬 앱 실행 + 회귀 기준):
- `pytest` 핵심 회귀: `127 passed`
- 프론트 단위: `22 passed`
- 리셋 포함 E2E(`smoke_excel_live_reset_cycle.py`): `40/42` 성공 (95.2%)
- 광범위 자연어 142-step(`smoke_excel_live_nl.py`): `128/142` HTTP 200 (90.1%)

복잡 작업 검증 범위(실제 실행 확인):
- `pivot_table` (피벗 생성/집계)
- `compare_ranges` (두 시트 범위 diff 생성)
- `forecast_linear` (선형 예측)
- `set_data_validation` (입력 제한 규칙)
- `set_formula` + `verify_formula_result` (수식 적용 + 값 검증)
- `consolidate_sheets` (멀티시트 통합)

현재 관측 이슈(복잡/러프 명령 안정화 대상):
- 일부 러프 문장에서 슬롯 누락 시 `500` 응답(`filter_rows.value` 누락, `set_formula` 형식 누락) 발생
- 긴 추론 케이스에서 `ReadTimeout`(8초) 빈도 존재

### 플래너 학습셋 (planner_sft_v5, 2026-08-10)

학습 데이터는 `scripts/build_planner_sft_jsonl.py`가 **추론과 똑같은 프롬프트**로 만든다.
v3까지 이 파이프라인에는 세 가지 구멍이 있었고, v4·v5에서 차례로 막았다.

| 문제 | 증상 | 고친 방법 |
|---|---|---|
| 프롬프트에 통합문서 상태가 없었다 (학습 0%, 추론 100%) | 시트·열 이름을 지어냄. 정답의 16.8%가 `Sales_Data` | `excel_workbook_fixtures.py`가 정답 계획과 아귀 맞는 다이제스트를 레코드마다 합성 |
| 되묻기 정답이 0건 | 애매한 지시에도 무조건 실행 | `excel_clarify_cases.py`가 되묻기 1턴 + 답변 2턴 쌍을 생성 |
| 액션 분포가 증류 로그 편향 그대로 | `pivot_table` 161건 vs `compare_ranges` 1건, **0건인 액션 3종** | `excel_action_coverage_cases.py`가 실행 가능한 49개 액션 전부에 바닥을 깔고, 빌더가 상위 액션에 상한을 건다 |

v4 → v5 분포 변화 (`scripts/audit_planner_action_coverage.py`):

| | v4 | v5 |
|---|---|---|
| 학습 예제 0건인 기능 | 3종 | **0종** |
| 최소 / 최대 예제 수 | 0 / 161 | **16 / 66** |
| 되묻기 비중 | 17.7% | 7.5% |
| 통합문서 상태 포함 | 100% | 100% |

커버리지 생성물은 **프로덕션 검증기를 그대로 통과**하는지 테스트한다
(`tests/test_excel_action_coverage_cases.py`). 학습은 시켰는데 실행 단계에서 반려되는
계획을 가르치는 것이 v1~v3의 반복된 실패였기 때문이다.

```bash
# 학습셋 재생성
python scripts/build_planner_sft_jsonl.py \
    --input ../datasets/distill/excel_distill_v1_verified_augmented.jsonl \
            ../datasets/distill/planner_augment_v3.jsonl \
            ../datasets/distill/excel_hard_manual_v1.jsonl \
            ../datasets/distill/excel_new_tools_manual_v1.jsonl \
            ../datasets/distill/excel_scenario_report_extract_v1.jsonl \
    --output ../datasets/train/planner_sft_v5_train.jsonl \
    --with-clarify --with-coverage --coverage-per-action 16 --max-per-action 40

# 분포 감사
python scripts/audit_planner_action_coverage.py \
    --jsonl ../datasets/train/planner_sft_v5_train.jsonl --output ../scratch/coverage_v5.json
```

#### 학습/검증 분할 — 자동화 트래픽을 먼저 걷어낸다

수확기(`build_excel_distill_jsonl.py`)는 실행 로그에서 학습 데이터를 만드는데,
그 로그에는 pytest와 프로브 스크립트가 만든 트래픽이 함께 쌓인다. 거르지 않으면
모델이 픽스처 문자열(`alpha123`)을 배우고, 검증셋은 자기 테스트를 채점하게 된다.
v5 검증셋 34건 중 21건이 실제로 pytest 세션이었다.

`services/traffic_origin.py`가 출처를 가른다. 기록 시점에 `origin`을 남기고,
태그가 없는 과거 이벤트는 세션 id·통합문서 경로로 추정하되 **확인되지 않으면
사람으로 치지 않는다.**

```bash
# 로그에 누구 트래픽이 얼마나 쌓였는지
uv run python scripts/report_traffic_origin.py ../logs/all_events.jsonl

# 오염 제거 + 중복 제거 + 출처×액션 층화 분할
uv run python scripts/split_planner_sft.py \
    --input ../datasets/train/planner_sft_v5_train.jsonl \
            ../datasets/train/planner_sft_v5_test.jsonl \
    --train-out ../datasets/train/planner_sft_v6_train.jsonl \
    --test-out ../datasets/train/planner_sft_v6_test.jsonl
```

분할은 지시문이 양쪽에 걸치지 않고, 검증셋에 중복이 없고, 검증에만 있고 학습에
없는 액션이 생기지 않도록 보장한다 (`tests/test_split_planner_sft.py`).

> 학습 중 eval loss는 **진전 계기판**일 뿐이다. 확인된 사람 트래픽이 0건이라
> 실사용 일반화는 아직 측정할 수 없다. 승격 판정은 손으로 쓴
> `planner_eval_v1.jsonl` 154건이 담당한다.

> **학습 중 GPU 주의**: 4060 Ti 16GB에서 QLoRA 학습은 약 10.5GB를 쓴다.
> Ollama가 플래너 모델을 물고 있으면(약 5GB) VRAM이 꽉 차 시스템 메모리로 페이징되고,
> 스텝 시간이 55초 → 250초로 무너진다. 학습 전에 `Stop-Process -Name ollama*`로 내려둘 것.

### 복잡 작업 100% 로드맵 (다음 단계)

- 라우터 보강: `filter/if/count` 계열 필수 슬롯 강제 채움 + 누락 시 `clarify` 고정
- 시간 예산 보강: parse/execute timeout 상향 + 재시도 백오프 정책 기본값 상향
- 검증기 보강: 실행 후 타입/범위/행수 검증 실패 시 자동 재계획 1회
- 회귀 자동화: 142-step + reset-cycle + hard-task를 릴리즈 게이트에 묶어 상시 측정
- 승격 기준: `hard 100%`, `reset-cycle >= 98%`, `broad NL >= 95%` 달성 시 기본 플래너 승격

### 질문 세트 관점 vs 시스템 설계 관점 (2026-07-07 보강)

- 질문 세트 관점:
  - 러프 명령 커버리지를 기능 나열 수준에서 멈추지 않고, 파일 상태/권한/복구/버전/성능/교육형 질문까지 확장
  - `엑셀 작업 예시.md`의 `3) 안전/복구 스모크`, `4) 최근 이슈 재검증` 구간에서 해당 케이스를 관리
- 시스템 설계 관점:
  - 권장 구조는 `라우터 + 전문 에이전트(도구형) + 검증기 + 승인 게이트`
  - 핵심 슬롯(`task_goal`, `target_sheet`, `target_range`, `key_column`, `value_column`, `output_location`, `safety_policy`, `version_constraint`)이 비어 있으면 실행 전 질문으로 수집
  - 위험 작업(삭제/덮어쓰기/매크로/외부 링크/개인정보)은 승인 후 실행
  - 검증기는 수식 샘플/행열 일치/차트 소스 범위/원본 보존 여부를 확인
- 스모크 입력 보강:
  - `python-sidecar/scripts/smoke_excel_live_nl.py`에 권한/복구/성능/버전/교육형 단일턴·멀티턴 케이스를 추가해 회귀 점검 범위를 넓힘
- 데이터셋 스키마/샘플:
  - `docs/EXCEL_LIVE_ROUTER_DATASET_SCHEMA.md`
  - `docs/EXCEL_LIVE_ROUTER_DATASET_SAMPLE.json`

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
