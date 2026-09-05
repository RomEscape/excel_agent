# Office-Claw 프로젝트 규칙

로컬 LLM이 엑셀을 편집하는 Tauri 앱. Rust(셸) + React(UI) + Python 사이드카(엑셀·LLM).

---

## 1. 고쳤으면 개발일지에 적는다 — 예외 없음

**코드를 편집하거나 실험을 돌린 턴은 `개발일지.md`에 한 항목을 남기고 끝낸다.**
이 저장소의 핵심 자산은 코드가 아니라 "무엇을 재 봤고 무엇이 반증됐는가"의 기록이다.
기록하지 않은 측정은 다음 사람이 처음부터 다시 재게 만든다.

적을 것 — 다섯 줄이면 충분하다:

```markdown
## 2026-08-16 (일) — <한 줄 제목>

- **증상**: 무엇이 잘못 보였나 (사용자 표현 그대로)
- **원인**: 파일:줄 + 왜 그렇게 되는지
- **조치**: 바꾼 파일과 한 줄 요약
- **측정**: 전/후 수치 + 실행 id (없으면 "측정 안 함"이라고 적는다)
- **남은 것**: 안 고친 것과 그 이유
```

지킬 것:

- **수치는 실측만.** 실행 id(`0811-171221-after-guards`)를 함께 남겨 나중에 같은 로그로 재확인할 수 있게 한다. 지어낸 숫자는 이 체계를 통째로 무용지물로 만든다.
- **반증된 것도 남긴다.** 외부 지적이 이미 구현된 기능을 가리키는 경우가 잦다(2026-08-11에 9개 중 3개가 그랬다).
- **날짜는 절대 표기로.** "어제", "지난주" 금지.

> `lefthook.yml`의 `devlog-guard` 훅이 이걸 **실제로 강제한다** — 코드가 staged인데
> `개발일지.md`가 없으면 커밋이 종료코드 1로 막힌다(2026-08-20 실측으로 확인).
> `scripts/py-run.sh`가 uv 없는 환경에서 프로젝트 venv를 찾아 주므로 이 개발기에서도 돈다.
> 다만 훅은 **파일이 바뀌었는지만** 본다 — 내용이 실측인지는 사람만 안다.

---

## 2. 명령어 — 복붙용

> **모노레포 구조 (2026-07 `feat/monorepo-relay`)**: 데스크톱 앱 = `apps/desktop/`(프론트엔드 + `src-tauri/`), Python 사이드카 = `services/sidecar/`, 중계 서버 = `services/relay/`, 공용 계약·코드 = `packages/`(`protocol`·`py-shared`). 경로 매핑: `src/`→`apps/desktop/src/`, `src-tauri/`→`apps/desktop/src-tauri/`, `python-sidecar/`→`services/sidecar/`. 아래 표의 파일 경로도 이 접두사 기준으로 읽는다.


### 이 환경의 파이썬

`uv`가 있으면 `uv run`을 그대로 쓰면 된다. 없거나 못 믿겠으면 아래 venv 인터프리터가 확실하다(셋업이 만들어 둔다).

```powershell
$PY = "$env:LOCALAPPDATA\officeclaw\venvs\python-sidecar\Scripts\python.exe"
$env:PYTHONUTF8 = "1"
```

문서의 `uv run python X` 는 전부 `& $PY X` 로 바꿔 읽는다.
`uv`를 복구하려면: `powershell -c "irm https://astral.sh/uv/install.ps1 | iex"`

### 실행

```powershell
cd apps/desktop; npm run tauri:dev        # 전체 앱 (Rust + Vite + webview). 개발 시 기본
npm run dev              # vite만. UI 레이아웃 확인용 — invoke()는 전부 실패한다
```

- Rust를 고쳤으면 `tauri:dev` 재시작해야 새 IPC가 등록된다.
- **앱이 이미 떠 있으면 빌드가 실패한다**(`os error 5`: 실행 중인 exe를 못 지움). 먼저 닫는다.
- **사이드카는 고아가 된다.** `sidecar.rs`가 자식 프로세스 핸들을 버려서, 앱을 껐다 켜도
  포트 19532의 옛 사이드카가 살아남는다. **파이썬을 고쳤으면 사이드카를 직접 죽였다 켠다:**

