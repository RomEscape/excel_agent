# 관측 모드 세 팔 비교

- `before` — 실행 `0811-182610-armA-off` · 오라클 5개 중 어긋남 5개 → **요청 이행 0/5**
- `after` — 실행 `0811-204514-after-task7b` · 오라클 5개 중 어긋남 5개 → **요청 이행 0/5**

| 케이스 | before | after |
|---|---|---|
| 빈칸행삭제 | 어긋남 3/3<br>`clear_range`<br>2843ms | 어긋남 3/3<br>`clear_range`<br>3012ms |
| 빈칸채우기 | 어긋남 3/3<br>`fill_range`<br>2463ms | 어긋남 3/3<br>`fill_range`<br>2628ms |
| 이상치강조 | 어긋남 3/3<br>`highlight_by_condition`<br>5906ms | 어긋남 3/3<br>`highlight_by_condition`<br>3069ms |
| 이상치삭제 | 어긋남 3/3<br>`clear_range`<br>2812ms | 어긋남 3/3<br>`clear_range`<br>2941ms |
| 문자열숫자 | 어긋남 3/3<br>`clarify`<br>10557ms | 어긋남 3/3<br>`clarify`<br>11227ms |

## 경로

### 빈칸행삭제
- `before` 3회 — quick_rule:miss → planner:local → final:ok
- `after` 3회 — quick_rule:miss → planner:local → final:ok

### 빈칸채우기
- `before` 3회 — quick_rule:miss → planner:local → final:ok
- `after` 2회 — quick_rule:miss → planner:local → final:ok
- `after` 1회 — quick_rule:miss → planner:failed

### 이상치강조
- `before` 3회 — quick_rule:miss → planner:local → verify:failed → verify:failed → replan:1 → final:ok
- `after` 3회 — quick_rule:miss → planner:local → final:ok

### 이상치삭제
- `before` 3회 — quick_rule:miss → planner:local → final:ok
- `after` 3회 — quick_rule:miss → planner:local → final:ok

### 문자열숫자
- `before` 3회 — quick_rule:miss → planner:failed → final:asked_back
- `after` 3회 — quick_rule:miss → planner:failed → final:asked_back
