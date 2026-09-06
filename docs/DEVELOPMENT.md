# 김대리 개발 안내 (README에서 분리, 2026-09-06)

> 사용자용 README는 설치·사용법만 남기고, 개발 환경·매일 쓰는 명령·구조·문서 지도는 여기로 옮겼다.
> 명령과 경로는 2026-09-06 새 PC(Rust·모델 없는 Windows 11)에서 실제로 돌려 확인한 것만 적었다.
> 규율(개발일지·측정 절차·코드 원칙)은 `CLAUDE.md`가 정본이다.

## 2. 개발 환경 — 명령 하나로

셋업 스크립트 하나가 도구·의존성·모델을 전부 준비한다. 저장소는 비공개라 GitHub 접근 권한이 있어야 클론된다.

### Windows

```powershell
git clone -b openclaw_jinh_demo https://github.com/sadStoneTurtle/officeclaw.git
cd officeclaw
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
npm run tauri:dev
```

`setup.ps1`이 하는 일(순서대로): winget으로 Node·Rust(rustup)·Ollama·uv·Python 설치 → MSVC 빌드 도구 확인(없으면 설치) →
`npm ci` → `uv sync --extra dev`(venv는 `%LOCALAPPDATA%\officeclaw\venvs\python-sidecar`, OneDrive 밖) →
Tauri externalBin 자리 파일 → `cargo fetch` · `npm run build` · `cargo check` → 모델 두 개 pull + 이름 맞추기.
첫 실행은 모델 9GB 포함 20~40분 걸린다(2026-09-06 새 PC 실측 16분). 다시 돌리면 이미 된 단계는 건너뛴다.

**엑셀 파일은 저장소 루트의 `엑셀 작업 폴더/`에 둔다.** 소스 트리에서 앱을 돌리면 사이드카가 이 폴더를 워크스페이스로 쓴다
(git에는 안 올라간다). 데모 워크북 `AI_Excel_Automation_Demo.xlsx`가 시작 파일로 들어 있다. 다른 곳을 쓰려면
`OFFICE_CLAW_WORKSPACE_DIR` 환경변수로 바꾼다. 온보딩 3단계의 폴더 칸은 고르는 게 아니라 이 경로가 자동으로 채워지는 칸이고,
누르면 탐색기로 열린다. 로컬 엔진이 켜질 때까지(첫 구동 최대 2분) 비어 있을 수 있다.
**어느 파일에 명령할지는 워크스페이스 목록에서 .xlsx를 한 번 클릭해 고른다**(행에 "대상" 배지가 붙고 채팅 헤더에 이름이 뜬다). 더블클릭이나 "Excel로 열기"는 Excel 앱으로 여는 것이다. 홈 화면의 문서 카드도 같다(클릭=대상, 더블클릭=열기).

### macOS (Apple Silicon)

```bash
brew install ollama            # setup.sh는 Ollama를 설치하지 않는다 — 감지만 한다
xcode-select --install         # Xcode CLT
git clone -b openclaw_jinh_demo https://github.com/sadStoneTurtle/officeclaw.git && cd officeclaw
./scripts/setup.sh
npm run tauri:dev
```

> **macOS 실기 검증은 아직 0회다.** 첫 구동에서 어긋나는 항목은 `개발일지.md`에 실측으로 남겨 달라.
> 첫 엑셀 명령에서 "Excel 제어 허용?" 팝업이 뜬다(시스템 설정 → 개인정보 보호 → 자동화). 거부하면 모든 라이브 명령이 실패한다.
> 라이브 모드에서 조건부 서식 3종(데이터 막대·색조·수식)과 입력 유효성 검사는 macOS Excel 자동화 API가 없어 불가하다.

### 셋업 옵션

| Windows `setup.ps1` | macOS `setup.sh` | 뜻 |
|---|---|---|
| `-DryRun` | `--dry-run` | 실행 없이 단계만 출력 |
| `-SkipBuild` | `--skip-build` | `npm run build`·`cargo check` 생략 |
| `-BuildSidecar` | `--build-sidecar` | 배포용 사이드카 단일 실행파일까지 빌드(Nuitka + PyInstaller, 개발에는 불필요) |
| `-NoAutoInstallTools` | `--no-auto-install-tools` | 도구 자동 설치 끄기 |
| `-PlannerHfRepo "<계정>/<저장소>"` | `OFFICECLAW_PLANNER_HF_REPO=…` | 플래너 GGUF를 받을 HF 저장소(기본 `PJiNH/ax7bplanner-v3-GGUF`) |
| `-GeneralHfRepo "<계정>/<저장소>"` | `OFFICECLAW_GENERAL_HF_REPO=…` | 범용 모델 GGUF를 받을 HF 저장소(기본 `jayusop/A.X-4.0-Light-Q4_K_M-GGUF`) |

### 안 될 때

