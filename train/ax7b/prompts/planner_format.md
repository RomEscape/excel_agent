# AX7B Planner 출력 포맷

학습 목표는 **자연어 -> Excel action_plan JSON** 변환입니다.

## 출력 제약

- JSON 외 텍스트 금지
- `action_plan`은 1~4단계
- 각 단계는 반드시 아래 필드 포함
  - `action`: `excel_live.*`
  - `params`: 객체(dict)
  - `reason`: 한 줄 한국어
- 범위가 모호하면 `__ACTIVE_SELECTION__` 또는 `context_range` 사용

## 정답 예시

```json
{
  "intent": "edit",
  "mutates_workbook": true,
  "action_plan": [
    {
      "action": "excel_live.sort_range",
      "params": {
        "target_range": "A1:E200",
        "key_column": "금액",
        "order": "desc",
        "has_header": true
      },
      "reason": "금액 열 기준 내림차순 정렬"
    }
  ],
  "slot_fill": {},
  "partial_params": {},
  "follow_up_question": "",
  "reason": "한 줄 한국어"
}
```

## 금지 규칙

- `excel_live.*` 외 action
- `params`가 문자열/배열인 형태
- 빈 `action_plan`
- 범위를 전혀 지정하지 않은 파괴적 작업
