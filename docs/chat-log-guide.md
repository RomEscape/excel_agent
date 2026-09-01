# chat_log.jsonl 읽는 법 — 판단 블랙박스 안내서

> 대상 독자: 이 프로젝트를 처음 보는 사람. 로그로 "AI가 왜 그렇게 했는지"를
> 재구성하고, 사용자 불만을 개선으로 잇는 방법까지 다룬다.

## 1. 이 파일이 뭔가요?

`logs/chat_log.jsonl`은 김대리의 **판단 블랙박스**다. 사용자가 명령 한 번을
보낼 때마다(= 한 **턴**) 사이드카가 "무엇을 받았고, 어떤 경로로 판단했고,
실제로 무엇을 실행했는지"를 **JSON 한 줄**로 남긴다.

- 한 턴 = 한 줄. 한 줄이 보통 2KB가 넘는다 → **에디터로 직접 열지 말 것**
  (눈으로 못 쫓는다. 아래 4장의 도구를 쓴다).
- 앱(GUI)·테스트 배터리·야간 게이트가 **전부 같은 파일**에 쌓인다. 누가 남긴
  턴인지는 `source` 필드로 가른다(사람이 친 명령만 보려면 `--human`).
- 64MB가 넘으면 자동 로테이션된다: `chat_log.jsonl` → `chat_log.20260831-054140.jsonl`
  처럼 시각이 붙은 파일로 밀려나고 새 파일이 시작된다. **이력은 지워지지 않는다.**

## 2. 한 줄(턴)의 해부 — 13개 키

| 키 | 뜻 | 초보자 설명 |
|---|---|---|
| `at` | 시각 | 이 턴이 언제 있었나 |
| `turn_id` / `session_id` | 턴·대화 식별자 | 같은 대화의 턴들을 묶는 이름표 |
| `endpoint` | 들어온 문 | `excel-live/command`(엑셀 명령) · `excel-live/approval`(확인 카드 승인) · `agent/chat`(일반 대화) |
| `user_input` / `message` | 사용자 문장 | 사람이 실제로 친 말(원문 그대로) |
| `source` | 출처 | 사람인지, 어느 테스트 스크립트인지(`blind_gate` 등) |
| `request` | 요청 부속 | 어느 워크북·시트·선택 영역에서 보냈나 |
| `routes` | **판단 경로 요약** | "어느 갈림길로 갔나" — 가장 먼저 볼 것(5장) |
| `stages` | 단계별 상세 | 각 층이 무엇을 보고 무엇을 만들었나(5장) |
| `outcome` | 결과 | 성공/실패, 최종 액션, 실제 실행 내역(`diag.xlwings_ops`) |
| `elapsed_ms` | 소요 시간 | 이 턴이 몇 ms 걸렸나 |
| `origin` | 실행 환경 | 어느 기기/모드에서 온 턴인지 |

## 3. 어떻게 쌓이나요?

```
사용자 문장 → 사이드카 엔드포인트 → (판단 층들) → 실행 → 응답
                      │
                      └── 턴이 끝나는 순간, 위 13키를 채운 JSON 한 줄 append
```

- 확인 카드가 뜨는 명령은 **두 줄**이 남는다: 계획을 세운 턴
  (`approval:required`)과, 사용자가 승인해 실행한 턴(`approval:resumed`).
- 게이트·배터리는 `BLIND_SESSION_TAG` 같은 태그로 `session_id`에 표식을 남겨
  사람 턴과 섞이지 않게 한다.

## 4. 보는 도구 — 복붙용

```powershell
$PY = "$env:LOCALAPPDATA\officeclaw\venvs\python-sidecar\Scripts\python.exe"
& $PY services\sidecar\scripts\show_turns.py -n 5              # 최근 5턴 요약
& $PY services\sidecar\scripts\show_turns.py --failed -n 8     # 깨진 턴만
& $PY services\sidecar\scripts\show_turns.py --grep "정렬"      # 문장으로 찾기
& $PY services\sidecar\scripts\show_turns.py --human -n 10     # 사람 턴만(테스트 제외)
& $PY services\sidecar\scripts\show_turns.py --follow          # 실시간 감시
```

한 턴에 모델을 여러 번 부르는 경로(재계획·관측 루프)는 `show_turns`가 **첫
호출만** 보여 준다. 더 깊게 볼 때:

```powershell
& $PY services\sidecar\scripts\dump_turn_llm_calls.py logs\diagnostics\<실행id>.jsonl <turn_id> logs\turn.txt
```

## 5. 판단 경로 읽는 법 — 개선의 절반

### routes: 갈림길 요약

```json
"routes": [
  {"at": "quick_rule:hit",  "why": "규칙이 excel_live.sort_range로 확정"},
  {"at": "approval:required", "action": "excel_live.sort_range"},
  {"at": "final:approval_required"}
]
```

**가장 중요한 한 줄**: `quick_rule:hit`이면 그 턴에는 **LLM이 아예 호출되지
않았다.** 프롬프트나 모델을 아무리 고쳐도 그 경로는 안 바뀐다 — 고칠 곳은
규칙이다. 반대로 `quick_rule:miss → planner:local`이면 모델이 계획을 냈다.

### stages: 층별 상세 (순서대로)

