# AI + xlwings Excel 자동화 데모

## 파일 구성

- `AI_Excel_Automation_Demo.xlsx`
  - `Dashboard`: 매출·이익·배송·재고·프로젝트 KPI와 차트
  - `Sales_Data`: 180건 주문, 수식·조건부서식·데이터 검증·Excel Table
  - `Inventory`: 재주문 판단, 권장 발주수량, 재고가치
  - `Project_Plan`: 일정·진행률·지연 위험
  - `AI_Command_Center`: 자연어 명령 예시, 에이전트 구조, JSON 계획 예시
  - `Lookup`: 목록형 입력 기준값
- `ai_excel_agent_xlwings.py`
  - 자연어 명령을 구조화 계획으로 바꾸는 데모
  - 허용된 작업만 실행하는 화이트리스트 실행기
  - 수정 전 자동 백업
  - `xlwings>=0.33.0` 사용

## 설치

```bash
pip install "xlwings>=0.33.0"
```

오픈소스 xlwings의 일반적인 데스크톱 자동화 모드는 Windows 또는 macOS에 Microsoft Excel이 설치되어 있어야 합니다.

## 실행

두 파일을 같은 폴더에 둔 뒤:

```bash
python ai_excel_agent_xlwings.py
```

기본 데모 명령:

```text
지역별 매출과 이익을 집계해서 새 시트와 차트를 만들어줘.
```

## 권장 실제 구조

1. xlwings로 워크북의 시트명, 표 헤더, 사용 범위, 수식 여부만 읽습니다.
2. LLM에 사용자 명령과 최소한의 워크북 메타데이터를 전달합니다.
3. LLM은 Python 코드를 생성하지 않고 `ExcelActionPlan` JSON만 반환합니다.
4. JSON Schema/Pydantic으로 시트·범위·작업 종류를 검증합니다.
5. 화이트리스트에 등록된 xlwings 함수만 실행합니다.
6. 수정 전 백업하고, 수정 후 합계·행 수·수식 오류를 검증합니다.
7. 명령·계획·결과를 감사 로그에 남깁니다.

## 중요한 원칙

LLM이 생성한 임의 Python, VBA, COM 호출을 직접 `exec()`하지 마십시오.  
파괴적 작업(시트 삭제, 대량 덮어쓰기, 외부 링크 변경, 매크로 실행)은 별도 승인 정책을 두는 것이 안전합니다.