```powershell
Get-CimInstance Win32_Process | ? { $_.CommandLine -match 'office_claw_sidecar' } |
  % { Stop-Process -Id $_.ProcessId -Force }
cd services/sidecar; & $PY -m office_claw_sidecar --port 19532 --auth-token dev-token
```

> **윈도우 네이티브 빌드 / 배포**: 상세 절차는 [`docs/build-and-release.md`](docs/build-and-release.md) (개발용/배포용 구분). 반복되는 함정 2가지 — (1) `tauri dev`도 `externalBin`(`binaries/office-claw-sidecar-<target>[.exe]`) 파일이 **존재**해야 빌드 통과(없으면 빈 placeholder 생성). (2) dev 모드 사이드카는 `services/sidecar/.venv`로 뜬다 → 윈도우는 `uv sync`로 venv 생성 필요(WindowsApps `python`은 가짜 스텁). 배포용 단일 설치파일은 `tauri build`/릴리스 CI가 PyInstaller 사이드카·WebView2를 번들하며 — **빌드 툴체인(Rust/MSVC/Node)은 `.exe`에 안 들어간다.**

> **2026-08 배포 타깃 노트**: 릴리스는 **Apple Silicon macOS + Windows x64 두 개만** 만든다(`release.yml` 매트릭스). Intel Mac은 의도적으로 뺐다 — macOS 러너는 GitHub Actions 청구 분이 **10배 배율**이라 Intel 잡 하나가 릴리스당 약 180분을 먹고, 그것만 빼도 릴리스 비용이 절반 가까이 준다. 되살리려면 매트릭스와 **랜딩 페이지 다운로드 버튼을 함께** 늘려야 한다 — 브라우저는 Apple Silicon과 Intel Mac을 구분하지 못하므로(Safari가 호환성 때문에 Apple Silicon에서도 userAgent에 "Intel Mac OS X"를 넣는다) 자동 판별이 불가능하고 버튼을 나눠야 한다.
>
> 자산 이름은 `releaseAssetNamePattern: kimdaeri-[platform]-[arch][ext]`로 **버전을 뺀다.** 랜딩 페이지가 `releases/latest/download/<고정이름>`을 영구 URL로 쓰기 때문이다 — 버전이 들어가면 릴리스마다 (다른 저장소에 있는) 랜딩 페이지를 고쳐야 한다. 기본 이름은 `productName`인 한글 `김대리`가 들어가 URL 인코딩도 지저분해진다.
>
> **랜딩 페이지는 아티팩트 실제 주소를 하드코딩하지 않는다.** 자기 도메인의 `/download/mac`·`/download/windows`만 가리키고, CloudFront Function이 302로 실제 위치(GitHub Releases)로 넘긴다. 저장 백엔드를 옮겨도 랜딩 페이지를 안 고쳐도 되게 하려는 것이다.

### 커밋 전 검사 (CI 미러)

```powershell
cd apps/desktop/src-tauri;      cargo fmt --check; cargo clippy --all-targets -- -D warnings
cd services/sidecar; .\.venv\Scripts\ruff.exe check .; & $PY -m pytest -q
cd ..;             npm run lint --if-present; npm run test:unit --if-present
```

`cargo fmt --check`가 가장 자주 떨어진다. clippy는 `--all-targets -- -D warnings`로 돌려야 CI와 같다.

### 커밋 전 검사 — bash/macOS (CI 미러 원본, dev 병합 2026-08-29)

`.github/workflows/pr-check.yml`에 정의된 4개 잡(`rust-check`, `python-check`, `frontend-check`, `flutter-check`)을 그대로 미러링한다. **커밋 전 영역별로 해당 명령을 직접 돌려 통과 확인.** 빠뜨리고 푸시하면 GitHub Actions에서 떨어진다.

#### Rust (`apps/desktop/src-tauri/`)

```bash
cd apps/desktop/src-tauri
cargo fmt --check                          # 또는 자동 적용: cargo fmt
cargo clippy --all-targets -- -D warnings  # -D warnings = 경고를 에러로 승격
```

