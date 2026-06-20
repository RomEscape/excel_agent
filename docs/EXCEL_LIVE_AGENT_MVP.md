# Excel Live Agent MVP (로컬 데스크톱 + COM)

> 목표: 사용자가 자연어로 명령하면, **열려 있는 Excel 화면이 즉시 편집**되어 결과를 바로 확인한다.
> 범위: Windows 데스크톱 우선, officeclaw 기존 `/agent/chat` + sidecar 보안 레이어 재사용.

---

## 1) 제품 목표와 성공 기준

- 사용자가 `Workspace 채팅`에 자연어 명령 입력 후 3~8초 내 첫 반응을 본다.
- Excel이 열려 있으면 셀 값/서식/수식 변경이 화면에 즉시 반영된다.
- 위험 작업(대량 덮어쓰기, 삭제)은 항상 승인(`CONFIRM`)을 거친다.
- 모든 작업은 감사 로그에 남고, 실패 시 원인 메시지를 한국어로 반환한다.

---

## 2) 단일 트랙 아키텍처 (MVP)

1. React/Tauri 채팅 UI에서 사용자 명령 입력
2. Python sidecar `/agent/chat`로 전달
3. 기존 보안 계층(DENIED/마스킹/감사) 통과
4. OpenClaw가 LLM Function Calling으로 `excel_live_*` 도구 선택
5. `excel_live_service`가 `xlwings + pywin32(COM)`으로 실행 중인 Excel 제어
6. 실행 결과(변경 수, 대상 범위, 오류) 반환 + 감사 기록

### 도메인 모듈 배치 (프로젝트 규칙 준수)

- `python-sidecar/office_claw_sidecar/services/excel_live_service.py`
  - Excel COM 연결/조회/편집 로직 소유
- `python-sidecar/office_claw_sidecar/services/excel_live_tools.py`
  - LLM tool schema + handler 바인딩
- `python-sidecar/office_claw_sidecar/services/tool_registry.py`
  - SAFE/CONFIRM/DENIED 정책 등록
- `python-sidecar/office_claw_sidecar/routers/agent.py`
  - 기존 `/agent/chat` 파이프라인 유지 (도구만 추가)
- `src/components/workspace/*`
  - 승인 필요 시 명확한 확인 다이얼로그 표기 (기존 Approval 흐름 재사용)

---

## 3) MVP 도구 6개 (Function Calling 스펙)

### 3.1 `excel_live_list_workbooks` (SAFE)
- 역할: 현재 Excel 인스턴스에 열린 통합문서 목록 조회
- 입력: 없음
- 출력: `[{ workbook_id, name, full_path, active_sheet }]`

### 3.2 `excel_live_select_workbook` (SAFE)
- 역할: 작업 대상 통합문서 지정
- 입력: `workbook_id | name`
- 출력: `{ selected: true, workbook_id }`

### 3.3 `excel_live_read_range` (SAFE)
- 역할: 범위 읽기 (LLM이 후속 명령 전에 상태 확인)
- 입력: `{ workbook_id, sheet_name, range_ref }`
- 출력: `{ values, address, row_count, col_count }`

### 3.4 `excel_live_write_range` (CONFIRM)
- 역할: 셀 값 일괄 쓰기
- 입력: `{ workbook_id, sheet_name, start_cell, values_2d }`
- 출력: `{ written_cells, address }`
- CONFIRM 기준: `written_cells >= 50` 또는 기존 값 덮어쓰기 비율이 높을 때

### 3.5 `excel_live_highlight_by_condition` (CONFIRM)
- 역할: 조건에 맞는 셀 서식 변경 (예: A열 50 이상 노란색)
- 입력: `{ workbook_id, sheet_name, target_range, operator, threshold, fill_color }`
- 출력: `{ matched_cells, changed_cells }`
- CONFIRM 기준: `changed_cells >= 100`

### 3.6 `excel_live_set_formula` (CONFIRM)
- 역할: 범위에 수식 채우기
- 입력: `{ workbook_id, sheet_name, range_ref, formula_a1 }`
- 출력: `{ formula_applied_cells }`
- CONFIRM 기준: 항상 승인 (수식 오염 리스크)

---

## 4) 승인 정책 (SAFE / CONFIRM / DENIED)

### SAFE
- 조회형만 허용: 목록/선택/읽기
- 즉시 실행, 감사 로그 기록

### CONFIRM
- 데이터 변경/서식 변경/수식 적용
- 승인 UI 문구 예시:
  - "Sheet1 A1:D200 범위의 값을 수정하려고 합니다. 계속할까요?"
- 60초 타임아웃 시 자동 거부

### DENIED
- MVP 범위 밖 명령은 차단
- 예: VBA 매크로 실행, 외부 URL 다운로드 후 실행, 임의 파일 시스템 접근

---

## 5) 런타임/배포 설계 (설치 후 즉시 동작)

### 런타임
- Tauri 앱 실행 시 sidecar 자동 기동
- sidecar 시작 시 `excel_live_service` 사전 체크:
  - Excel 설치 여부
  - COM 접근 가능 여부
  - `xlwings`, `pywin32` import 확인

### 배포
- Windows 인스톨러에 포함:
  - Tauri 앱
  - Python sidecar 런타임
  - `xlwings`, `pywin32`
- 첫 실행 진단:
  - "Excel 연결 테스트" 버튼 제공
  - 샘플 명령 1회 (`A열 50 이상 노란색`) 자동 점검 옵션

---

## 6) 1주 구현 플랜

### Day 1 — 서비스 골격
- `excel_live_service.py` 생성 (COM attach/list/read 기본)
- 오류 타입 표준화 (`ExcelNotRunningError`, `WorkbookNotFoundError` 등)

### Day 2 — 도구 3개 SAFE 구현
- `list_workbooks`, `select_workbook`, `read_range`
- `/agent/chat` 경유 스모크 테스트

### Day 3 — 변경 도구 2개 구현
- `write_range`, `highlight_by_condition`
- CONFIRM 정책 연결

### Day 4 — 수식 도구 + 롤백 보조
- `set_formula` 구현
- 실행 전 스냅샷(메모리/임시 파일) 최소 기능 추가

### Day 5 — UI/승인/문구 개선
- 승인 다이얼로그 메시지 정교화
- 실패 메시지 한국어 표준화

### Day 6 — 테스트/문서
- Python 단위 테스트 + 통합 시나리오 테스트
- 사용자 가이드(샘플 프롬프트 10개) 작성

### Day 7 — 안정화/릴리즈 후보
- 예외 케이스 정리(병합셀, 보호 시트, 비어있는 시트)
- 성능 점검(대량 범위)
- 릴리즈 체크리스트 완료

---

## 7) MVP 완료 정의 (Definition of Done)

- 자연어 시나리오 5개가 연속 성공:
  - "A열 50 이상 노란색"
  - "B2:D2에 헤더 작성"
  - "E열 합계 수식 채우기"
  - "현재 열린 파일 목록 보여줘"
  - "Sheet2 C1:C20 값 읽어줘"
- CONFIRM 동작/타임아웃/거부 분기 모두 검증
- 감사 로그에 요청/도구/결과가 누락 없이 기록
- 앱 재실행 후에도 동일 워크플로우 재현 가능

---

## 8) Phase 2/3 확장 포인트

- Phase 2: 파일 기반 `openpyxl` 백업 모드 (Excel 미실행/원격 환경 대응)
- Phase 3: Graph API 무인 자동화 + Office Add-in 크로스플랫폼 UI

