# 데모 브랜치 설계·측정 노트 (README에서 분리, 2026-09-06)

> `openclaw_jinh_demo` 브랜치의 운영 문서·측정 하네스·SFT 파이프라인 노트다. 사용 가이드가
> 아니라 **날짜별 설계 기록**이며, README를 사용자 안내 위주로 줄이면서 그대로 옮겨 왔다.
> 2026-09-06 실측 대조에서 아래는 **낡았거나 틀린 것으로 확인**됐다(개발일지 2026-09-06 참조).
>
> - 파이썬 명령은 전부 `cd services/sidecar && uv run python …` 기준으로 읽는다. 본문의
>   맨 `python …`·`& $PY …`는 옛 표기다(`$PY`는 이 문서 안에 정의가 없다).
> - `src/lib/requestPolicy.js` → `apps/desktop/src/lib/requestPolicy.js`.
> - `logs/diagnostics/<실행id>.jsonl` → 스크립트는 실제로 `services/logs/diagnostics/`에 쓴다.
> - `ax7bplanner-v5r`는 2026-08-20에 삭제됐다. 플래너는 v3를 유지한다.
> - 지원 액션 수는 16/26/49 세 벌로 적혀 있으나 `excel_live_plan_validator.SUPPORTED_ACTIONS`는 56(clarify 제외 55).
> - `logs/verifier_*.json`·`logs/eval_gate_*.json`은 gitignore 대상이라 저장소에 없다.
> - 완전 무관 입력은 HTTP 400이 아니라 `route_to_chat: true` 응답이다. 파싱 타임아웃 기본값은 8초가 아니라 10초.

# 데모 브랜치 부록 (openclaw_jinh_demo)

> 아래는 엑셀 에이전트 데모 브랜치의 운영 문서다 — 질문 예시·측정 하네스(배터리/블라인드
> 게이트/야간 게이트)·플래너 SFT 학습 파이프라인. 경로는 모노레포 기준
> (`services/sidecar/`, `apps/desktop/`)으로 갱신돼 있다. 규율의 원문은 CLAUDE.md가 소유한다.

## Excel Live 질문 예시

아래는 **사람이 실제로 치는 말투** 그대로 실측 통과한 문장들이다(2026-08-18,
사람 말투 배터리 48문장 · GUI 조건 ×3 반복 100%). 좌표·수식·함수 이름 없이도 된다.

**집계 — 표를 붙여넣고 말하면 된다**
- `합계를 표 아래에 한 줄로 넣어줘` / `여기다가 합 좀 밑에다 적어줄래?` / `밑에 합계 한줄 부탁해`
- `A7:F7 합계를 여기 위치에 열 별로 만들어줘` (붙여넣은 줄에 열별 합계)
- `A4에 지역성과 시트 주문건수 합계를 가져와줘` (다른 시트를 읽어 `=SUM('지역성과'!B2:B6)`)
- `컨트롤 c, 컨트롤 v 한 위치에 있는 모든 합을 밑에 기록해줘`

**서식**
- `첫줄 있잖아 그거 남색 배경으로 하고 글자는 흰색 굵게 부탁` / `머리글 진하게 해줘`
- `주문건수랑 출고건수 숫자에 콤마좀 찍어줘야 보기 편할듯`
- `표 전체 테두리좀 둘러줘` / `테두리는 빼고 배경만 노란색으로` / `빨간색 말고 노란색으로 칠해줘`
- `클레임 10 넘는 데만 빨갛게 칠해줘` / `상태가 대기인 애들만 분홍으로` / `미납인 건 빨간색으로`
- `매출이 3만 5천 원 이상이면 노란색` / `수익률이 마이너스인 종목 빨갛게`

