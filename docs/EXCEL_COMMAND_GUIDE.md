# Excel 명령 가이드 — 무엇이 되고, 어떻게 말해야 하나

이 문서는 "지금 실제로 되는 것"만 적는다. 근거는 `datasets/excel_complex_scenarios_v1.json`(30개),
`datasets/excel_demo_workbook_scenarios_v1.json`(15개) 시나리오 검증 결과와
`python-sidecar/office_claw_sidecar/services/tool_registry.py`의 액션 등록표다.

---

## 0. 먼저 알아야 할 것 — 에이전트는 3층으로 판단한다

| 층 | 하는 일 | 언제 타나 |
|---|---|---|
| 규칙 (rule) | 정규식으로 바로 액션 확정 | "C3에 120 입력해줘"처럼 대상이 명확할 때. **가장 빠르고 가장 정확** |
| 플래너 (LLM) | 통합문서 머리글을 보고 1~4단계 계획 수립 | 규칙이 못 잡은 문장 |
| 매크로 | 한 문장을 여러 하위 명령으로 분해 → 승인 → 순차 실행 | "대시보드 만들어줘"처럼 결과물이 복합일 때 |

**어느 층을 탔는지, 무슨 계획을 세웠는지는 `logs/chat_log.jsonl`에 턴마다 기록된다.**
읽는 법은 이 문서 4장.

---

## 1. 잘 되는 명령 (검증 통과 기준)

### 1-1. 값 읽기·쓰기 — 성공률 가장 높음

```
C3에 120 입력해줘
B2:D10 값 읽어줘
A1:C1에 이름, 수량, 금액 써줘
```

### 1-2. 표 만들기

```
5*5 표 만들어줘
4행 3열 표 만들어줘, 금액, 장소, 날짜
금액, 장소, 날짜, 요건, 비고 헤더로 표 만들어줘
```

- 크기 표기는 `5*5`, `5x5`, `4행 3열`, `3열 4행`, `가로 3 세로 4`, `행 4개 열 3개` 모두 인식한다.
- 헤더만 주면 열 수는 헤더 개수로 잡는다.
- 크기를 두 번 물어도 못 알아들으면 기본값(헤더 있으면 10행, 없으면 5*5)으로 만들고 그렇게 말해 준다.

### 1-3. 서식

```
A열에서 50 이상인 셀만 노란색 배경 적용
단가가 100만원 미만인 건은 노란색으로 표시해줘
B2:D10에 테두리 넣어줘
금액 열 통화 형식으로 바꿔줘
```

> 주의: **수식으로 계산된 열**(예: 매출 = 수량*단가)에 조건부 서식을 걸면 0셀이 적용될 수 있다.
> 파일 엔진은 수식의 계산 결과 캐시를 못 읽는 경우가 있다. 이때는 값이 직접 들어 있는 열을 기준으로 말하는 게 확실하다.

### 1-4. 정렬 · 필터 · 중복

```
금액 열 기준 내림차순으로 정렬해줘
매출 데이터를 주문일자 오름차순으로 정렬해줘
금액이 1000 이상인 행만 남겨줘
코드 열 기준으로 중복 제거해줘
```

- **"정렬해줘"처럼 기준이 없으면 되묻는다.** 열 이름을 같이 말하면 한 번에 끝난다.
  정렬·중복제거는 기준을 잘못 잡으면 데이터가 조용히 뒤섞이므로, 원문에 기준이 없으면
  플래너가 채워 넣은 열 이름은 무시하고 반드시 물어본다.

### 1-5. 수식

```
C1에 B2:B20 합계 수식 넣어줘
이익률 열에 매출이익 나누기 매출 수식을 넣어줘
I1:I10에 수식 =A1*2 적용해줘
```

### 1-6. 집계 · 비교

```
지역별 매출 합계를 집계해서 Regional_Report 시트에 만들어줘
제품 분류마다 매출이 얼마나 나오는지 Cat_Sum 시트로 뽑아줘
A열과 C열 비교해서 다른 것만 알려줘
```

- **결과를 쓸 시트 이름을 반드시 같이 말할 것.** 원본 시트에 덮어쓰면 데이터가 사라진다.

### 1-7. 구조 편집

```
경기를 경기도로 바꿔줘
B열 삭제해줘
수량 열 이름을 판매수량으로 바꿔줘
비고 열 추가해줘
1행 틀 고정해줘
열 너비 자동 조정해줘
```

---

## 2. 잘 안 되는 명령 (실측 실패 사례)

| 명령 유형 | 실제 실패 예 | 왜 |
|---|---|---|
| 차트 | `"매출 시트 A1:E9 데이터를 차트로 만들어줘"` → `pandas 엔진에서는 차트 생성을 지원하지 않습니다.` | 파일 엔진에 차트 미지원. Excel이 실제로 열려 있어야 함 |
| 피벗 (복잡) | `"월을 행으로, 카테고리를 열로, 금액 합계 피벗을 피벗1 시트 A1에 만들어줘"` → `Grouper for '1' not 1-dimensional` | 행/열 필드 바인딩 실패 |
| 드롭다운 제한 | `"F2:F200은 완료,진행중,지연만 선택되도록 제한해줘"` → `create_table`로 오해 | 의도 분류 오류 |
| 여러 시트 통합 | `"1분기,2분기,3분기 시트를 통합1로 합쳐줘"` → `시트를 찾을 수 없습니다: 1분기` | 시트명 파싱 실패 |
| 연쇄 지시 | `"금액 자동 계산하고 검증한 다음 입력값 범위 제한까지 한 번에"` → 중간 단계 누락 | 한 계획에 4단계 초과 |
| 모호한 지시 | `"조건에 맞는 것만 보여줘"` → 0행 필터 | 조건이 문장에 없음 |

