# Office-Claw 프로젝트 규칙

## 코드 작성 원칙 — 모듈/객체지향

**모든 새 기능은 재사용 가능한 모듈로 작성하고 다른 곳에서 활용한다.** 한 컴포넌트/파일에 로직을 인라인으로 쏟아붓지 않는다.

### 구체적 가이드라인

1. **상태는 모듈이 소유한다.** 도메인 로직과 그 상태는 한 모듈 안에 묶는다. UI는 그 모듈을 구독해서 읽기만 한다.
   - 예: `src/lib/statusManager.js`의 `STATUS_MODULES.openclaw`는 자기 상태(`store.modules.openclaw`)와 액션(`check/install/start`)을 모두 소유한다. Dashboard·StatusBar·Sidebar·LocalAISetupWizard는 같은 모듈을 구독만 한다.

2. **표시(presentation)와 데이터(data)를 분리한다.**
   - 데이터 토큰/타입 → `src/lib/*.js` (예: `statusTokens.js`)
   - 데이터 매핑/액션 → `src/lib/*.js` (예: `statusManager.js`)
   - 공용 UI primitive → `src/components/ui/*.jsx` (예: `status.jsx`의 `StatusDot/StatusBadge/StatusRow/StatusBanner`)
   - 페이지/도메인 UI → `src/components/<domain>/*.jsx` — 위 3개를 조합만 한다

3. **새 도메인이 생기면 모듈 세트를 만든다.**
   - `src/store/<domain>Store.js` — Zustand store
   - `src/lib/<domain>Manager.js` — 액션 (check/start/stop/refresh 등)
   - `src/hooks/use<Domain>*.js` — React hook (필요 시)
   - 그다음에 UI 작성

4. **중복 fetch를 피한다.** 같은 데이터를 여러 컴포넌트가 각각 fetch하지 않는다 — 중앙 store/manager에 모으고 구독한다.

5. **Rust 측도 같은 원칙.** `src-tauri/src/`에 도메인별 모듈 (`openclaw.rs`, `ollama.rs`, `installer.rs`). 각 모듈은 자기 상태(필요 시 `State<Mutex<...>>`)와 그 상태에 대한 함수만 소유. IPC는 `ipc.rs`에서 얇은 wrapper로 expose.

### 안티패턴 (피해야 할 것)

- 컴포넌트 안에 `fetch`/`invoke` 직접 호출 (status fetch 등 도메인 로직)
- 같은 상태를 여러 컴포넌트가 각자 state로 들고 있기
- UI 컴포넌트 안에 비즈니스 로직 (모델명 검증, 톤 결정 등)
- 한 파일에 여러 도메인의 로직 섞기

### 좋은 예시 (이 프로젝트의 기존 패턴)

| 도메인 | Store | Manager/Lib | Rust 모듈 | UI primitive |
|---|---|---|---|---|
| 시스템 상태 | `store/statusStore.js` | `lib/statusManager.js`, `lib/statusTokens.js` | — | `components/ui/status.jsx` |
| 로컬 AI 설정 단계 | — | `lib/localAISetup.js` (buildPlan/isAllReady) | — | `components/guide/LocalAISetupWizard.jsx` (조합만) |
| Tauri IPC | — | `lib/api.js` (모든 invoke wrapper 1곳) | `src-tauri/src/ipc.rs` | — |
| OS 자격증명 | — | api.js의 `rustCredential*` | `src-tauri/src/keyring_svc.rs` | — |
| 감사 로그 | — | api.js의 `rustAudit*` | `src-tauri/src/audit.rs` | — |
| Excel tool-calling | — | (sidecar) `services/excel_tool_schemas.py`(함수 명세) · `excel_tool_agent.py`(루프) · `excel_actions.py`(실행) | — | WorkspacePage 채팅 (조합만) |
| 턴 트레이스·진단 | — | `services/decision_trace.py`(기록) · `trace_report.py`(한 턴 펼치기) · `trace_digest.py`(여러 턴 접기) | — | `scripts/show_turns.py` · `run_command_diagnostics.py` (조합만) |
| 결과 상태 채점 | — | `tests/excel_e2e/command_battery.py`의 `expect_effect` 오라클 (결과 워크북을 직접 열어 본다) | — | `run_command_diagnostics.py`가 `.report.json`에 기록 |
| 수량 한정어 해석 | — | `services/excel_rank_limit.py` — `detect`("몇 개를 어느 쪽으로")와 `resolve_step`(N번째 값을 파일에서 읽어 기준값으로) | — | 라우터의 목표 누락 가드가 조합만 |
| 관측 모드 (실험) | — | `services/excel_observation.py` — `off`/`read_first`/`loop` 삼항 스위치와 그 판정(`allows_read_first`·`truncate_at_observation`·`should_replan_after_observation`·`render_observation`) | — | 라우터 실행 루프와 `excel_live_agent`가 조합만 |

