---
name: devlog-and-logs
description: Office-Claw에서 코드를 고치거나 실험(배터리·게이트·진단)을 돌린 뒤 반드시 실행한다. 개발일지 항목을 실측 수치와 함께 남기고, 오류·실패는 추측하지 말고 logs/chat_log.jsonl에서 실제 판단 경로를 확인하게 한다. "왜 이렇게 됐지", "오류 원인", "실패 분석", "개발일지" 같은 요청에도 쓴다.
---

# 개발일지와 로그 — 고쳤으면 적고, 틀렸으면 로그를 연다

이 저장소의 핵심 자산은 코드가 아니라 **"무엇을 재 봤고 무엇이 반증됐는가"의 기록**이다.
기록하지 않은 측정은 다음 사람이 처음부터 다시 재게 만든다.

## 1. 오류를 만나면 — 추측 금지, 로그부터

증상만 보고 코드를 뒤지면 원인이 모델의 변덕일 때 끝없이 헤맨다. **판단 경로를 먼저 본다.**

### 대화 배터리 실패 (`scenarios/dialogue/*_log.json`)

```powershell
$PY = "$env:LOCALAPPDATA\officeclaw\venvs\python-sidecar\Scripts\python.exe"
& $PY python-sidecar\scripts\dialogue_failures.py <dialogue_exN_log.json> [...]
```

러너 로그와 `logs/chat_log.jsonl`을 턴별로 합쳐, 실패마다 **경로 · 훅 · 모델 계획 · 검증 오류**를 보여 준다.

### 한 턴을 깊게

```powershell
& $PY python-sidecar\scripts\show_turns.py --failed -n 8      # 깨진 턴만
& $PY python-sidecar\scripts\show_turns.py --grep "정렬" -n 3  # 문장으로 찾기
```

한 턴에 모델을 여러 번 부르는 경로(재계획·관측 루프)는 `show_turns.py`로 부족하다 — **첫 호출만** 보여 준다:

```powershell
& $PY python-sidecar\scripts\dump_turn_llm_calls.py logs\diagnostics\<실행id>.jsonl <turn_id> logs\turn.txt
```

> `logs/chat_log.jsonl`을 에디터로 직접 열지 말 것 — 한 턴이 2KB짜리 한 줄이라 눈으로 못 쫓는다.

### 로그에서 무엇을 보는가

| 필드 | 답해 주는 질문 |
|---|---|
| `routes[].at` | `quick_rule:hit`인가 `miss`인가 — **hit이면 LLM은 호출되지도 않았다.** 프롬프트·모델을 고쳐도 그 경로는 안 바뀐다 |
| `routes[].reason` | 규칙이 왜 확정했나/왜 넘겼나(`high_confidence_action` · `row_write_confirmed` · `underfit:<조항>` · `no_quick_plan`) |
| `stages[rules].hook` | 어느 사람 말투 훅이 계획을 냈나 |
| `stages[planner].action_plan` | 모델이 실제로 무엇을 냈나 |
| `stages[binder].notes` | 어떤 슬롯이 `unresolved`인가, 이유는(`not_stated` · `echoed_request`) |
| `stages[executed]` | 실행 params·엔진(xlwings/file)·**실행 직후 값 스냅샷** |
| `outcome.diag` | 검증 오류·실패 단계·롤백 |

### 그리고 파일을 연다 — 응답을 믿지 말 것

사이드카는 실패를 예외가 아니라 `ok:false`로 알리고, **계획대로 실행됐어도 계획이 틀렸으면 성공으로 보고한다.**
2026-08-19 실측: 배터리가 99.7%라고 한 상태에서 결과 워크북에는 명령문이 박힌 칸 4개, 원본 시트에 잘못 쓰인
수식 6건, 지워진 학생 이름 1개가 있었다.

```powershell
& $PY <scratchpad>\scan_command_text_cells.py     # 셀에 명령문이 박혔는지 전수 검사
```

## 2. 고쳤으면 개발일지에 적는다 — 예외 없음

**코드를 편집하거나 실험을 돌린 턴은 `개발일지.md`에 한 항목을 남기고 끝낸다.**

```markdown
## YYYY-MM-DD (요일) — <한 줄 제목>

- **증상**: 무엇이 잘못 보였나 (사용자 표현 그대로)
- **원인**: 파일:줄 + 왜 그렇게 되는지
- **조치**: 바꾼 파일과 한 줄 요약
- **측정**: 전/후 수치 + 실행 id (없으면 "측정 안 함"이라고 적는다)
- **남은 것**: 안 고친 것과 그 이유
```

지킬 것:

- **수치는 실측만.** 지어낸 숫자는 이 체계를 통째로 무용지물로 만든다. 실행 id(`0811-171221-after-guards`)를
  함께 남겨 나중에 같은 로그로 재확인할 수 있게 한다.
- **반증된 것도 남긴다.** 외부 지적이 이미 구현된 기능을 가리키는 경우가 잦다(2026-08-11에 9개 중 3개가 그랬다).
  내가 세운 가정이 틀렸다면 **그 가정과 무엇이 뒤집었는지**를 적는다 — 그게 다음 사람을 가장 많이 아낀다.
- **날짜는 절대 표기로.** "어제", "지난주" 금지.
- 커밋 메시지에도 같은 수치를 넣는다.

## 3. 측정 없이 "고쳤다"고 하지 않는다

로컬 LLM은 같은 문장에 같은 계획을 준다는 보장이 없다. 한 번 본 실패로 코드를 고치기 시작하면
원인이 모델의 변덕일 때 끝없이 헤맨다.

```powershell
cd python-sidecar
& $PY scripts\run_command_diagnostics.py -n 3 --label before-<작업이름>
& $PY scripts\run_command_diagnostics.py -n 3 --label after-<작업이름>
```

일반화(처음 보는 문장)를 재려면 **블라인드 게이트**를 쓴다 — 회귀 배터리는 일반화를 증명하지 못한다:

```powershell
& $PY scripts\run_blind_paraphrase_gate.py ..\datasets\eval\blind_paraphrases_v1.jsonl
& $PY scripts\blind_gate_report.py ..\datasets\eval\blind_paraphrases_v1_report.json
```

보는 지표는 정답률보다 **조용한 오실행률**이 먼저다 — 틀리는 것보다 조용히 틀리는 것이 나쁘다.

## 4. 배터리 운영 규칙

- **동시 실행 금지.** 배터리 두 개가 같은 워크북·사이드카를 만지면 결과가 섞인다.
- **배터리가 도는 동안 패키지 파일을 고치지 않는다.** `run_dialogue.py`는 각본마다 새 프로세스로 임포트해서,
  중간에 파일이 깨져 있으면 그 뒤 각본이 전부 0턴이 된다(2026-08-19에 두 번 겪음). 테스트·문서는 괜찮다.
- v1/v2 각본은 **같은 워크북을 공유**한다. 결과를 대조할 때는 마지막에 돈 각본 기준으로 본다.
