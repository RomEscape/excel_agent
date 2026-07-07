# Excel Live 라우터 데이터셋 스키마

엑셀 질문 세트를 단순 문장 목록이 아니라, **의도 라벨 + 슬롯 + 멀티턴 + 검증 기준**으로 관리하기 위한 기준 문서다.

## 1) 목적

- 라우터 분류 정확도 평가
- 멀티턴 슬롯 수집 품질 평가
- 복합 작업(여러 에이전트 경유) 경로 검증
- 위험 작업 승인 게이트/복구 정책 검증

## 2) 권장 필드

| 필드 | 설명 |
|---|---|
| `id` | 케이스 고유 ID |
| `domain` | 업무 도메인(`sales`, `hr`, `project`, `finance` 등) |
| `user_level` | 사용자 숙련도(`beginner`, `intermediate`, `advanced`) |
| `initial_query` | 사용자의 러프 첫 질문 |
| `intent` | 라우터 정답 의도 |
| `sub_intents` | 복합 작업 보조 의도 배열 |
| `ambiguity_level` | `low`/`medium`/`high` |
| `required_slots` | 실행 전 필수 슬롯 목록 |
| `dialogue` | 멀티턴 대화(질문-응답) 로그 |
| `final_action_plan` | 최종 실행 요약 |
| `expected_route` | 호출되어야 하는 모듈/에이전트 순서 |
| `expected_tools` | 예상 액션/도구(`excel_live.*`) |
| `risk_level` | `low`/`medium`/`high` |
| `needs_confirmation` | 승인 필요 여부 |
| `validation_criteria` | 결과 검증 체크리스트 |

## 3) 의도 라벨 최소 집합

- `CREATE_TABLE`
- `FORMAT_SHEET`
- `CLEAN_DATA`
- `TRANSFORM_DATA`
- `FORMULA_CALC`
- `CONDITIONAL_LOGIC`
- `LOOKUP_MATCH`
- `FILTER_SORT`
- `AGGREGATE_SUMMARY`
- `PIVOT_TABLE`
- `CHART_VISUALIZE`
- `DASHBOARD`
- `VALIDATION_RULE`
- `COMPARE_TABLES`
- `IMPORT_MERGE`
- `EXPORT_PRINT`
- `AUTOMATION`
- `DEBUG_FORMULA`
- `PERFORMANCE`
- `EXPLAIN_HELP`
- `SAFETY_PERMISSION`

## 4) JSON 예시

```json
{
  "id": "excel_sales_pivot_001",
  "domain": "sales",
  "user_level": "beginner",
  "initial_query": "매출 정리해줘",
  "intent": "PIVOT_TABLE",
  "sub_intents": ["FORMULA_CALC", "CHART_VISUALIZE"],
  "ambiguity_level": "high",
  "required_slots": [
    "key_column",
    "value_column",
    "operation",
    "output_location"
  ],
  "dialogue": [
    {"role": "user", "content": "매출 정리해줘."},
    {"role": "assistant", "content": "월별/상품별/담당자별 중 어떤 기준으로 정리할까요?"},
    {"role": "user", "content": "월별이랑 상품별."},
    {"role": "assistant", "content": "매출액 열이 있나요, 아니면 수량×단가로 계산할까요?"},
    {"role": "user", "content": "수량이랑 단가가 있어."},
    {"role": "assistant", "content": "결과는 새 요약 시트에 만들까요?"},
    {"role": "user", "content": "응."}
  ],
  "final_action_plan": "수량×단가 계산 후 월별·상품별 피벗 요약표와 차트를 새 시트에 생성",
  "expected_route": [
    "WorkbookInspector",
    "FormulaAgent",
    "PivotAgent",
    "ChartAgent",
    "Validator"
  ],
  "expected_tools": [
    "excel_live.set_formula",
    "excel_live.pivot_table",
    "excel_live.create_chart"
  ],
  "risk_level": "medium",
  "needs_confirmation": true,
  "validation_criteria": [
    "피벗 합계가 원본 합계와 일치",
    "차트 소스 범위가 피벗 결과를 참조",
    "원본 시트 데이터가 보존"
  ]
}
```

## 5) 운영 권장

- 의도별 최소 20개 이상으로 시작해 회귀 실패 케이스를 누적
- 모호한 러프 질문(`정리해줘`, `보기 좋게`, `자동화해줘`) 비중을 높게 유지
- 위험 작업은 항상 `needs_confirmation=true` 케이스를 별도로 분리해 평가