| 증상 | 원인과 조치 |
|---|---|
| `failed to run 'cargo metadata' … program not found` | Rust가 없거나, 셸이 설치 전 PATH를 물려받았다(VS Code·Cursor 통합 터미널은 **에디터를 재시작**해야 새 PATH를 본다). `npm run tauri:dev`는 `scripts/with-tool-path.mjs`를 거쳐 `~\.cargo\bin`을 스스로 찾으니 최신 코드면 그대로 되고, 안 되면 `setup.ps1`을 안 돌린 것이다. |
| `os error 5` 빌드 실패 | 앱이 이미 떠 있다. 먼저 닫는다. |
| `스크립트 실행이 … 정책 때문에` | `powershell -ExecutionPolicy Bypass -File scripts\setup.ps1`로 친다. |
| 채팅은 되는데 Excel 계획이 엉망 / "플래너가 조용히 죽음" | 모델이 없다. `ollama list`에 `ax7bplanner-v3`·`skt/A.X-4.0-Light`가 있어야 한다. 없으면 `setup.ps1` 재실행 또는 [README의 모델 준비](../README.md). |
| 파이썬을 고쳤는데 반영이 안 됨 | 옛 사이드카가 포트 19532에 살아 있다. [3절](#3-매일-쓰는-명령)의 "사이드카 죽였다 켜기". |
| 명령마다 20~30초 걸리다 "…초 안에 답하지 못했습니다" 또는 엉뚱한 되묻기 | GPU가 없거나 작은 PC(내장 그래픽)라 플래너가 CPU로 돈다. 사용자 환경변수 `EXCEL_LIVE_PARSE_TIMEOUT_SECONDS=45`를 두고 앱을 재시작한다(2026-09-06 Intel Arc A350M PC 실측: 기본 10초로는 매번 타임아웃). |
| `failed to locate pyvenv.cfg` | `services/sidecar/.venv`가 OneDrive로 옮겨 온 껍데기다. 지우고 `setup.ps1`을 다시 돌린다. |

---

## 3. 매일 쓰는 명령

**인터프리터 규약**: 파이썬 명령은 전부 `cd services/sidecar` 한 뒤 `uv run python …`으로 친다.
`uv`가 없으면 `sh scripts/py-run.sh …`가 프로젝트 venv를 찾아 준다.

```powershell
# 앱 (Rust + Vite + webview). Rust를 고쳤으면 재시작해야 새 IPC가 등록된다.
npm run tauri:dev
# UI 레이아웃만 볼 때 (Tauri 없음 → invoke()는 전부 실패)
cd apps/desktop; npm run dev

# 사이드카를 따로 띄울 때 / 파이썬을 고친 뒤 죽였다 켜기 (앱을 껐다 켜도 옛 사이드카가 남는다)
Get-CimInstance Win32_Process | ? { $_.CommandLine -match 'office_claw_sidecar' } | % { Stop-Process -Id $_.ProcessId -Force }
cd services/sidecar; uv run python -m office_claw_sidecar --port 19532 --auth-token dev-token

# 모델이 다 있는지
curl -H "Authorization: Bearer dev-token" localhost:19532/health     # missing_models 가 [] 여야 한다

# 로그 — logs/chat_log.jsonl 을 직접 열지 말 것(한 턴이 2KB 한 줄)
cd services/sidecar
uv run python scripts/show_turns.py --log ../../logs/chat_log.jsonl -n 5          # 최근 5턴
uv run python scripts/show_turns.py --log ../../logs/chat_log.jsonl --failed -n 8 # 깨진 턴만

# 야간 게이트 — 세션을 시작하면 이것부터 본다. 맨 위에 ❌가 있으면 그게 첫 작업이다.
Get-Content logs\nightly\LATEST.md -TotalCount 20
```

### 커밋 전 검사 (CI 미러)

`.github/workflows/pr-check.yml`의 잡 5개를 그대로 미러링한다. `lefthook`이 pre-commit·pre-push로 자동 실행하지만
(`cd apps/desktop && npm ci`를 해야 훅이 설치된다), PR 전 한 번 직접 돌리는 걸 권한다.

```bash
# 0. 개발일지 — 코드가 바뀌었는데 개발일지.md 가 안 바뀌면 커밋이 막힌다(devlog-guard)
node scripts/check-devlog-update.mjs --staged
# 1. Python 사이드카 (CI는 uvx 가 아니라 uv run ruff 다)
cd services/sidecar && uv run ruff check . && uv run pytest -q
# 2. Frontend
cd apps/desktop && npm ci && npm run lint && npm run test:unit && npm run build
# 3. Rust — cargo fmt --check 가 가장 자주 떨어진다
cd apps/desktop/src-tauri && cargo fmt --check && cargo clippy --all-targets -- -D warnings
# 4. Flutter 모바일 (CI는 3.44.6 고정)
cd apps/mobile && flutter pub get --enforce-lockfile && flutter analyze && flutter test
```

- **코드를 고친 턴은 `개발일지.md`에 한 항목을 남긴다** — 증상·원인(파일:줄)·조치·측정(실행 id)·남은 것. 양식은 `CLAUDE.md` §1.
- `tests/test_noop_honesty.py` 한 건은 Ollama 플래너 모델이 있어야 통과한다(모델 없는 PC에서는 1 failed가 정상, 2026-09-06 실측).
- CI에 없는 검증: `cd services/relay && uv run pytest -q`, `scripts/verify-local-stack.ps1`, `scripts/verify-excel-live-e2e.mjs`(Excel 필요, 사이드카를 `--auth-token dev-token`으로 띄워 둘 것).
- 프로토콜 스키마를 고쳤으면 `cd packages/protocol/python && uv run python ../scripts/export_schema.py` 재생성 후 diff가 비어야 한다.

### 그 외 자주 쓰는 것

```bash
# 중계 서버 (모바일 연동 테스트)
cd services/relay && uv sync --extra dev && uv run python -m oc_relay      # PORT 기본 8787
# 모바일 (실기기는 데스크톱 페어링 화면의 relay 주소를 127.0.0.1 대신 LAN IP로)
cd apps/mobile && flutter pub get && flutter run
# 명령 진단 배터리 — 고치기 전/후를 잰다
cd services/sidecar && uv run python scripts/run_command_diagnostics.py -n 3 --label before-<작업이름>
```

---

## 4. 구조와 문서

```
officeclaw/
├─ apps/desktop/        Tauri 앱 — React 프론트(src/) + Rust 셸(src-tauri/)
├─ apps/mobile/         Flutter 앱 — 릴레이로 데스크톱을 원격 조종
├─ services/sidecar/    Python FastAPI 사이드카 — Excel 편집(xlwings·openpyxl)·LLM·권한
├─ services/relay/      중계 서버 — 라우팅 헤더만 읽는 content-blind 릴레이
├─ packages/            공용 계약 — protocol(스키마·프레임), py-shared(auth·codec)
├─ scripts/             setup.ps1 / setup.sh / dev.ps1 / dev.sh / with-tool-path.mjs / nightly-gates.ps1 …
├─ 엑셀 작업 폴더/       개발기 워크스페이스 — 앱이 읽고 쓰는 엑셀 파일 (git 제외)
├─ datasets/ deploy/ train/ artifacts/   플래너 SFT 데이터·Modelfile·학습·LoRA(가중치는 git 밖)
├─ docs/  logs/  config/                 문서 · 실행 로그(chat_log·nightly) · 게이트 기준선
├─ CLAUDE.md            작업 규율과 명령 원문 (개발일지 규칙, 측정 절차, 코드 원칙)
└─ 개발일지.md           실측 기록 — "무엇을 재 봤고 무엇이 반증됐는가"
```

**LLM 경로**: Ollama OpenAI 호환 API(`/v1/chat/completions`) 단일 경로. 계획은 `planner_model`(`ax7bplanner-v3`, 계획 JSON 전용 SFT),
고수준 분해와 일반 대화는 `model`(`skt/A.X-4.0-Light`). 권한(SAFE/CONFIRM/DENIED)은 `tool_registry.py`가 소유한다.

| 읽을 것 | 무엇 |
|---|---|
| [docs/architecture.md](docs/architecture.md) | 계층별 역할, Excel tool-calling 흐름, 모바일 원격 제어 흐름 (README에서 분리) |
| [docs/demo-branch-notes.md](docs/demo-branch-notes.md) | 데모 브랜치 설계·측정 노트 — 질문 예시, 확신 3분기, 게이트, 트레이스, 플래너 학습 (README에서 분리) |
| [docs/build-and-release.md](docs/build-and-release.md) | 윈도우 네이티브 빌드·배포, 사이드카 하드닝(Nuitka + PyInstaller) |
| [CLAUDE.md](CLAUDE.md) | 개발일지 규칙, 에이전트 동작을 고칠 때의 측정 절차, 모듈 지도 |
| [개발일지.md](개발일지.md) | 날짜별 실측 기록 (330개 항목) |
| 하위 README | [services/relay](services/relay/README.md) · [packages/protocol](packages/protocol/README.md) · [packages/py-shared](packages/py-shared/README.md) · [logs](logs/README.md) · [datasets/distill](datasets/distill/README.md) |

### 진단·감사 보고서 — 발행된 아티팩트 (claude.ai 링크)

> 코드 밖에 있는 문서들 — 회의·인수인계·실측 대조는 전부 여기서 찾는다.
> 링크는 소유자 계정으로 발행된 것이라 처음 열 때 claude.ai 로그인이 필요할 수 있다.
> 문서를 갱신할 때는 새로 만들지 말고 **같은 URL로 재발행**한다(새 URL이 생기면 이 표가 낡는다).

| 문서 | 무엇인가 | 누가 읽나 | 최신 |
|------|----------|-----------|------|
| [Office Claw 에이전트 진단](https://claude.ai/code/artifact/74b35322-e0eb-4b73-9fd6-d4fa982bef63) | 구조 지도 · 문제 4가지 · 반복 고리 · 8/19~25 변화(게이트·감사 라운드·반증) · 방향 · 핵심 숫자 · 결정 5항 | 회의 참석자, 처음 보는 사람 | 2026-08-25 v5 |
| [Office Claw 구현 플로우](https://claude.ai/code/artifact/20c4452e-8a69-4e5b-be46-cd9e72d9f82d) | React → Rust → 사이드카 → LLM → 엑셀 전 구간을 함수·줄 번호·JSON 필드명 단위로. 승인 왕복, 매크로 루프, 함정 10가지 | 개발자, 이식·인수인계 | 2026-08-25 v3 |
| [chat_log 판독 가이드](https://claude.ai/code/artifact/e4d7a612-231a-40ae-9061-724ec0f4e5f5) | 판단 로그(턴당 JSON 한 줄)의 13키 해부 · routes/stages 판독법 · 사용자 문제를 원인 층까지 지목해 개선으로 잇는 루프 · 실전 판독 예제 | 로그로 문제를 추적하려는 사람 | 2026-09-01 |
| [사람 말투 대화 전록](https://claude.ai/code/artifact/cb1553b2-14a5-4b77-81f6-7bb021cac066) | 8개 데모 대시보드를 좌표 없는 사람 문장(오타·반말·정정 포함)과 실제 붙여넣기 흐름으로 만든 **대화 전문** — 되묻기·해석 카드·실행 리포트가 순서 그대로, 5라운드 반복 판정 | 실사용 흐름을 보고 싶은 사람 | 2026-08-19 |
| [이미지 대 엑셀 대조](https://claude.ai/code/artifact/214f38f7-0f4d-49b8-8879-5d5e76685082) | 예시 6종의 목표 이미지(왼쪽)와 대화만으로 만들어진 실제 엑셀 파일 렌더(오른쪽)를 시트별로 나란히 | 결과물을 눈으로 확인하려는 사람 | 2026-08-18 |
| [명령 인식·계획 정확도](https://claude.ai/code/artifact/1845c30a-5434-4b60-80d6-6e8faba21c90) | 다양한 명령을 넣었을 때 무엇을 할지 맞게 인식·계획하는지 — 게이트 안(99.4%)과 밖(68%)을 갈라 잰 실측, 과제별 표와 오인식 해부 | 정확도를 수치로 보려는 사람 | 2026-08-25 |
| [Office-Claw 견고성 감사](https://claude.ai/code/artifact/110e2634-4a76-40bd-b0a0-a4b77d1ed472) | 대형 모델에 맡기듯 여러 방향에서 찔러 본 기록 — 라우팅 누수·정체성 이탈·프롬프트 주입 등 발견 F-01~F-09와 수정 순서 | 보안·견고성 검토자 | 2026-08-16 |

### 핵심 설계 원칙

1. **상태는 모듈이 소유** — 도메인 로직·상태는 `lib/` 모듈 안에, UI는 store 구독만
2. **표시와 데이터 분리** — `lib/*.js`(데이터) → `components/ui/*.jsx`(primitive) → 도메인 UI(조합)
3. **중복 fetch 없음** — 같은 데이터는 중앙 store/manager 1곳에서
4. **IPC 단일 진입점** — 모든 Tauri invoke는 `lib/api.js` 경유
5. **계약은 SSOT 하나** — 프로토콜은 `packages/protocol`, Excel 함수 명세는 `excel_tool_schemas.py`, 권한은 `tool_registry.py`
6. **브랜드 색은 SVG가 원본** — `apps/desktop/src/assets/brand-logo-*.svg` / `brand-wordmark.svg`의 값을 코드로 옮길 뿐, 새 색을 코드에서 짓지 않는다

### 현재 상태와 한계 (2026-09-06)

- 릴리스 타깃은 **Apple Silicon macOS + Windows x64** 둘뿐이다. Intel Mac은 의도적으로 뺐다(러너 비용).
- 발행된 릴리스 0건. macOS 실기 검증 0회. 표시명은 **김대리**, 번들 식별자는 `com.kimdaeri.app`.
- 모바일 원격 승인 UI와 데스크톱 QR 카운트다운은 구현돼 있다. E2E 암호화·재연결 재개·Redis 라우팅은 미구현.
- 플래너는 v3를 유지한다(v5r은 2026-08-20 삭제). 정확도 수치는 위 아티팩트 표의 "명령 인식·계획 정확도".