새 기능을 추가할 때 이 표에 한 줄이 더 늘어나야 한다.

> **2026-05 Rust 보안 계층 노트**: Keyring · Audit 두 도메인은 Python sidecar의 동명 서비스와 *같은* OS Keychain·파일(`audit.jsonl`, `credentials_registry.json`)을 공유한다. 신규 코드는 Rust 경로(`rustCredential*`, `rustAudit*`)를 우선 사용하되, Python 측은 자체 라우터 안에서 자기 서비스를 계속 쓴다.
>
> **2026-07 LLM 경로 노트**: OpenClaw 게이트웨이 통합은 `feat/ollama-tool-calling`에서 전면 제거됐다. LLM 호출은 Ollama OpenAI 호환 API(`/v1/chat/completions`) + `tools`(function calling) 단일 경로다. Excel 함수 명세는 `excel_tool_schemas.py`가 단일 소스이며, 권한(SAFE/CONFIRM/DENIED)은 `tool_registry.py`가 계속 소유한다.

## 에이전트 동작을 고칠 때 — 로그 먼저

플래너·검증기·라우터 동작을 바꾸기 전에 **먼저 재고 나중에 고친다.** 로컬 LLM은
같은 문장에 같은 계획을 준다는 보장이 없어서, 한 번 본 실패를 코드에서 찾기
시작하면 원인이 모델의 변덕일 때 끝없이 헤맨다.

```bash
cd python-sidecar
uv run python scripts/run_command_diagnostics.py -n 3 --label before-<작업이름>
# 고친 뒤
uv run python scripts/run_command_diagnostics.py -n 3 --label after-<작업이름>
uv run python scripts/show_turns.py --log ../logs/diagnostics/<실행id>.jsonl --source <케이스> --prompt
```

앱을 켜 놓고 직접 명령을 쳐 보는 중이라면 실시간으로 흘려 본다. `logs/chat_log.jsonl`을
에디터에서 그대로 읽지 말 것 — 한 턴이 2KB짜리 한 줄이라 눈으로 못 쫓는다.

```bash
cd python-sidecar
uv run python scripts/show_turns.py --follow                          # 터미널
uv run python scripts/show_turns.py --follow --out ../logs/turns.txt  # 에디터에 열어 두기
uv run python scripts/show_turns.py --follow --failed                 # 깨진 턴만
```

한 턴에 모델을 여러 번 부르는 경로(재계획·관측 루프)를 볼 때는 `show_turns.py`로
부족하다 — 요약하느라 **첫 호출만** 보여 준다. 두 번째 호출이 무엇을 보고 무엇을
냈는지가 정작 알고 싶은 것이므로 이걸 쓴다.

```bash
uv run python scripts/dump_turn_llm_calls.py ../logs/diagnostics/<실행id>.jsonl <turn_id> ../logs/turn.txt
```

지킬 것:

1. **주장을 코드에 대조하고 나서 고친다.** 외부 지적이 이미 구현된 기능을 가리키는
   경우가 잦다(2026-08-11에 9개 중 3개가 그랬다). 반증된 것도 근거와 함께 일지에 남긴다.
2. **모델 문제와 코드 문제를 가른다.** `--prompt`로 그 턴에 모델이 실제로 본
   통합문서 상태를 확인한다. 머리글을 보여 줬는데도 없는 열을 골랐다면 관측이
   아니라 모델 문제다.
3. **수치는 실측만 적는다.** 실행 id(`0811-171221-after-guards`)를 함께 남겨
   나중에 같은 로그로 재확인할 수 있게 한다. 지어낸 숫자는 이 체계를 통째로 무용지물로 만든다.
4. **프롬프트를 바꾸면 반드시 전후를 잰다.** `build_planner_prompt`는 SFT 데이터
 생성과 **같은 함수**다. 문구를 바꾸면 이미 학습된 모델은 본 적 없는 형식을 받는다.
5. **모델을 탓하기 전에 `[ROUTE]`를 본다.** `quick_rule:hit`이면 그 턴에는 LLM이
 아예 호출되지 않았다. 프롬프트·모델을 아무리 고쳐도 그 경로는 바뀌지 않는다
 (2026-08-11 "상위 3개"가 그랬다 — 규칙이 한정어를 버리고 열 전체를 칠했다).
 규칙이 문장의 일부를 표현하지 못하면 `_quick_plan_underfits_message`에서 놓게 한다.