**입력 — 좌표 대신 붙여넣기(2026-08-19 실측, GUI 흐름 그대로)**
- Excel에서 대상 범위(예: A1:F6)를 드래그해 Ctrl+C → 채팅창에 Ctrl+V(📋 "6행 × 6열 — A1:F6 범위로 인식했습니다") →
  뒤에 값과 동사만 이어 친다: `지역,주문건수,…; 수도권,10452,…; 충청권,… 입력해줘` (쉼표=칸, 세미콜론·줄바꿈=행).
  시트 이름·좌표를 문장에 쓰지 않는다. 한 칸(A1)만 잡고 복사해도 그 칸부터 쓴다.
- 다른 앱·통합문서에서 표를 복사해 붙여넣으면(선택 영역이 비어 있고 붙여넣은 표에 값이 있을 때) 값이 **탭·줄바꿈 그대로**
  살아서 붙고, `입력해줘`만 붙이면 그 자리에 쓴다("1,234", "서울, 경기"처럼 칸 안의 쉼표도 안전).
- 값을 빠뜨리고 `여기에 입력해줘`만 치면 "어떤 값을 넣을까요?"로 되묻는다(활성 셀을 덮지 않음). `이거 입력해줘: 지역,…`처럼 동사가 앞에 와도 된다.
- 값 안의 "철근 (D25)", "단열재 (T100)", "AI 물량 자동 산출", "필터 차압 상승" 같은 셀 닮은 토큰·계산어·서식어는 데이터로 본다.
- `E15에 A15에서 C15 뺀 값 넣어줘` / `F3에 B3랑 C3 더한 값` / `B8에 에너지_상세 시트 B2 값 가져와줘` — 수식 문자열 없이 두 셀 연산·다른 시트 한 칸 참조.
- (각본 전용) `A1:F6에 지역,주문건수,…; 수도권,10452,…; 충청권,… 입력` 좌표 문형도 여전히 된다.
- `B2:B4에 12,000, 8,500, 9,300 입력해줘` (천 단위 콤마 보존, 세로 범위는 열 따라 내려 씀)
- `아니 부산으로 바꿔줘` (직전에 쓴 칸 정정) / `아니 B2로 옮겨줘` (자리 이동)
- `여기 안에 차트 같은거 다 지워주고 셀 초기화 전체 해줘` / `표 서식만 지워줘, 값은 그대로 두고`
- `주문건수 많은 순으로 정렬해줘` (합계행은 맨 아래 고정) / `수도권 행만 남기고 나머지는 치워줘`

**차트 · 시트 · 기타**
- `정시배송률 가지고 선그래프 하나 뽑아줘` / `클레임 비중 도넛으로 보여줘` / `차트 다 지어줘`(오타도 처리)
- `백업2 시트를 만들어줄래?` / `요약이라는 이름으로 시트 추가좀` / `아직 저장하지 마`(부정문)
- `H2에 =AVERAGE(D2:D6) 넣어줘` / `F1에 =SEQUENCE(5) 수식 넣어줘` — 수식은 **무제한**(SEQUENCE·FILTER·XLOOKUP·LET 등 신형 함수와 자유 조합, 실계산 33/33 검증)

전체 재현 각본(8개 데모 대시보드를 대화만으로 만드는 41~61턴, **붙여넣기 흐름판**)은
`docs/사람말투_대화전록_8예시.md`(사람 → 모델 답변 전록, 각 5라운드 연속 100%), 각본 JSON은
`services/sidecar/scenarios/dialogue/dialogue_ex1~8.json`, 러너는 `services/sidecar/scripts/run_dialogue.py`(HUMAN_REPEAT=5).
옛 좌표판은 `docs/example*_재현_대화_*.md`·`docs/example1_사람말투판_45턴.md`.