- `cargo fmt --check` 가 가장 자주 떨어지는 항목 — fmt 기본 스타일과 다른 코드를 그대로 푸시하면 즉시 실패.
- `cargo clippy --no-deps` 만 돌리면 안 됨 — CI는 `--all-targets -- -D warnings`라서 테스트 코드의 경고까지 잡힘.
- (참고) CI는 `binaries/office-claw-sidecar-*` 더미 파일을 만들고 clippy를 돌린다. 로컬은 PyInstaller 산출물이 있으면 그걸 쓰고, 없으면 동일하게 더미를 만들거나 `cargo check`로 컴파일 가능 여부만 봐도 됨.

#### Python (`services/sidecar/`)

```bash
cd services/sidecar
uvx ruff check .                           # lint
uv run pytest -q                           # unit tests
```

- `uv`가 없으면: `brew install uv` (또는 `astral.sh/setup-uv`의 설치 스크립트).
- 의존성 변경 시 `uv sync --frozen --extra dev` 한 번 더 (CI는 lockfile 고정).
- macOS 로컬은 OS Keychain 백엔드가 있어 keyring 호출이 실제 동작 — CI는 `PYTHON_KEYRING_BACKEND=keyring.backends.null.Keyring` 환경변수로 no-op 처리한다는 점이 다름. 테스트가 OS Keychain에 부수효과를 남기지 않는지 확인할 것.

#### Frontend (`apps/desktop`)

```bash
cd apps/desktop
npm ci                                     # CI와 동일하게 lockfile 고정 설치
npm run lint --if-present                  # lint 스크립트 존재 시
npm run test:unit --if-present             # 현재: node --test src/lib/*.test.js
```

- 빠른 확인이면 `npm run build`만 돌려도 import 경로 깨짐은 잡힘.

#### Flutter 모바일 (`apps/mobile`)

```bash
cd apps/mobile
flutter pub get --enforce-lockfile         # CI와 동일하게 lockfile 고정 설치
flutter analyze                            # 정적 분석 (경고도 실패로 잡힘)
flutter test                               # unit tests
```

- CI는 Flutter **3.44.6**으로 고정돼 있다. 로컬 SDK가 다르면 analyze 결과가 갈릴 수 있으니 `flutter --version`으로 맞출 것. 올릴 때는 `pr-check.yml`의 `flutter-version`과 같이 올린다.
- `dart format --set-exit-if-changed`는 **CI에 넣지 않았다** — 기존 파일 다수가 이미 미준수라 켜는 순간 전부 빨개진다. 넣으려면 먼저 `dart format .`으로 전체를 한 번 정리하는 별도 커밋이 필요하다. 그전까지는 새로 만드는 파일만 `dart format <파일>`로 맞춘다.
- 빌드(APK/IPA)는 CI에서 돌리지 않는다. iOS 빌드는 macOS 러너가 필요하고 서명까지 얽혀서 PR 게이트에는 과하다.

#### 한 번에 다 — 추천 alias

`.zshrc` / `.bashrc`에:

```bash
alias oc-precheck='cd <저장소 경로> && \
  (cd apps/desktop/src-tauri && cargo fmt --check && cargo clippy --all-targets -- -D warnings) && \
  (cd services/sidecar && uvx ruff check . && uv run pytest -q) && \
  (cd apps/desktop && npm run test:unit --if-present) && \
  (cd apps/mobile && flutter analyze && flutter test)'
```

PR 만들기 직전 `oc-precheck` 한 번 — 넷 다 통과하면 CI도 통과.

### 로그 보기

`logs/chat_log.jsonl`을 에디터로 직접 열지 말 것 — 한 턴이 2KB짜리 한 줄이라 눈으로 못 쫓는다.

```powershell
& $PY services\sidecar\scripts\show_turns.py -n 5                 # 최근 5턴
& $PY services\sidecar\scripts\show_turns.py --failed -n 8        # 깨진 턴만
& $PY services\sidecar\scripts\show_turns.py --follow             # 실시간
& $PY services\sidecar\scripts\show_turns.py --grep "정렬" -n 3   # 문장으로 찾기
```

한 턴에 모델을 여러 번 부르는 경로(재계획·관측 루프)는 `show_turns.py`로 부족하다 — **첫 호출만** 보여 준다.

```powershell
& $PY services\sidecar\scripts\dump_turn_llm_calls.py logs\diagnostics\<실행id>.jsonl <turn_id> logs\turn.txt
```

### 진행률 보기

Ollama를 부르는 긴 작업(평가·진단)의 남은 시간:

```powershell
.\scripts\watch-eval.ps1              # 실시간, 10초마다 갱신
.\scripts\watch-eval.ps1 -Once        # 한 번만
.\scripts\watch-eval.ps1 -Total 154   # 건수 지정
```

### 야간 게이트 — **세션을 시작하면 이것부터 본다**

매일 03:00에 pytest·파괴 게이트 72·말투 게이트 624가 자동으로 돈다(약 70분).
결과는 `logs/nightly/LATEST.md`에 남고, 기준선(`config/gate_baseline.json`)보다
**나빠지면 맨 위에 ❌와 항목이 뜬다. 그러면 그게 그 세션의 첫 작업이다.**

```powershell
Get-Content logs\nightly\LATEST.md -TotalCount 20   # 세션 시작 시
.\scripts\nightly-gates.ps1 -Only guard             # 손으로 하나만
.\scripts\nightly-gates.ps1 -UpdateBaseline         # 좋아진 값을 기준선으로 승격
.\scripts\nightly-gates.ps1 -Register / -Unregister # 예약 등록·해제
```

기준선은 **좋아졌을 때만** 올린다. 나빠진 값을 기준선으로 내리면 이 체계가 통째로
무용지물이 된다. `silent_max`(미검출 오실행)는 0에서 절대 올리지 않는다.

> 게이트와 대화 배터리는 **동시에 돌면 안 된다**(결과가 뒤섞인다).
> 야간 게이트는 `logs/nightly/.running.lock`으로 겹침을 막지만, 배터리를 손으로
> 돌릴 때는 사람이 시간을 피해야 한다.

### 측정

```powershell
cd services/sidecar
& $PY scripts\run_command_diagnostics.py -n 3 --label before-<작업이름>   # 고치기 전
& $PY scripts\run_command_diagnostics.py -n 3 --label after-<작업이름>    # 고친 뒤
```

플래너 모델 회귀 평가(154건 × 2모델, 약 15분):

```powershell
cd services/sidecar
& $PY scripts\eval_ax7b_shadow.py --input-jsonl ..\..\datasets\eval\planner_eval_v1.jsonl `
  --output-json ..\..\logs\eval_shadow.json `
  --baseline-model ax7bplanner-v3:latest --candidate-model <후보>
& $PY scripts\eval_release_gate.py --shadow-report ..\..\logs\eval_shadow.json `
  --output-json ..\..\logs\eval_gate.json --thresholds-json config\planner_gate_thresholds.json
