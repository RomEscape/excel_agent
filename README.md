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
│   │   ├── excel_live_service.py   # xlwings 기반 Excel 제어 (Windows COM / macOS appscript)
│   │   ├── excel_live_agent.py     # 자연어 → Excel 명령 파싱
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
| `local-stack/GEMMA4_OPENCLAW.md` | Gemma4 로컬 스택 상세 |

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
    - 예: `정렬해줘` → `어떤 열 기준으로 정렬할까요?` → `매출 열 기준 높은 순`
    - 예: `그래프로 만들어줘` → `선/막대/원형 중 어떤 차트?` → `선 그래프`
    - 예: `중복 지워줘` → `어떤 기준으로 중복 판단?` → `전화번호 기준`
    - 예: `완료 건수 세어줘` → `어떤 열 기준으로 셀까요?` → `B열 상태에서 완료 개수`

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

전체 173개 검증 입력 세트 + 확장 러프 스모크(최신 142 step)는 아래 파일/스크립트에서 확인 가능:

- `TEST_INPUT_COMMANDS_EXCEL_LIVE.txt`
- `python-sidecar/scripts/smoke_excel_live_nl.py`

최신 142-step 스모크 요약:
- `http_200=141/142`
- `network_or_timeout=1`
- `ask_follow_up=112`
- `approval_required=24`
- `slow_ge_4000ms=17`
- `latency_p50=2ms`, `latency_p95=6010ms`

남은 관측 이슈:
- `저장해줘` 1건은 `save_workbook` 단계에서 8초 타임아웃(네트워크/파일 잠금 환경 영향 가능)으로 기록됨

### 질문 세트 관점 vs 시스템 설계 관점 (2026-07-07 보강)

- 질문 세트 관점:
  - 러프 명령 커버리지를 기능 나열 수준에서 멈추지 않고, 파일 상태/권한/복구/버전/성능/교육형 질문까지 확장
  - `TEST_INPUT_COMMANDS_EXCEL_LIVE.txt`의 `150~173` 구간에서 해당 케이스를 별도로 관리
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
