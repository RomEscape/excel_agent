# 측정 — 재고 나서 고쳤다고 한다

## 이 환경의 파이썬

```powershell
$PY = "$env:LOCALAPPDATA\officeclaw\venvs\python-sidecar\Scripts\python.exe"
$env:PYTHONUTF8 = "1"
```

문서의 `uv run python X`는 전부 `& $PY X`로 바꿔 읽는다.

## before/after 진단

```powershell
cd python-sidecar
& $PY scripts\run_command_diagnostics.py -n 3 --label before-<작업이름>
& $PY scripts\run_command_diagnostics.py -n 3 --label after-<작업이름>
```

## 대화 배터리 (회귀 — 일반화는 증명하지 못한다)

```powershell
bash <scratchpad>/run_all.sh 1 ex9 ex9_v2 ex10 ...   # 순차, 동시 실행 금지
& $PY scripts\dialogue_failures.py <..._log.json>     # 실패 삼각측량
```

## 블라인드 게이트 (일반화 — 이쪽이 진짜 지표)

코드를 못 본 작성자가 쓴 624문장, 정답은 **파일 상태 오라클**.

```powershell
cd python-sidecar
& $PY scripts\run_blind_paraphrase_gate.py ..\datasets\eval\blind_paraphrases_v1.jsonl
& $PY scripts\blind_gate_report.py ..\datasets\eval\blind_paraphrases_v1_report.json
```

분류: `PASS_RULE` / `PASS_CARD` / `ASK` / `WRONG`(카드 없으면 **조용한 오실행**) / `ERROR`.

**보는 순서**: ① 조용한 오실행률 ② 정답 실행률 ③ 되묻기율.
되묻기율이 오르는 것은 **정상**이다 — 남은 불확실성만큼 물어야 한다.

## 플래너 모델 회귀 (154건 × 2모델, 약 15분)

```powershell
& $PY scripts\eval_ax7b_shadow.py --input-jsonl ..\datasets\eval\planner_eval_v1.jsonl `
  --output-json ..\logs\eval_shadow.json `
  --baseline-model ax7bplanner-v3:latest --candidate-model <후보>
& $PY scripts\eval_release_gate.py --shadow-report ..\logs\eval_shadow.json `
  --output-json ..\logs\eval_gate.json --thresholds-json config\planner_gate_thresholds.json
```

## 진행률

```powershell
.\scripts\watch-eval.ps1          # 실시간
.\scripts\watch-eval.ps1 -Once    # 한 번만
```
