# 관측 모드 세 팔 비교

- `off` — 실행 `0811-182610-armA-off` · 오라클 5개 중 어긋남 5개 → **요청 이행 0/5**
- `read_first` — 실행 `0811-182750-armB-readfirst` · 오라클 5개 중 어긋남 5개 → **요청 이행 0/5**
- `loop` — 실행 `0811-182858-armC-loop` · 오라클 5개 중 어긋남 5개 → **요청 이행 0/5**

| 케이스 | off | read_first | loop |
|---|---|---|---|
| 빈칸행삭제 | 어긋남 3/3<br>`clear_range`<br>2843ms | 어긋남 3/3<br>`clear_range`<br>2745ms | 어긋남 3/3<br>`clear_range`<br>2790ms |
| 빈칸채우기 | 어긋남 3/3<br>`fill_range`<br>2463ms | 어긋남 3/3<br>`fill_range`<br>2473ms | 어긋남 3/3<br>`fill_range`<br>2476ms |
| 이상치강조 | 어긋남 3/3<br>`highlight_by_condition`<br>5906ms | 어긋남 3/3<br>`highlight_by_condition`<br>5853ms | 어긋남 3/3<br>`highlight_by_condition`<br>5873ms |
| 이상치삭제 | 어긋남 3/3<br>`clear_range`<br>2812ms | 어긋남 3/3<br>`clear_range`<br>2816ms | 어긋남 3/3<br>`clear_range`<br>2817ms |
| 문자열숫자 | 어긋남 3/3<br>`clarify`<br>10557ms | 어긋남 3/3<br>`validate_data`<br>2622ms | 어긋남 3/3<br>`validate_data`<br>5110ms |

## 경로

### 빈칸행삭제
- `off` 3회 — quick_rule:miss → planner:local → final:ok
- `read_first` 3회 — quick_rule:miss → planner:local → final:ok
- `loop` 3회 — quick_rule:miss → planner:local → final:ok

### 빈칸채우기
- `off` 3회 — quick_rule:miss → planner:local → final:ok
- `read_first` 3회 — quick_rule:miss → planner:local → final:ok
- `loop` 2회 — quick_rule:miss → planner:local → final:ok
- `loop` 1회 — quick_rule:miss → planner:local_repair → final:ok

### 이상치강조
- `off` 3회 — quick_rule:miss → planner:local → verify:failed → verify:failed → replan:1 → final:ok
- `read_first` 3회 — quick_rule:miss → planner:local → verify:failed → verify:failed → replan:1 → final:ok
- `loop` 3회 — quick_rule:miss → planner:local → verify:failed → verify:failed → replan:1 → final:ok

### 이상치삭제
- `off` 3회 — quick_rule:miss → planner:local → final:ok
- `read_first` 3회 — quick_rule:miss → planner:local → final:ok
- `loop` 3회 — quick_rule:miss → planner:local → final:ok

### 문자열숫자
- `off` 3회 — quick_rule:miss → planner:failed → final:asked_back
- `read_first` 3회 — quick_rule:miss → planner:local → final:ok
- `loop` 3회 — quick_rule:miss → planner:local → observe:1 → final:ok
