# 로그에서 무엇을 읽는가

> `logs/chat_log.jsonl`을 에디터로 직접 열지 말 것 — 한 턴이 2KB짜리 한 줄이라 눈으로 못 쫓는다.

## 한 턴을 훑어보기

```powershell
$PY = "$env:LOCALAPPDATA\officeclaw\venvs\python-sidecar\Scripts\python.exe"
& $PY services\sidecar\scripts\show_turns.py -n 5                 # 최근 5턴
& $PY services\sidecar\scripts\show_turns.py --failed -n 8        # 깨진 턴만
& $PY services\sidecar\scripts\show_turns.py --follow             # 실시간
& $PY services\sidecar\scripts\show_turns.py --grep "정렬" -n 3   # 문장으로 찾기
```

한 턴에 모델을 여러 번 부르는 경로(재계획·관측 루프)는 `show_turns.py`로 부족하다 — **첫 호출만** 보여 준다:

```powershell
& $PY services\sidecar\scripts\dump_turn_llm_calls.py logs\diagnostics\<실행id>.jsonl <turn_id> logs\turn.txt
```

## 필드가 답해 주는 질문

| 필드 | 답해 주는 질문 |
|---|---|
| `routes[].at` | `quick_rule:hit`인가 `miss`인가 — **hit이면 LLM은 호출되지도 않았다** |
| `routes[].reason` | 규칙이 왜 확정했나/왜 넘겼나 (`high_confidence_action` · `row_write_confirmed` · `underfit:<조항>` · `no_quick_plan`) |
| `stages[understand]` | 의도 분류·표 힌트 — 여기서 이미 틀렸으면 뒤는 다 따라 틀린다 |
| `stages[rules].hook` | 어느 사람 말투 훅이 계획을 냈나 (`row_write_paste` · `aggregate_below` · `cross_sheet_aggregate` · `single_cell_write` …) |
| `stages[observation]` | 그 턴에 모델이 **실제로 본** 시트·머리글·사용 범위. 머리글을 보여 줬는데도 없는 열을 골랐다면 관측이 아니라 모델 문제 |
| `stages[planner].action_plan` | 모델이 실제로 무엇을 냈나 |
| `stages[binder].notes` | 어떤 슬롯이 `unresolved`인가, 이유는 (`not_stated` · `echoed_request`) |
| `stages[plan_final].plan_source` | `rule`인가 모델 해석인가 — 해석이면 카드가 떴어야 한다 |
| `stages[executed]` | 실행 params · 엔진(xlwings/file) · **실행 직후 값 스냅샷** |
| `outcome.diag` | 검증 오류 · 실패 단계 · 롤백 |
| `blast_radius` 노트 | 지목 밖의 값을 덮으려 했는가 |

## 진단 순서

1. `routes`를 본다 — 규칙인가 모델인가. **규칙이면 프롬프트를 고쳐도 소용없다.**
2. 규칙 경로면 `rules.hook`으로 어느 훅인지 찾아 그 함수를 연다.
3. 모델 경로면 `observation`으로 모델이 본 상태를 확인하고, `planner.action_plan`과 대조한다.
4. 계획은 맞는데 결과가 틀렸으면 `executed`의 스냅샷과 `outcome.diag`를 본다.
5. 그래도 모르면 **워크북을 연다**(SKILL.md §2).