```

---

## 3. 에이전트 동작을 고칠 때 — 재고 나서 고친다

로컬 LLM은 같은 문장에 같은 계획을 준다는 보장이 없다. 한 번 본 실패를 코드에서 찾기
시작하면 원인이 모델의 변덕일 때 끝없이 헤맨다. **§2의 before/after 측정을 먼저 돌린다.**

1. **주장을 코드에 대조하고 나서 고친다.** 이미 구현된 기능을 가리키는 지적이 잦다.
2. **모델 문제와 코드 문제를 가른다.** `--prompt`로 그 턴에 모델이 실제로 본 통합문서
   상태를 확인한다. 머리글을 보여 줬는데도 없는 열을 골랐다면 관측이 아니라 모델 문제다.
3. **모델을 탓하기 전에 `[ROUTE]`를 본다.** `quick_rule:hit`이면 그 턴에는 LLM이 아예
   호출되지 않았다. 프롬프트·모델을 아무리 고쳐도 그 경로는 안 바뀐다. 규칙이 문장의
   일부를 표현하지 못하면 `_quick_plan_underfits_message`에서 놓게 한다.
4. **구조를 의심하기 전에 학습 리터럴을 세어 본다.** 모델이 없는 열·없는 범위를 고르면
   그 문자열이 학습셋에 그대로 있는지 먼저 본다.

   ```powershell
   Select-String -Path datasets\train\planner_sft_v3_train.jsonl -Pattern "<이상한 문자열>" |
     Measure-Object | % Count
   ```

   실측 예: `L2:L181`이 1000건 중 81건(8.1%). 관측 결과를 프롬프트에 넣어 줘도 **바이트
   단위로 같은 답**이 나온다 — 하네스를 아무리 고쳐도 안 바뀌는 종류다.
5. **프롬프트를 바꾸면 반드시 전후를 잰다.** `build_planner_prompt`는 SFT 데이터 생성과
   **같은 함수**다. 문구를 바꾸면 이미 학습된 모델은 본 적 없는 형식을 받는다.
6. **실험 팔을 비교하기 전에 세 팔이 같은 조건인지 본다.** 일부 케이스가 `quick_rule:hit`이라
   LLM에 가지도 않으면 무엇을 바꿔도 안 움직인다. 규칙·폴백 수정은 **팔이 아니라 하네스**이므로
   모든 팔에 적용하고 기준선을 다시 잰다.
7. **API의 자기보고를 믿지 말고 결과 워크북을 연다.** 사이드카는 실패를 예외가 아니라
   `ok:false`로 알린다. 성공했다는 응답과 실제 파일이 다른 사례가 실측으로 나왔다.

---

## 4. 코드 원칙 — 재사용 가능한 모듈로 쓴다

**한 컴포넌트/파일에 로직을 인라인으로 쏟아붓지 않는다.**

1. **상태는 모듈이 소유한다.** 도메인 로직과 그 상태를 한 모듈에 묶고, UI는 구독해서 읽기만 한다.
2. **표시와 데이터를 분리한다.** 토큰/타입은 `src/lib/*.js`, 공용 UI는 `src/components/ui/*.jsx`,
   페이지 UI는 그 둘을 조합만 한다.
3. **새 도메인이면 모듈 세트를 만든다.** `store/<domain>Store.js` → `lib/<domain>Manager.js` →
   `hooks/use<Domain>.js` → 그다음에 UI.
4. **중복 fetch 금지.** 같은 데이터를 여러 컴포넌트가 각각 가져오지 않는다.
5. **Rust도 같다.** `apps/desktop/src-tauri/src/`에 도메인별 모듈, IPC는 `ipc.rs`에서 얇게 노출.

### 안티패턴

- 컴포넌트 안에서 `fetch`/`invoke` 직접 호출
- 같은 상태를 여러 컴포넌트가 각자 들고 있기
- UI 컴포넌트 안에 비즈니스 로직
- 한 파일에 여러 도메인 섞기

### 기존 모듈 지도 — 새 기능을 넣으면 여기 한 줄이 늘어야 한다

| 도메인 | Store / Lib | Python 서비스 | UI |
|---|---|---|---|
| 시스템 상태 | `store/statusStore.js`, `lib/statusManager.js` | — | `components/ui/status.jsx` |
| Tauri IPC | `lib/api.js` (invoke 래퍼 1곳) | — | — |
| OS 자격증명 · 감사 | api.js의 `rustCredential*` / `rustAudit*` | `keyring_service.py` · `audit_service.py` | — |
| Excel 계획 수립 | — | `excel_planner_prompt.py` · `excel_tool_schemas.py` · `excel_live_agent.py` | — |
| Excel 실행 | — | `excel_live_service.py`(xlwings) · `excel_live_file_service.py`(openpyxl) | WorkspacePage |
| 파라미터 확정 | — | `excel_param_binder.py` — 상징 파라미터를 실제 좌표로 | — |
| 실패 보정 | — | `excel_step_repair.py` — 재시도 전 파라미터 교정 | — |
| 결과 검증 | — | `excel_result_verifier.py` — 워크북을 다시 읽어 사후조건 확인 | — |
| 고수준 분해 | — | `excel_macro_planner.py` — 한 문장을 하위 명령들로 | — |
| 턴 트레이스 | — | `decision_trace.py` · `trace_report.py` · `trace_digest.py` | `scripts/show_turns.py` |

> **LLM 경로**: Ollama OpenAI 호환 API(`/v1/chat/completions`) 단일 경로.
> 계획은 `planner_model`(`ax7bplanner-*`, 계획 JSON 전용 SFT), 고수준 분해와 일반 대화는
> `model`(에이닷 `ax4-light`)이 맡는다 — 플래너는 문장을 펼치는 태스크에 맞지 않는다.
> 권한(SAFE/CONFIRM/DENIED)은 `tool_registry.py`가 소유한다.

---

## 5. 한국어

- 코드 주석 · UI 문자열 · 커밋 메시지 · 개발일지: **한국어**
- 변수/함수/타입명: 영어 (kebab-case 파일명, camelCase JS, snake_case Rust)
