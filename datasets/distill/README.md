# datasets/distill

한국어 우선 distillation 데이터 동결 산출물을 저장하는 디렉터리입니다.

## 생성 파일

- `excel_distill_v1_train.jsonl`
- `excel_distill_v1_valid.jsonl`
- `excel_distill_v1_test.jsonl`
- `excel_distill_v1_hard_cases.jsonl`
- `freeze_manifest.json`

## 권장 생성 순서

1) 원천 distill JSONL 생성

```bash
cd python-sidecar
uv run python scripts/build_excel_distill_jsonl.py \
  --all-events ../logs/all_events.jsonl \
  --preferred-locale ko \
  --output ../datasets/distill/excel_distill_v1_raw.jsonl \
  --stats
```

2) hard-case 샘플링 + split 고정

```bash
cd python-sidecar
uv run python scripts/sample_hard_cases.py \
  --input-jsonl ../datasets/distill/excel_distill_v1_raw.jsonl \
  --output-dir ../datasets/distill \
  --hard-case-min-score 2 \
  --train-ratio 0.8 \
  --valid-ratio 0.1 \
  --seed 42 \
  --stats
```

## 버전 관리 규칙

- split을 바꾸는 변경은 `freeze_manifest.json`의 타임스탬프와 함께 기록합니다.
- 학습 파이프라인은 `record_id`를 기준으로 중복을 제거합니다.
- `hard_cases`는 재학습 우선순위 큐로 사용합니다.
