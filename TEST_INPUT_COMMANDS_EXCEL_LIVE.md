# Excel Live 테스트 입력 명령어 (간소화판)

기존 장문 목록을 빠르게 점검할 수 있도록 핵심 시나리오만 남긴 버전입니다.

## 사용 방법

- 각 항목 형식: `입력 문장 -> 기대 액션`
- 기본 점검 순서: **코어 스모크 -> 멀티턴 -> 안전/복구 -> 최근 이슈 재검증**
- 승인 작업(`approval_required`)은 UI 승인 후 실행 확인

---

## 1) 코어 스모크 (단일턴)

### A. 워크북/조회

1. `열린 통합문서 목록 보여줘` -> `excel_live.list_workbooks`
2. `워크북 sales.xlsx 선택` -> `excel_live.select_workbook`
3. `A1:C10 조회해줘` -> `excel_live.read_range`
4. `B열 보여줘` -> `excel_live.read_range`

### B. 입력/서식

5. `C3에 120 입력해줘` -> `excel_live.write_range`
6. `B2:D2에 이름,수량,금액 입력` -> `excel_live.write_range`
7. `A열에서 50 이상인 셀만 노란색 배경 적용` -> `excel_live.highlight_by_condition`
8. `표 색을 전반적으로 노랗게 칠해줘` -> `excel_live.fill_range`
9. `B2:D5 범위에 경계선 적용해줘` -> `excel_live.apply_border`
10. `5 * 5 표를 하나 만들어줘` -> `excel_live.create_table`

### C. 정렬/필터/집계/시각화

11. `매출 높은 순으로 정렬해줘` -> `excel_live.sort_range`
12. `완료된 것만 보고 싶어` -> `excel_live.filter_rows`
13. `중복된 거 지워줘` -> `excel_live.dedupe_rows`
14. `부서별 비용 집계표 만들어줘` -> `excel_live.pivot_table`
15. `월별 매출 그래프로 만들어줘` -> `excel_live.create_chart`

### D. 수식/검증

16. `C1에 B2:B20 합계 수식 넣어줘` -> `excel_live.set_formula`
17. `D2:D50 수식 결과 값 확인해줘` -> `excel_live.verify_formula_result`
18. `이상한 값 있는지 점검해줘` -> `excel_live.validate_data`

---

## 2) 멀티턴 스모크 (질문-응답-실행)

19. `정렬해줘` -> 열/순서 확인 후 `excel_live.sort_range`
20. `중복 없애줘` -> 기준 열 확인 후 `excel_live.dedupe_rows`
21. `피벗으로 만들어줘` -> 행/값 기준 확인 후 `excel_live.pivot_table`
22. `그래프로 만들어줘` -> 차트 종류 확인 후 `excel_live.create_chart`
23. `수량이랑 가격 곱해서 금액 나오게 해줘` -> 열 확인 후 `set_formula + verify_formula_result`
24. `코드 기준으로 가격 찾아와` -> 조회열/참조범위/반환열 확인 후 `set_formula + verify_formula_result`

---

## 3) 안전/복구 스모크

25. `수식 있는 칸 잠그고 입력칸만 수정 가능하게 해줘` -> `excel_live.protect_sheet`
26. `상태 열은 완료,진행중,지연 드롭다운으로 제한해줘` -> `excel_live.set_data_validation`
27. `Power Query 새로고침해줘` -> `excel_live.refresh_power_query`
28. `Module1.RefreshReport 매크로 실행해줘` -> `excel_live.run_vba_macro`
29. `원본시트 A2:D100 과 변경시트 A2:D100 차이 비교해줘` -> `excel_live.compare_ranges`
30. `B2:B25 기준으로 6개월 예측해줘` -> `excel_live.forecast_linear`
31. `최근 백업에서 되돌려줘` -> `excel_live.restore_last_backup`
32. `파일이 읽기 전용이라 수정이 안 돼` -> 안전 확인 follow-up (`excel_live.safety`)

---

## 4) 최근 이슈 재검증 (필수)

33. `엑셀을 전체 다 지워줘` -> `excel_live.clear_range` (전체 초기화 의도 인식)
34. `안에 내용 전부 지우고 깨끗하게 만들어줘` -> `excel_live.clear_range`
35. `대시보드처럼 한눈에 정리해줘` -> 차트/요약 후속 질문 또는 `excel_live.create_chart`
36. `뭔가 이상한 명령` -> 엑셀 의도 없으면 `HTTP 400`, 엑셀 의도면 `excel_live.clarify`

---

## 5) 대량 러프 테스트

- 스크립트: `python-sidecar/scripts/smoke_excel_live_nl.py`
- 목적: 광범위 자연어 입력에 대한 회귀 점검
- 권장: 배포 전 1회 실행 + 실패 케이스만 위 목록에 추가