**새 자료 14종(example_9~22) × 원문/변형 28본**(2026-08-19): 코드를 못 본 작성자 14명이 이미지만 보고 쓴 각본 —
`dialogue_ex9~22.json`(원문)과 `dialogue_ex9~22_v2.json`(같은 값·의도, 문장은 전부 다르게: 어순 도치·영어 혼용·군말·오타·존댓말).
첫 대화 → 오류 분석 → 수정 → 재대화를 4라운드 돌려 **1,780턴 중 1,774 통과(99.7%)**, 처음 만난 자료에서 잡은 루트 원인 20종은
`개발일지.md` 2026-08-19 항목과 `tests/test_battery_regressions.py::TestNewScenario*`에 핀으로 남겼다.
실패 턴 삼각측량: `scripts/dialogue_failures.py <dialogue_exN_log.json>` (러너 로그 + chat_log의 규칙·바인더·플래너 노트).

> 경계선 기본값은 **검정**이다. 더 얇게 원하면 `얇게 경계선 적용`처럼 지시하면 된다.

### 확신 3분기 — 모든 턴의 출구는 셋뿐이다 (2026-08-18)

3개월간 "새 표현 → 조용한 오답 → 스크린샷 → 수정" 루프가 반복된 구조적 원인은
"규칙이 놓친 문장을 로컬 모델이 **조용히 틀리게** 처리한다"는 것이었다. 그래서 출구를 셋으로 고정했다.

| 출구 | 조건 | 사용자가 보는 것 |
|---|---|---|
| ① 실행 | 규칙(`plan_source=rule`)이 확정 | 승인 카드 → 실행 리포트("액션 — 대상 · 규모") |
| ② 해석 카드 | 규칙 미스, **모델이 해석**한 계획 | **"이렇게 이해했어요 — 맞나요?"** (맞아요, 실행 / 아니에요) |
| ③ 되묻기 | 이해 불가·모호 | 무엇으로 이해했는지 밝히고 출구가 있는 질문 |

핵심은 **"규칙 미스 + 모델 확신 → 실행 금지"**다. 커버리지 구멍이 조용한 오염 대신
확인 질문으로 나타나므로, 그 자리에서 "응/아니"로 진행된다. 관련 안전장치:
셀 지목 없는 4낱말 넘는 문장은 값으로 쓰지 않음, 위험 한정사("서식만/중복된 행/필터")가
있으면 통째 삭제 금지, 계산 불가 조건("급증한/임박한")으로 칠하지 않음, 승인 팝업은
배경 클릭·Escape로 취소되지 않음, 같은 되묻기 2연속이면 슬롯을 버림.

### 강건성 게이트 — "한 번 되는 것"이 아니라 "여러 번 100%"

```powershell
cd services/sidecar
# 사람 말투 48문장 × 3회 반복 · GUI 조건(workbook_id 없음) · 파일 상태로 판정
$env:ROBUST_REPEAT="3"; $env:EXCEL_LIVE_ENGINE="file"; & $PY scripts\run_human_robustness.py
```

