# OfficeClaw Excel Distillation 데이터 파이프라인

목표: 상위 API 모델의 스프레드시트 수행 능력을 7-8B 로컬 모델로 압축하기 위해, 공개 벤치/로그/교사(Teacher) 출력을 하나의 학습 포맷(JSONL)으로 통합한다.

## 1) 핵심 원칙

- 실행 엔진과 분리: 모델은 `자연어 -> excel_live 액션 계획(JSON)`에 집중
- 데이터 계층 분리: `unlabeled(벤치) -> weak(로그) -> teacher(교사) -> verified(실행 검증)`
- 회귀 가능성 확보: `record_id`, `source.dataset`, `source.sample_id`를 고정해 재생산 가능하게 유지

## 2) 통합 JSONL 스키마 (excel_distill.v1)

모든 레코드는 한 줄 JSON으로 저장한다.

```json
{
  "schema_version": "excel_distill.v1",
  "record_id": "spreadsheetbench:task_0001:2b6d9f8c",
  "source": {
    "dataset": "spreadsheetbench",
    "split": "train",
    "sample_id": "task_0001",
    "license": "unknown",
    "provenance": {
      "source_file": "data/sample_data_200.jsonl"
    }
  },
  "input": {
    "instruction": "월별 매출 피벗 만들어줘",
    "locale": "ko",
    "workbook_refs": [
      {
        "role": "input",
        "path": "spreadsheet/task_0001/1_task_0001_input.xlsx"
      },
      {
        "role": "golden",
        "path": "spreadsheet/task_0001/1_task_0001_answer.xlsx"
      }
    ],
    "context_hints": {
      "sheet_name": "",
      "answer_position": "G2",
      "instruction_type": "Sheet-Level Manipulation"
    }
  },
  "target": {
    "task_type": "spreadsheet_edit",
    "label_status": "needs_teacher_plan",
    "action_plan": [],
    "expected_output": {
      "golden_workbook_path": "spreadsheet/task_0001/1_task_0001_answer.xlsx"
    }
  },
  "quality": {
    "verification": "benchmark_golden_available",
    "passed": null,
    "confidence": 0.0
  },
  "metadata": {
    "created_at": "2026-07-21T00:00:00+09:00",
    "generator": "python-sidecar/scripts/build_excel_distill_jsonl.py",
    "notes": []
  }
}
```

### 필드 요약

- `schema_version`: 포맷 버전 (`excel_distill.v1`)
- `record_id`: 데이터셋/샘플/해시 기반 고유 ID
- `source`: 원본 데이터셋 식별 정보
- `input`: 사용자 질의 + 워크북 참조 + 컨텍스트 힌트
- `target.label_status`:
  - `needs_teacher_plan`: 액션 계획 미라벨
  - `log_observed`: 로그 기반 약한 라벨
  - `teacher_labeled`: 상위 모델 라벨 완료
  - `verified`: 실행 검증 통과
- `target.action_plan`: `excel_live.*` 액션 배열
- `quality`: 검증 방식/통과 여부/신뢰도

## 3) 데이터셋 매핑 전략

### A. SpreadsheetBench / SpreadsheetBench-2

- 원본: instruction + input/golden workbook 경로
- 기본 출력: `label_status=needs_teacher_plan`
- 활용:
  1) Teacher 모델로 action plan 생성
  2) pandas/xlwings 실행
  3) golden workbook 비교 통과 시 `verified` 승격

### B. SheetCopilot / SheetRM

- 원본: task 메타(xlsx), workbook 식별자, 카테고리
- 기본 출력: `label_status=needs_teacher_plan`
- 활용: task category를 intent 힌트로 사용

### C. OfficeClaw logs/all_events.jsonl

- 원본: `/excel-live/command` 하네스 이벤트
- 기본 출력: `label_status=log_observed`
- 장점: 실제 사용자 문장 분포를 반영
- 주의: action이 있어도 `params` 불완전 가능 -> teacher 재라벨 권장

### D. FxBench (선택)

- 원본: 수식/타겟셀/워크북 바이트
- 기본 출력: `task_type=formula_generation`
- `action_plan=[{"action":"excel_live.set_formula","params":...}]`로 강한 지도 가능

### E. 한국어 사용자 질의 우선 정책

- 기본 선호 언어를 한국어(`preferred_locale=ko`)로 두고 학습 포맷에 아래 힌트를 저장한다.
  - `input.training_hints.preferred_locale`
  - `input.training_hints.preferred_locale_match`
  - `input.language_views` (`normalized`, `no_space`, `compact`, `ko_core`, `ko_no_space`)
- 한국어·혼합 질의만 추출하고 싶으면 `--drop-non-preferred-locale` 옵션 사용
- 영어/기타 샘플은 제거 대신 `needs_locale_rewrite` 노트로 남겨 Teacher가 한국어 패러프레이즈를 생성하도록 권장

## 4) 품질 게이트 (권장)

- G1 Schema Gate: JSONL 필수 필드 누락 0%
- G2 Action Gate: `teacher_labeled` 이상은 `excel_live.*` 외 액션 금지
- G3 Execution Gate: `verified`는 재실행 통과율 95% 이상 유지
- G4 Regression Gate: 고정 평가셋에서 정확도 하락 시 배포 차단

## 5) 학습 분할 권장

- `train`: 80%
- `valid`: 10%
- `test`: 10%
- 분할 단위: 같은 `sample_id/workbook`가 서로 다른 split에 섞이지 않게 그룹 분할

## 6) distillation 루프

1. 공개 벤치 + 로그 수집 (`needs_teacher_plan` / `log_observed`)
2. 상위 API 모델로 action plan 라벨 생성 (`teacher_labeled`)
3. 실행/검증 통과 샘플만 선별 (`verified`)
4. 7-8B 모델 SFT(+DPO) 학습
5. 회귀 벤치(SpreadsheetBench subset + 내부 하네스 리플레이) 측정
6. 실패 케이스를 다음 라운드 데이터에 재편입

## 7) 스크립트

- 변환 스크립트: `python-sidecar/scripts/build_excel_distill_jsonl.py`
- 사용 예시:

```bash
cd python-sidecar
uv run python scripts/build_excel_distill_jsonl.py \
  --spreadsheetbench-root ../datasets/SpreadsheetBench \
  --spreadsheetbench2-root ../datasets/SpreadsheetBench-2 \
  --sheetcopilot-root ../datasets/SheetCopilot \
  --sheetrm-root ../datasets/SheetAgent \
  --all-events ../logs/all_events.jsonl \
  --preferred-locale ko \
  --drop-non-preferred-locale \
  --output ../datasets/officeclaw_excel_distill_v1.jsonl \
  --stats
```

