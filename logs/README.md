# logs/

## 상시 로그 — 디버깅은 여기서 시작한다

| 파일 | 내용 |
|---|---|
| **`chat_log.jsonl`** | **핵심.** 턴당 한 줄. 사용자 원문·라우팅·계획·실행·검증·결론이 전부 들어 있다 |
| `all_events.jsonl` | 하위 이벤트 스트림 |
| `planner_escalations.jsonl` | 플래너가 실패해 상위 티어로 넘어간 건 |

`chat_log.jsonl`은 한 턴이 2KB짜리 한 줄이라 에디터로 열면 못 읽는다. 반드시 뷰어를 쓴다.

```powershell
$PY = "$env:LOCALAPPDATA\officeclaw\venvs\python-sidecar\Scripts\python.exe"

& $PY python-sidecar\scripts\show_turns.py -n 5              # 최근 5턴
& $PY python-sidecar\scripts\show_turns.py --failed -n 8     # 깨진 턴만
& $PY python-sidecar\scripts\show_turns.py --follow          # 실시간
& $PY python-sidecar\scripts\show_turns.py --grep "정렬"      # 사용자 문장으로 (원문 포함)
& $PY python-sidecar\scripts\show_turns.py --macro <macro_id> # 한 매크로의 하위 단계만
```

한 턴에 모델을 여러 번 부른 경로(재계획·관측 루프)는 `show_turns.py`가 **첫 호출만** 보여 준다.

```powershell
& $PY python-sidecar\scripts\dump_turn_llm_calls.py <실행id>.jsonl <turn_id> logs\turn.txt
```

### 레코드에서 꼭 보는 필드

- `user_input` — **사람이 실제로 요구한 말.** 항상 채워진다.
- `message` — 그 턴이 처리한 문장. 매크로 하위 단계면 분해기가 만든 문장이라 `user_input`과 다르다.
- `origin.kind` — `user` / `macro_step` / `approval`. 매크로면 `macro_id`·`step_index`도 붙는다.
- `routes` — `quick_rule:hit`이면 그 턴에는 LLM이 **아예 호출되지 않았다**.
- `outcome.ok` — 실패는 예외가 아니라 이 필드로 온다.

## 실사용 기준 트리아지 — "채팅 치자마자 뭐가 깨졌나"

`chat_log.jsonl`에는 실사용과 배터리·측정 트래픽이 섞여 있다. 앱(GUI) 세션은
`excel-live::ui::<uuid>` 키를 쓰므로 실사용만 갈라 볼 수 있다:

```powershell
& $PY python-sidecar\scripts\triage_real_usage.py                 # 일자별 문제율 + 세션 첫 턴 분포
& $PY python-sidecar\scripts\triage_real_usage.py --day 2026-08-17
& $PY python-sidecar\scripts\triage_real_usage.py --problems 20   # 최근 문제 턴 상세
```

분류: 실패 / 무변화(성공인데 보이는 변화 0 — 정직 보고 턴) / 되묻기 / 승인대기 /
채팅전환 / 안전정지 / 실행OK. "세션 첫 턴" 분포가 체감 품질 지표다.

한계: **실행은 성공했지만 보기에 어긋난 부류**(연한 테두리가 흰 배경에서 안 보임
같은)는 로그로 못 잡는다 — 그건 GUI 스크린샷이 유일한 단서다.

**측정 스크립트를 새로 만들 때는 세션 키를 `test-` 접두로** 지어 실사용 집계를
오염시키지 않는다.

## measurements/ — 날짜별 측정 산출물

재현·평가 결과를 날짜 폴더에 모은다. 개발일지가 실행 id로 참조하는 파일들이라 지우지 않는다.

| 폴더 | 내용 |
|---|---|
| `2026-08-16/` | 플래너 회귀 평가(v3 vs v5r) 154건 + 승격 게이트 판정, demo 재현 대조 산출물 |
| `2026-08-18/` | v5r 섀도 평가 조각(A/B)·병합·게이트 판정 — 승격 불가 근거 |

새 측정을 돌리면 `measurements/<날짜>/`에 넣는다. **logs/ 최상위에는 상시 로그만 둔다.**

## officeclaw_backups/

작업 전 자동 백업. 복구용이며 주기적으로 비워도 된다.
