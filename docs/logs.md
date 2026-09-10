# 로그 — `logs/` 에는 `chat_log.jsonl` 하나만

> 사용자 지시(2026-09-10): "로그는 지금 모든 작업을 다 chat_log에만 다 남겨주고 그렇게 될 수
> 있도록 구성해주고, 이 앱 깃 클론한 사람도 딱 chat_log.jsonl에만 남길 수 있도록 작업해줘."

그래서 저장소 `logs/` 안에는 **`chat_log.jsonl` 한 파일만** 있다. 런타임이 남기는 기록(턴·이벤트·
플래너 승격)은 전부 이 한 파일에 들어가고, 측정·게이트·진단 스크립트의 산출물은 저장소 **밖**
(`reports/`, §3)으로 간다. 옛 `all_events.jsonl`·`planner_escalations.jsonl` 은 **파일 자체가
없어졌다** — 2026-09-10 부터 런타임이 그 기록을 chat_log 의 줄로 쓴다(§1). 디스크에 남은 옛 파일은
접지 않고 `reports/legacy/` 로 옮긴다(§5). 경로의 단일 소스는
`services/sidecar/office_claw_sidecar/config.py` 의 `get_*` 함수(§4)다.
이 문서는 옛 `logs/README.md`(2026-08-16 신설, 2026-09-10 폐지)를 잇는다.

```powershell
$PY      = "$env:LOCALAPPDATA\officeclaw\venvs\python-sidecar\Scripts\python.exe"
$REPORTS = "$env:LOCALAPPDATA\office_claw\reports"      # get_reports_dir() 기본값 (§3)
$env:PYTHONUTF8 = "1"
```

## 1. `logs/chat_log.jsonl` — 줄 종류 세 가지

한 줄이 JSON 하나다. 줄마다 종류가 다르고, **`turn_id` 가 있으면 턴**이다. 읽는 쪽은
`"turn_id" in rec` 로 턴만 고른다 — 턴 줄에는 `record` 키가 없으므로 `record` 를 먼저 보면 안 된다.

| 종류 | 구분 | 필드 |
|---|---|---|
| **턴** | `turn_id` 있음 | 13키 고정: `turn_id` · `at` · `endpoint` · `session_id` · `source` · `user_input` · `message` · `origin` · `request` · `elapsed_ms` · `routes` · `stages` · `outcome`. **키를 추가하지 않는다** (2026-08-29 전수 감사: 10,518턴 균일). 소유 모듈 `services/decision_trace.py`. |
| **이벤트** | `"record": "event"` | `at`(ISO, KST) · `event_type`(`audit` · `chat_message` · `command_audit` · `harness` …) · `payload`(dict). 옛 `all_events.jsonl` 의 한 줄과 같은 모양이다. 소유 모듈 `services/unified_log_service.py`. |
| **플래너 승격** | `"record": "planner_escalation"` | `at` + 옛 `planner_escalations.jsonl` 의 키 그대로(`instruction` · `digest` · `attempts` · `final_tier` · `last_error` · `output_json` · `recorded_at`). 로컬 플래너가 실패해 상위 단계로 넘어간 건 — SFT 학습 후보 큐다. 소유 모듈 `services/excel_planner_escalation.py`. |

턴이 아닌 줄에는 `turn_id` 를 넣지 않는다. 세 종류가 시간순으로 섞여 있다.
프론트 사건(`trace_client_event`)도 턴 줄로 같은 파일에 들어간다.

### 보는 법 — 에디터로 열지 않는다

한 턴이 2KB짜리 한 줄이라 눈으로 못 쫓는다. 반드시 뷰어를 쓴다.

```powershell
& $PY services\sidecar\scripts\show_turns.py -n 5              # 최근 5턴
& $PY services\sidecar\scripts\show_turns.py --failed -n 8     # 깨진 턴만
& $PY services\sidecar\scripts\show_turns.py --follow          # 실시간
& $PY services\sidecar\scripts\show_turns.py --grep "정렬"      # 사용자 문장으로 (원문 포함)
& $PY services\sidecar\scripts\show_turns.py --macro <macro_id> # 한 매크로의 하위 단계만
& $PY services\sidecar\scripts\show_turns.py --log <다른 파일>   # 회전 조각·진단 실행 파일도 같은 뷰어로
```

한 턴에 모델을 여러 번 부른 경로(재계획·관측 루프)는 `show_turns.py`가 **첫 호출만** 보여 준다.
LLM 호출의 프롬프트·응답 전문은 chat_log 가 아니라 진단 실행 파일(`reports/diagnostics/`)에 있다.

```powershell
& $PY services\sidecar\scripts\dump_turn_llm_calls.py $REPORTS\diagnostics\<실행id>.jsonl <turn_id> scratch\turn.txt
```

### 레코드에서 꼭 보는 필드 (턴 줄)