| 단계 | 무엇을 하나 |
|---|---|
| `understand` / `rules` | 결정론 규칙이 문장을 훑고 퀵 계획을 시도 |
| `observation` | 워크북을 실제로 읽어 머리글·범위 파악(모델에게 줄 사실) |
| `llm_call` (`intent_normalizer`) | 의도층 — 문장을 task로 분류 |
| `llm_call` (`planner`) + `planner` | 플래너 모델의 계획(JSON) |
| `binder` | 상징 파라미터를 실제 좌표로 확정 |
| `plan_final` | 교정기·가드를 거친 **최종 계획** — 실행될 그것 |
| `executed` | 단계별 실행 결과(ok/verified/error) |

`planner`에는 옳게 있던 값이 `plan_final`에서 달라졌다면, 그 사이의
교정기/슬롯/가드 층이 바꾼 것이다 — 로그만으로 범인 층을 지목할 수 있다.

## 6. 사용자의 AI 문제를 로그로 보고받고 개선하는 루프

```
① 증상 접수 → ② 턴 찾기 → ③ 원인 층 가르기 → ④ 파일로 검증 → ⑤ 고치고 재기 → ⑥ 기록
```

1. **증상은 사용자 표현 그대로** 받아 적는다("정렬했는데 합계줄이 섞여요").
   요약하는 순간 단서가 사라진다.
2. **턴 찾기**: `show_turns.py --grep "합계줄"` 또는 `--human --failed`.
3. **원인 층 가르기** — routes·stages로 아래 분류표를 탄다:

   | 로그에서 보이는 것 | 범인 층 | 고칠 곳 |
   |---|---|---|
   | `quick_rule:hit` + 틀린 계획 | 결정론 규칙 | 라우터 퀵룰(규칙이 못 푸는 문형이면 **놓게** 한다) |
   | intent `unmapped`/오매핑 | 의도층 | `excel_intent_normalizer.py` |
   | `planner`의 action_plan부터 틀림 | 모델/프롬프트 | 학습 리터럴부터 센다(아래 주의 2) |
   | `planner`는 옳은데 `plan_final`이 다름 | 교정기·슬롯·가드 | 로그의 trace_note가 어느 교정기인지 말해 준다 |
   | `executed`에서 ok:false | 실행기 계약 | 파라미터 계약(예: sheet_name 필수) |
   | 성공 보고인데 결과가 이상 | 검증기 맹점 | 오라클/사후조건 보강 |

4. **자기보고를 믿지 말고 파일을 연다.** 사이드카는 "계획대로 실행되면"
   성공이라 한다 — 계획 자체가 틀려도. 결과 워크북 전수 감사:

   ```powershell
   & $PY services\sidecar\scripts\audit_result_workbooks.py
   ```

5. **고치면 반드시 전/후를 잰다.** 한 번 본 실패로 코드를 고치면 원인이 모델의
   변덕일 때 끝없이 헤맨다. 부분 게이트(`scripts/run_blind_subset.py`)로 그
   과제만 재고, 회귀는 핀(`tests/test_battery_regressions.py`)으로 못박는다.
6. **개발일지에 남긴다** — 증상(원문)·원인(파일:줄)·조치·실측 수치·실행 id.
   기록하지 않은 측정은 다음 사람이 처음부터 다시 재게 만든다.

### 주의 두 가지

- **모델을 탓하기 전에 routes를 본다.** `quick_rule:hit`이면 모델 문제가 아니다.
- **모델이 없는 열·범위를 지어내면 학습셋부터 검색한다.** 그 문자열이 학습
  데이터에 그대로 있으면 하네스를 고쳐도 안 바뀌는 종류다:

  ```powershell
  Select-String -Path datasets\train\planner_sft_v3_train.jsonl -Pattern "<이상한 문자열>" | Measure-Object | % Count
  ```

## 7. 실전 예제 — 실제로 이렇게 잡았다 (2026-09-01)

증상: 사용자가 "안녕하세요!"라고 인사했는데 앱이 **워크북 목록 조회**를 했다.

로그 판독(실측 그대로):

```json
"routes": [{"at": "quick_rule:miss", "why": "규칙으로 확정하지 못해 플래너로 넘김"}]
"stages": [
  {"stage": "llm_call", "purpose": "intent_normalizer", "outcome": "unmapped", "task": "other"},
  {"stage": "planner", "intent": "navigate", "action_plan": [{"action": "excel_live.list_workbooks"}]}
]
```

읽는 법: 의도층은 "엑셀 일이 아니다(other)"라고 **옳게** 판단했는데, 그 다음이
계획 전용 플래너라 인사에도 억지 계획을 냈다. 범인은 모델이 아니라 **라우팅**
— "엑셀 일이 아니면 일반 대화로 보내는 문"이 좁았던 것. 입구 게이트에 스몰토크
판정을 넓혀 봉합했고, 게이트 코퍼스 696문이 안 뺏기는 것까지 핀으로 못박았다.

## 8. 상시 감시 — 사람이 안 봐도 잡히게

매일 03:00 야간 게이트가 pytest·파괴 72문장·말투 624문장을 돌리고
`logs/nightly/LATEST.md`에 기준선 대비 판정을 남긴다. **맨 위에 ❌가 뜨면 그게
그 세션의 첫 작업이다.** 특히 `미검출 오실행(silent)`은 0에서 절대 올리지
않는다 — 사용자가 눈치 못 채는 오실행이야말로 이 로그 체계가 잡으려는 것이다.
