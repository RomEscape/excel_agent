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

> 참고: `lefthook.yml`의 `devlog-guard` 훅이 이걸 강제하도록 돼 있지만, 지금 이 환경에서는
> `uv`가 없어 동작하지 않는다(§2 참조). 훅이 막아 주지 않아도 규칙은 그대로다.

---

## 2. 명령어 — 복붙용

### 이 환경의 파이썬

시스템 파이썬도 `uv`도 없다. **동작하는 인터프리터는 하나뿐이다.**

```powershell
$PY = "$env:LOCALAPPDATA\officeclaw\venvs\python-sidecar\Scripts\python.exe"
$env:PYTHONUTF8 = "1"
```

문서의 `uv run python X` 는 전부 `& $PY X` 로 바꿔 읽는다.
`uv`를 복구하려면: `powershell -c "irm https://astral.sh/uv/install.ps1 | iex"`

### 실행

```powershell
npm run tauri:dev        # 전체 앱 (Rust + Vite + webview). 개발 시 기본
npm run dev              # vite만. UI 레이아웃 확인용 — invoke()는 전부 실패한다
```

- Rust를 고쳤으면 `tauri:dev` 재시작해야 새 IPC가 등록된다.
- **앱이 이미 떠 있으면 빌드가 실패한다**(`os error 5`: 실행 중인 exe를 못 지움). 먼저 닫는다.
- **사이드카는 고아가 된다.** `sidecar.rs`가 자식 프로세스 핸들을 버려서, 앱을 껐다 켜도
  포트 19532의 옛 사이드카가 살아남는다. **파이썬을 고쳤으면 사이드카를 직접 죽였다 켠다:**

```powershell
Get-CimInstance Win32_Process | ? { $_.CommandLine -match 'office_claw_sidecar' } |
  % { Stop-Process -Id $_.ProcessId -Force }
cd python-sidecar; & $PY -m office_claw_sidecar --port 19532 --auth-token dev-token
```

### 커밋 전 검사 (CI 미러)

```powershell
cd src-tauri;      cargo fmt --check; cargo clippy --all-targets -- -D warnings
cd python-sidecar; .\.venv\Scripts\ruff.exe check .; & $PY -m pytest -q
cd ..;             npm run lint --if-present; npm run test:unit --if-present
```

`cargo fmt --check`가 가장 자주 떨어진다. clippy는 `--all-targets -- -D warnings`로 돌려야 CI와 같다.

### 로그 보기

`logs/chat_log.jsonl`을 에디터로 직접 열지 말 것 — 한 턴이 2KB짜리 한 줄이라 눈으로 못 쫓는다.

```powershell
& $PY python-sidecar\scripts\show_turns.py -n 5                 # 최근 5턴
& $PY python-sidecar\scripts\show_turns.py --failed -n 8        # 깨진 턴만
& $PY python-sidecar\scripts\show_turns.py --follow             # 실시간
& $PY python-sidecar\scripts\show_turns.py --grep "정렬" -n 3   # 문장으로 찾기
```

한 턴에 모델을 여러 번 부르는 경로(재계획·관측 루프)는 `show_turns.py`로 부족하다 — **첫 호출만** 보여 준다.

```powershell
& $PY python-sidecar\scripts\dump_turn_llm_calls.py logs\diagnostics\<실행id>.jsonl <turn_id> logs\turn.txt
```

### 진행률 보기

Ollama를 부르는 긴 작업(평가·진단)의 남은 시간:

```powershell
.\scripts\watch-eval.ps1              # 실시간, 10초마다 갱신
.\scripts\watch-eval.ps1 -Once        # 한 번만
.\scripts\watch-eval.ps1 -Total 154   # 건수 지정
```

### 측정

```powershell
cd python-sidecar
& $PY scripts\run_command_diagnostics.py -n 3 --label before-<작업이름>   # 고치기 전
& $PY scripts\run_command_diagnostics.py -n 3 --label after-<작업이름>    # 고친 뒤
```

플래너 모델 회귀 평가(154건 × 2모델, 약 15분):

```powershell
cd python-sidecar
& $PY scripts\eval_ax7b_shadow.py --input-jsonl ..\datasets\eval\planner_eval_v1.jsonl `
  --output-json ..\logs\eval_shadow.json `
  --baseline-model ax7bplanner-v3:latest --candidate-model <후보>
& $PY scripts\eval_release_gate.py --shadow-report ..\logs\eval_shadow.json `
  --output-json ..\logs\eval_gate.json --thresholds-json config\planner_gate_thresholds.json
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
5. **Rust도 같다.** `src-tauri/src/`에 도메인별 모듈, IPC는 `ipc.rs`에서 얇게 노출.

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
