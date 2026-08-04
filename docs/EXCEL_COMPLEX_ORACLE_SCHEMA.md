# Excel 복잡 작업 오라클 스키마 (v1)

목표: 단일 액션 성공이 아니라, **복잡 멀티턴 작업이 실제 결과물까지 맞는지**를 자동 검증한다.

## 1) 파일 구성

- 시나리오 팩: `datasets/excel_complex_scenarios_v1.json`
- 기계 검증 스키마: `datasets/excel_complex_oracle_schema.v1.json`
- 실행 스크립트: `python-sidecar/scripts/verify_excel_complex_scenarios.py`

## 2) 오라클 3계층

각 시나리오는 `oracle` 아래 3계층을 가진다.

1. `conversation`
   - 턴별 기대 상태 검증
   - 예: `status_code`, `action`, `action_in`, `ask_follow_up`, `result_numeric_gte`
2. `execution`
   - 실행 액션 시퀀스 제약
   - 예: `must_include_actions`, `forbid_actions`
3. `result`
   - 최종 workbook 상태 검증
   - 예: `sheet_exists`, `cell_equals`, `cell_formula_startswith`,
     `range_non_empty_at_least`, `data_validation_exists`, `sheet_protected`

## 3) assertion 타입

- `sheet_exists`
- `cell_equals`
- `cell_formula_startswith`
- `range_non_empty_at_least`
- `sheet_protected`
- `data_validation_exists`
- `chart_count_at_least`
- `cell_has_border`
- `cell_has_fill`

## 4) 실행 예시

```bash
cd python-sidecar
uv run python scripts/verify_excel_complex_scenarios.py \
  --scenario-pack ../datasets/excel_complex_scenarios_v1.json \
  --output-json ../logs/excel_complex_verify_report.json \
  --model skt/A.X-4.0-Light:latest
```

리포트 핵심 지표:

- `total_scenarios`
- `passed_scenarios`
- `pass_rate`
- `critical_failures`
- `failed_scenarios`

## 5) 릴리즈 게이트 연동

```bash
cd python-sidecar
uv run python scripts/eval_release_gate.py \
  --shadow-report ../logs/eval_ax7b_shadow.json \
  --hard-smoke-report ../logs/smoke_excel_ko_hard_tasks.json \
  --complex-report ../logs/excel_complex_verify_report.json \
  --thresholds-json ../python-sidecar/release_gate_thresholds.v1.json \
  --output-json ../logs/eval_release_gate.json
```

> 권장 정책: `complex pass_rate >= 0.95`, `critical_failures == 0`, `min scenarios >= 30`