- `user_input` — **사람이 실제로 요구한 말.** 항상 채워진다.
- `message` — 그 턴이 처리한 문장. 매크로 하위 단계면 분해기가 만든 문장이라 `user_input`과 다르다.
- `origin.kind` — `user` / `macro_step` / `approval`. 매크로면 `macro_id`·`step_index`도 붙는다.
- `routes` — `quick_rule:hit`이면 그 턴에는 LLM이 **아예 호출되지 않았다**.
- `outcome.ok` — 실패는 예외가 아니라 이 필드로 온다.

필드별로 어떤 질문에 답하는지는 `.claude/skills/devlog-and-logs/references/log-reading.md`.

### 실사용 기준 트리아지 — "채팅 치자마자 뭐가 깨졌나"

`chat_log.jsonl`에는 실사용과 배터리·측정 트래픽이 섞여 있다. 앱(GUI) 세션은
`excel-live::ui::<uuid>` 키를 쓰므로 실사용만 갈라 볼 수 있다:

```powershell
& $PY services\sidecar\scripts\triage_real_usage.py                 # 일자별 문제율 + 세션 첫 턴 분포
& $PY services\sidecar\scripts\triage_real_usage.py --day 2026-08-17
& $PY services\sidecar\scripts\triage_real_usage.py --problems 20   # 최근 문제 턴 상세
```

분류: 실패 / 무변화(성공인데 보이는 변화 0 — 정직 보고 턴) / 되묻기 / 승인대기 /
채팅전환 / 안전정지 / 실행OK. "세션 첫 턴" 분포가 체감 품질 지표다.

한계: **실행은 성공했지만 보기에 어긋난 부류**(연한 테두리가 흰 배경에서 안 보임
같은)는 로그로 못 잡는다 — 그건 GUI 스크린샷이 유일한 단서다.

**측정 스크립트를 새로 만들 때는 세션 키를 `test-` 접두로** 지어 실사용 집계를
오염시키지 않는다. 배터리·게이트가 만든 턴도 같은 파일에 쌓이지만 이 접두로 자동 제외된다.

## 2. 회전 — 64MB 마다 조각을 저장소 밖으로

`chat_log.jsonl` 이 64MB 를 넘으면 `decision_trace._rotate_if_needed` 가 현재 파일을
`chat_log.<KST스탬프>.jsonl` 로 옆으로 치우고 새 파일을 시작한다. 조각의 목적지는 저장소 **밖**:

| 무엇 | 어디 |
|---|---|
| 회전 조각 | `get_chat_log_archive_dir()` = `%LOCALAPPDATA%\office_claw\chat_log_archive\chat_log.<KST스탬프>.jsonl` |

다른 폴더로의 이동이라 `rename` 이 아니라 `shutil.move` 다. 이력은 **무손실** — 전 이력 =
조각들 + 현재 파일. git 은 현재 파일만 추적한다. 조각을 볼 때는 `show_turns.py --log <조각>`.

## 3. 산출물 — `reports/` (저장소 밖)

측정·게이트·진단 스크립트가 내는 보고서 JSON·MD, 야간 게이트 결과, 회귀 평가는 전부
`get_reports_dir()` 아래로 간다. 개발일지가 실행 id 로 가리키는 파일은 여기서 찾는다.

| 옛 위치 (2026-09-10 이전) | 지금 |
|---|---|
| `logs/nightly/` (야간 게이트 `LATEST.md`·날짜별 `.md`·`.txt`) | `reports/nightly/` |
| `logs/nightly/.running.lock` (게이트·배터리 겹침 방지 자물쇠) | `reports/nightly/.running.lock` |
| `logs/measurements/<날짜>/` | `reports/measurements/<날짜>/` |
| `logs/diagnostics/<실행id>.jsonl`·`.report.json` (명령 진단 배터리) | `reports/diagnostics/` |
| `logs/e2e/` | `reports/e2e/` |
| `logs/skill_ab/` | `reports/skill_ab/` |
| `logs/<보고서>.json` · `logs/<보고서>.md` (eval_shadow·eval_gate·verifier_*·ab_* …) | `reports/<같은 이름>` — 새 산출물이 여기 생긴다. 디스크에 남은 옛 파일은 §5 가 `reports/legacy/` 로 옮긴다 |
| `logs/all_events.jsonl` | **없어짐** — 새 기록은 `chat_log.jsonl` 의 `record=event` 줄. 옛 파일은 접지 않고 `reports/legacy/all_events.jsonl` 로(§5) |
| `logs/planner_escalations.jsonl` | **없어짐** — 새 기록은 `chat_log.jsonl` 의 `record=planner_escalation` 줄. 옛 파일은 접지 않고 `reports/legacy/planner_escalations.jsonl` 로(§5) |
| `logs/<그 밖의 파일·폴더>` (`turns.txt` · 대응표에 없는 낱 파일 · 대응표에 없는 폴더) | `reports/legacy/<같은 이름>` (§5) |
| `logs/chat_log.<스탬프>.jsonl` (회전 조각) | `chat_log_archive/` (§2) |
| `logs/archive-2026-08/`·`archive-2026-09/` (일회성 산출물 보관) | `reports/archive-2026-08/`·`reports/archive-2026-09/` |
| `logs/officeclaw_backups/` | 워크북 **옆**에 생기는 작업 전 자동 백업이다(`excel_live_file_service.py`). `logs/` 와 무관하다 — §5 가 비어 있으면 제거하고, 아니면 `reports/legacy/officeclaw_backups/` 로 옮긴다. |