6. **구조를 의심하기 전에 학습 리터럴을 세어 본다.** 모델이 없는 열·없는 범위를 고르면
 그 문자열이 학습셋에 그대로 있는지 먼저 본다. 2026-08-11 관측 실험에서 모델이 계속
 고른 `I2:I181`은 `datasets/train/planner_sft_v3_train.jsonl` 1000건 중 27건(2.7%)에
 리터럴로 들어 있었다. 관측 결과를 프롬프트에 넣어 줘도 **바이트 단위로 같은 답**이
 나왔다 — 하네스를 아무리 고쳐도 안 바뀌는 종류다.

 ```bash
 rg -c "<모델이 고른 이상한 문자열>" datasets/train/planner_sft_v3_train.jsonl
 ```
7. **실험 팔을 비교하기 전에 세 팔이 같은 조건인지 본다.** 일부 케이스가
 `quick_rule:hit`이라 LLM에 가지도 않으면, 무엇을 바꿔도 그 케이스는 안 움직인다.
 그 상태로 비교하면 "효과 없음"이라는 틀린 결론이 나온다. 규칙·폴백 수정은 **팔이
 아니라 하네스**이므로 모든 팔에 적용하고 기준선을 다시 잰다.

## 빌드/실행

- `npm run tauri:dev` — 전체 앱 (Rust + Vite + Tauri webview). 개발 시 기본.
- `npm run dev` — vite-only. UI 레이아웃만 빠르게 확인할 때.
  주의: `invoke()` 호출은 모두 실패한다 (Tauri runtime 없음 → "cannot read properties of undefined").
- Rust 변경 후에는 `tauri:dev`를 재시작해야 새 IPC 명령이 등록된다.

## 커밋/푸시 전 체크 (CI 미러)

`.github/workflows/pr-check.yml`에 정의된 3개 잡(`rust-check`, `python-check`, `frontend-check`)을 그대로 미러링한다. **커밋 전 영역별로 해당 명령을 직접 돌려 통과 확인.** 빠뜨리고 푸시하면 GitHub Actions에서 떨어진다.

### Rust (`src-tauri/`)

```bash
cd src-tauri
cargo fmt --check                          # 또는 자동 적용: cargo fmt
cargo clippy --all-targets -- -D warnings  # -D warnings = 경고를 에러로 승격
```

- `cargo fmt --check` 가 가장 자주 떨어지는 항목 — fmt 기본 스타일과 다른 코드를 그대로 푸시하면 즉시 실패.
- `cargo clippy --no-deps` 만 돌리면 안 됨 — CI는 `--all-targets -- -D warnings`라서 테스트 코드의 경고까지 잡힘.
- (참고) CI는 `binaries/office-claw-sidecar-*` 더미 파일을 만들고 clippy를 돌린다. 로컬은 PyInstaller 산출물이 있으면 그걸 쓰고, 없으면 동일하게 더미를 만들거나 `cargo check`로 컴파일 가능 여부만 봐도 됨.

### Python (`python-sidecar/`)

```bash
cd python-sidecar
uvx ruff check .                           # lint
uv run pytest -q                           # unit tests
```

- `uv`가 없으면: `brew install uv` (또는 `astral.sh/setup-uv`의 설치 스크립트).
- 의존성 변경 시 `uv sync --frozen --extra dev` 한 번 더 (CI는 lockfile 고정).
- macOS 로컬은 OS Keychain 백엔드가 있어 keyring 호출이 실제 동작 — CI는 `PYTHON_KEYRING_BACKEND=keyring.backends.null.Keyring` 환경변수로 no-op 처리한다는 점이 다름. 테스트가 OS Keychain에 부수효과를 남기지 않는지 확인할 것.

### Frontend (repo root)

```bash
npm ci                                     # CI와 동일하게 lockfile 고정 설치
npm run lint --if-present                  # lint 스크립트 존재 시
npm run test:unit --if-present             # 현재: node --test src/lib/*.test.js
```

- 빠른 확인이면 `npm run build`만 돌려도 import 경로 깨짐은 잡힘.

### 한 번에 다 — 추천 alias

`.zshrc` / `.bashrc`에:

```bash
alias oc-precheck='cd /Users/skim/Desktop/project/office_claw && \
  (cd src-tauri && cargo fmt --check && cargo clippy --all-targets -- -D warnings) && \
  (cd python-sidecar && uvx ruff check . && uv run pytest -q) && \
  npm run test:unit --if-present'
```

PR 만들기 직전 `oc-precheck` 한 번 — 셋 다 통과하면 CI도 통과.

## 한국어

- 코드 주석·UI 문자열·커밋 메시지: 한국어
- 변수/함수/타입명: 영어 (kebab-case 파일명, camelCase JS, snake_case Rust)
