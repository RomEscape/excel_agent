# AX7B Distillation Runbook

목표: `A.X 7B` 계열 모델을 Excel 플래너(`자연어 -> excel_live.action_plan`)로 증류하고, 기존 실행기 안정성(validator/rollback/queue)을 유지한 상태로 교체한다.

## 0) 사전 조건

- Python sidecar 실행 환경 준비
- Ollama에 baseline 모델 설치 (`skt/A.X-4.0-Light:latest`)
- 원천 로그 준비 (`logs/all_events.jsonl`)

## 1) 데이터 동결

```bash
cd python-sidecar
uv run python scripts/sample_hard_cases.py \
  --all-events ../logs/all_events.jsonl \
  --output-dir ../datasets/distill \
  --stats
```

산출물:
- `datasets/distill/excel_distill_v1_train.jsonl`
- `datasets/distill/excel_distill_v1_valid.jsonl`
- `datasets/distill/excel_distill_v1_test.jsonl`
- `datasets/distill/excel_distill_v1_hard_cases.jsonl`
- `datasets/distill/freeze_manifest.json`

## 2) Teacher 라벨링 + 재시도

```bash
cd python-sidecar
uv run python scripts/teacher_label_action_plan.py \
  --input-jsonl ../datasets/distill/excel_distill_v1_train.jsonl \
  --output-jsonl ../datasets/distill/excel_distill_v1_teacher_labeled.jsonl \
  --provider ollama \
  --teacher-model skt/A.X-4.0-Light:latest \
  --stats
```

```bash
cd python-sidecar
uv run python scripts/teacher_label_retry.py \
  --input-jsonl ../datasets/distill/excel_distill_v1_teacher_labeled.jsonl \
  --output-jsonl ../datasets/distill/excel_distill_v1_teacher_labeled_retry.jsonl \
  --provider ollama \
  --teacher-model skt/A.X-4.0-Light:latest \
  --stats
```

## 3) 실행 검증 게이트

```bash
cd python-sidecar
uv run python scripts/verify_distill_execution.py \
  --input-jsonl ../datasets/distill/excel_distill_v1_teacher_labeled_retry.jsonl \
  --output-jsonl ../datasets/distill/excel_distill_v1_verified.jsonl \
  --stats
```

```bash
cd python-sidecar
uv run python scripts/build_ax7b_training_set.py \
  --input-jsonl ../datasets/distill/excel_distill_v1_verified.jsonl \
  --output-jsonl ../datasets/train/ax7b_planner_sft_train.jsonl \
  --stats
```

## 4) QLoRA 학습

설정 파일:
- `train/ax7b/qlora_config.yaml`

실행:

```bash
python train/ax7b/train_ax7b_qlora.py --config train/ax7b/qlora_config.yaml
```

산출물:
- `artifacts/ax7b-planner-lora/`

## 5) LoRA 병합 + GGUF 변환

```bash
python train/ax7b/merge_lora_to_fp16.py \
  --base-model skt/A.X-4.0-Light \
  --adapter-dir artifacts/ax7b-planner-lora \
  --output-dir artifacts/ax7b-planner-merged
```

```bash
LLAMA_CPP_DIR=/path/to/llama.cpp \
bash train/ax7b/export_ax7b_gguf.sh \
  artifacts/ax7b-planner-merged \
  artifacts/ax7b-planner.gguf \
  q4_k_m
```

Ollama 배포용 템플릿:
- `deploy/ollama/Modelfile.ax7b-planner`

## 6) Shadow 평가

```bash
cd python-sidecar
uv run python scripts/eval_ax7b_shadow.py \
  --input-jsonl ../datasets/distill/excel_distill_v1_verified.jsonl \
  --output-json ../logs/eval_ax7b_shadow.json \
  --provider ollama \
  --baseline-model skt/A.X-4.0-Light:latest \
  --candidate-model officeclaw-ax7b-planner:latest \
  --limit 100
```

## 7) 승격 게이트

```bash
cd python-sidecar
uv run python scripts/eval_release_gate.py \
  --shadow-report ../logs/eval_ax7b_shadow.json \
  --hard-smoke-report ../logs/smoke_excel_ko_hard_tasks.json \
  --complex-report ../logs/excel_complex_verify_report.json \
  --thresholds-json ../python-sidecar/release_gate_thresholds.v1.json \
  --output-json ../logs/eval_release_gate.json
```

## 8) 복잡 작업 30시나리오 검증 (권장)

```bash
cd python-sidecar
uv run python scripts/verify_excel_complex_scenarios.py \
  --scenario-pack ../datasets/excel_complex_scenarios_v1.json \
  --output-json ../logs/excel_complex_verify_report.json \
  --model skt/A.X-4.0-Light:latest
```

산출물:
- `logs/excel_complex_verify_report.json`

## 9) 개발일지 자동 append

```bash
cd python-sidecar
uv run python scripts/append_devlog_from_reports.py \
  --devlog ../개발일지.md \
  --complex-report ../logs/excel_complex_verify_report.json \
  --release-gate ../logs/eval_release_gate.json \
  --from-staged
```

강제 체크:
- 로컬 pre-commit: `lefthook.yml`의 `devlog-guard`
- PR CI: `.github/workflows/pr-check.yml`의 `devlog-check`

## 운영 원칙

- planner만 교체하고 executor는 그대로 유지
- 승격 대상 데이터는 `label_status=verified`만 허용
- 실패 케이스는 `hard_cases` 큐로 재주입하여 다음 라운드에서 재학습