### 옛 `logs/measurements/` 의 날짜 폴더 — 개발일지가 실행 id 로 참조한다, 지우지 않는다

| 폴더 | 내용 |
|---|---|
| `2026-08-16/` | 플래너 회귀 평가(v3 vs v5r) 154건 + 승격 게이트 판정, demo 재현 대조 산출물 |
| `2026-08-18/` | v5r 섀도 평가 조각(A/B)·병합·게이트 판정 — 승격 불가 근거 |

새 측정을 돌리면 `reports/measurements/<날짜>/` 에 넣는다.

### 야간 게이트를 보는 명령

```powershell
Get-Content $env:LOCALAPPDATA\office_claw\reports\nightly\LATEST.md -TotalCount 20   # 세션 시작 시
```

## 4. 경로의 단일 소스 — `config.py` 와 환경변수 3개

| 함수 | 기본값 | 환경변수 |
|---|---|---|
| `get_logs_dir()` | 소스 트리 실행: `<repo>/logs` · 배포: `<data_dir>/logs` | `OFFICE_CLAW_LOGS_DIR` |
| `get_chat_log_path()` | `get_logs_dir()/chat_log.jsonl` | (위를 따른다) |
| `get_chat_log_archive_dir()` | `<data_dir>/chat_log_archive` | `OFFICE_CLAW_CHAT_LOG_ARCHIVE_DIR` |
| `get_reports_dir()` | `<data_dir>/reports` | `OFFICE_CLAW_REPORTS_DIR` |

`<data_dir>` = Windows `%LOCALAPPDATA%\office_claw` · macOS `~/Library/Application Support/office_claw`
· Linux `~/.local/share/office_claw` (`get_data_dir()`). 세 폴더는 부르는 순간 만들어진다.

경로를 **문자열로 박지 않는다.** 사이드카 안에서는 `from office_claw_sidecar.config import get_reports_dir`,
저장소 루트 `scripts/` 의 파이썬은 `sys.path.insert(0, "<repo>/services/sidecar")` 뒤 같은 함수를
임포트한다(패키지 `__init__` 은 `__version__` 한 줄이라 가볍다).

## 5. 옛 배치를 옮기기 — `scripts/migrate_logs_dir.py`

2026-09-10 이전부터 쓰던 작업 사본에는 `logs/` 에 §3 의 옛 위치들이 그대로 있다. 새로 클론한
사람은 할 일이 없다 — 처음부터 `chat_log.jsonl` 하나만 생긴다.

```powershell
& $PY scripts\migrate_logs_dir.py            # 기본은 dry-run: 무엇을 어디로 옮길지만 보여 준다
& $PY scripts\migrate_logs_dir.py --apply    # 실제로 옮긴다
```

§3 대응표대로 옮긴다 — 폴더는 항목 단위로 합치고, 대상에 같은 이름이 이미 있으면 덮지 않고
건너뛴다(건너뛴 원본은 `logs/` 에 남는다). 옛 `all_events.jsonl`·`planner_escalations.jsonl` 과
그 밖의 낱 파일·폴더는 `reports/legacy/` 로 옮긴다 — **chat_log 로 접지 않는다**(런타임만
2026-09-10 부터 `record` 줄로 쓴다, §1). `officeclaw_backups/` 는 비어 있으면 제거하고, 아니면
`reports/legacy/` 로 옮긴다.

**야간 게이트나 배터리가 도는 중에는 돌리지 않는다.** 옮기는 대상이 `logs/` 이므로 막아야 할
실행은 옛 코드로 떠서 `logs/nightly/` 에 쓰는 것이다 — `logs/nightly/.running.lock` 을 산 프로세스가
쥐고 있으면 스크립트가 `--apply` 를 거부한다(종료코드 2). 죽은 프로세스가 남긴 그 자물쇠는 지운다.

## 6. git 추적 — `chat_log.jsonl` 하나

`.gitignore` 는 `logs/*` 뒤에 `!logs/chat_log.jsonl` 한 줄만 예외로 둔다.
`git ls-files logs` 는 `logs/chat_log.jsonl` 한 줄이어야 한다. 옛 `logs/nightly/*.md` 추적은
2026-09-10 에 풀었다(디스크에는 남아 있고 §5 로 옮긴다).

턴당 약 2KB. 명령 원문·워크북 값이 남으니 **남의 파일로 작업한 턴은 커밋 전에 지운다.**