- 판정은 액션 보고가 아니라 **파일 상태**(값·수식·색·차트)다. 공통 불변식: 머리글 A1이 문장 텍스트로 오염되면 즉시 실패.
- `ROBUST_REPEAT=N`: 같은 문장을 N번 돌려 결과(성공 여부·액션)가 하나라도 흔들리면 **비결정 실패**.
- 배터리는 GUI와 같은 요청 형태(`workbook_id: null`, 승인은 `approval_id` 재개)로 돈다 — id를 명시한 러너들이 전부 통과했는데 GUI만 실패한 사각지대(2026-08-18)를 막는다.
- 대화 러너(`scripts/run_dialogue.py`)는 프론트 `handleSend`의 규칙(복합문 분리, "여기" 지시어에 붙여넣기 범위 접두, 문장에 범위가 없으면 **직전 결과 주소를 context_range로**, 붙여넣기 📋 = 선택 범위)까지 그대로 이식했다(2026-08-19). 배터리와 GUI가 다른 요청을 보내면 배터리는 아무것도 보증하지 않는다.
- 취약 문형 백로그: `docs/강건성_백로그_2026-08-18.md` (5렌즈 코드 정찰 70건, 근거·처리 현황 포함).
- 오타 정규화(`만들어조·함계·정열·테두르·차투·지어줘` 등)는 라우터 입구에서 일괄 적용된다.

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
    --output ../../datasets/distill/excel_escalation_harvest_v1.jsonl \
    --unsolved-output ../../logs/planner_unsolved.jsonl
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
python scripts/show_turns.py --grep "합계" -n 3   # 문장으로 찾기
python scripts/triage_real_usage.py     # 실사용 턴만 골라 실패 유형별 집계 (test- 세션 제외)
```

에이전트의 **답변도 전부 기록**된다(2026-08-18) — 되묻기 질문, 실행 리포트(`[REPLY]`),
승인 카드 문구(`[APPROVAL]`). "무엇을 추가로 물었는지"를 로그만으로 확인할 수 있다.
테스트·배터리는 세션 id를 `test-`로 시작해 실사용 통계에서 자동 제외된다.

테스트가 만든 턴도 누적할 수 있다. 기본값은 임시 디렉터리이고(실행마다 660여 턴이
실제 로그에 쌓이면 사람이 읽어야 할 기록이 묻힌다), 들여다볼 때만 켠다.

```powershell
$env:OFFICE_CLAW_TRACE_TESTS = "1"; uv run pytest -q
python scripts/show_turns.py --log ../../logs/test-runs/chat_log.jsonl --failed
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
cd services/sidecar
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
cd services/sidecar
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

승격 조건은 `services/sidecar/config/planner_gate_thresholds.json`에 근거와 함께 있다.
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
- `services/sidecar/scripts/smoke_excel_live_nl.py`
- `services/sidecar/scripts/smoke_excel_ko_hard_tasks.py` (한국어 고난도 작업 E2E/엔진 점검)
- `datasets/excel_complex_scenarios_v1.json` (복잡 작업 30시나리오 팩)
- `services/sidecar/scripts/verify_excel_complex_scenarios.py` (오라클 기반 자동 검증)

한국어 입력 우선 distillation 샘플 생성 예시:

```bash
cd services/sidecar
uv run python scripts/build_excel_distill_jsonl.py \
  --all-events ../../logs/all_events.jsonl \
  --preferred-locale ko \
  --drop-non-preferred-locale \
  --output ../../logs/excel_distill_ko_only_sample.jsonl \
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
    --input ../../datasets/distill/excel_distill_v1_verified_augmented.jsonl \
            ../../datasets/distill/planner_augment_v3.jsonl \
            ../../datasets/distill/excel_hard_manual_v1.jsonl \
            ../../datasets/distill/excel_new_tools_manual_v1.jsonl \
            ../../datasets/distill/excel_scenario_report_extract_v1.jsonl \
    --output ../../datasets/train/planner_sft_v5_train.jsonl \
    --with-clarify --with-coverage --coverage-per-action 16 --max-per-action 40

# 분포 감사
python scripts/audit_planner_action_coverage.py \
    --jsonl ../../datasets/train/planner_sft_v5_train.jsonl --output ../scratch/coverage_v5.json
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
uv run python scripts/report_traffic_origin.py ../../logs/all_events.jsonl

# 오염 제거 + 중복 제거 + 출처×액션 층화 분할
uv run python scripts/split_planner_sft.py \
    --input ../../datasets/train/planner_sft_v5_train.jsonl \
            ../../datasets/train/planner_sft_v5_test.jsonl \
    --train-out ../../datasets/train/planner_sft_v6_train.jsonl \
    --test-out ../../datasets/train/planner_sft_v6_test.jsonl
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
  - `services/sidecar/scripts/smoke_excel_live_nl.py`에 권한/복구/성능/버전/교육형 단일턴·멀티턴 케이스를 추가해 회귀 점검 범위를 넓힘
- 데이터셋 스키마/샘플:
  - `docs/EXCEL_LIVE_ROUTER_DATASET_SCHEMA.md`
  - `docs/EXCEL_LIVE_ROUTER_DATASET_SAMPLE.json`