**연쇄 지시는 매크로 층이 받아 여러 명령으로 쪼개는 게 정답이다.** 한 문장에 3개 이상의 작업을 넣지 말고,
"대시보드 만들어줘"처럼 결과물 이름으로 말하거나 한 번에 하나씩 시키는 편이 성공률이 높다.

---

## 3. 성공률을 올리는 말하기 규칙

1. **대상을 지목한다.** "금액 열", "B2:D10", "매출 시트" — 대명사("그거", "저기")는 피한다.
2. **기준을 함께 준다.** "정렬해줘"(되묻음) → "금액 열 기준 내림차순으로 정렬해줘"(바로 실행).
3. **결과를 쓸 위치를 준다.** 집계·피벗·예측·비교는 출력 시트를 지정한다.
4. **한 문장에 한 작업.** 3개 이상 붙이면 중간 단계가 조용히 빠진다.
5. **머리글 이름을 그대로 쓴다.** 통합문서에 `Sales`라고 적혀 있으면 "매출"이라 불러도 되지만,
   `Sales`라고 하면 더 확실하다.
6. **되묻는 질문에는 짧게 답한다.** "4열 3행", "금액 열 기준", "Report 시트" 정도면 충분하다.

---

## 4. 무슨 일이 일어났는지 확인하는 법

턴마다 `logs/chat_log.jsonl`에 JSON 한 줄이 쌓인다. 필드는 이렇다.

| 필드 | 내용 |
|---|---|
| `message` | 사용자가 실제로 보낸 문장 |
| `request` | 대상 통합문서·시트·승인 여부 |
| `stages[].understand` | 규칙이 무엇으로 알아들었는지 (의도, 표 크기, 헤더, 대기 슬롯) |
| `stages[].planner` | 어떤 모델이 어떤 계획을 냈는지, 실패했으면 그 오류 |
| `stages[].macro_plan` | 매크로가 몇 개 하위 명령으로 쪼갰는지 |
| `stages[].table_slot` | 표 슬롯이 몇 번 되물었고 무엇이 비어 있었는지 |
| `stages[].plan_final` | **실제로 실행에 넘어간 계획** (액션 + 파라미터) |
| `stages[].executed` | 단계별 성공/실패, 오류 메시지, 바뀐 범위 |
| `outcome` | 사용자에게 나간 응답, 되묻기 여부, 실패 사유 |

읽는 명령:

```bash
cd python-sidecar
uv run python scripts/show_chat_log.py            # 최근 10턴 요약
uv run python scripts/show_chat_log.py -n 30      # 최근 30턴
uv run python scripts/show_chat_log.py --failed   # 실패·되묻기로 끝난 턴만
uv run python scripts/show_chat_log.py --grep 표   # '표'가 들어간 명령만
uv run python scripts/show_chat_log.py --raw -n 1 # 원본 JSON
```

명령 묶음을 한 번에 돌려 회귀를 확인하려면 (사이드카가 떠 있어야 한다):

```bash
uv run python scripts/run_command_battery.py
```

**"왜 안 됐지?"를 확인하는 순서**

1. `--failed`로 해당 턴을 찾는다.
2. `understand`를 본다 → 질문 자체를 잘못 알아들었으면 말하기 규칙(3장) 문제다.
3. `plan_final`을 본다 → 계획이 엉뚱하면 플래너/규칙 문제다.
4. `executed`를 본다 → 계획은 맞는데 오류가 났으면 엔진(파일/xlwings) 문제다.

이 세 곳 중 어디서 틀어졌는지가 곧 고쳐야 할 층이다.

---

## 5. 지원 액션 전체 목록

권한은 `tool_registry.py` 기준. CONFIRM은 실행 전 승인 카드가 뜬다.

**SAFE (바로 실행)** — `list_workbooks` `select_workbook` `list_sheets` `select_sheet` `create_sheet`
`read_range` `verify_formula_result` `find_duplicates` `recalculate` `compare_ranges` `save_workbook`
`calculate_column_stat` `group_by_aggregate` `validate_data`

**CONFIRM (승인 후 실행)** — `write_range` `create_table` `highlight_by_condition` `fill_range`
`clear_range` `apply_border` `set_formula` `sort_range` `filter_rows` `sort_rows` `dedupe_rows`
`drop_column` `rename_column` `add_column` `find_replace` `merge_cells` `unmerge_cells` `freeze_panes`
`autofit_columns` `define_named_range` `set_print_area` `add_cell_comment` `apply_color_scale`
`apply_data_bar` `set_number_format` `export_pdf` `pivot_table` `create_chart` `protect_sheet`
`set_data_validation` `consolidate_sheets` `consolidate_workbooks_from_folder` `refresh_power_query`
`run_vba_macro` `forecast_linear`

모든 액션 이름은 `excel_live.` 접두사가 붙는다 (예: `excel_live.write_range`).
