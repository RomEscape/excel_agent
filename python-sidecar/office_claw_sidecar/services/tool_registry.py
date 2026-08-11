"""
Tool Registry — 텔레그램 에이전트의 샌드박스 화이트리스트.

샌드박스 원칙:
  이 레지스트리에 등록된 도구만 실행 가능.
  등록되지 않은 모든 작업(파일 삭제, 셸 실행 등)은 물리적으로 불가.

PermissionLevel:
  SAFE    — 즉시 실행 (조회, 읽기, AI 생성)
  CONFIRM — 텔레그램으로 사용자 확인 후 실행
  DENIED  — 항상 거부 (위험 작업 명시적 차단)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum


class PermissionLevel(Enum):
    SAFE = "safe"
    CONFIRM = "confirm"
    DENIED = "denied"


@dataclass
class ToolDef:
    name: str
    description: str                  # 한국어 설명 (LLM 분류용)
    permission: PermissionLevel
    example_triggers: list[str] = field(default_factory=list)


# ── 화이트리스트 — 여기 없는 것은 실행 불가 ──────────────────────────────────

TOOL_REGISTRY: list[ToolDef] = [
    # ── 일반 대화 ────────────────────────────────────────────────────────────
    ToolDef(
        name="chat",
        description="일반 대화, 질문 답변, 정보 요청, 위의 어떤 도구에도 해당하지 않는 모든 메시지",
        permission=PermissionLevel.SAFE,
        example_triggers=["안녕", "질문", "설명해줘", "어떻게", "뭐야", "알려줘"],
    ),
    # ── 문서 생성 ────────────────────────────────────────────────────────────
    ToolDef(
        name="document.generate",
        description=(
            "보고서, 기획안, 회의록, 계약서초안, 이메일, 제안서 등 업무 문서 초안 작성. "
            "params: doc_type(보고서|기획안|회의록|계약서초안|이메일|제안서), "
            "content(핵심 내용), tone(공식적|친근한|전문적), length(짧게|보통|길게)"
        ),
        permission=PermissionLevel.SAFE,
        example_triggers=["보고서 작성", "기획안 써줘", "회의록", "이메일 초안", "문서 만들어줘", "제안서"],
    ),
    # ── 시스템 ───────────────────────────────────────────────────────────────
    ToolDef(
        name="status.check",
        description="시스템 상태 확인 (AI 엔진, 사이드카 상태)",
        permission=PermissionLevel.SAFE,
        example_triggers=["상태 확인", "시스템 상태", "연결 상태", "잘 돼?", "작동해?"],
    ),
    ToolDef(
        name="help",
        description="사용 가능한 명령어와 기능 목록 표시",
        permission=PermissionLevel.SAFE,
        example_triggers=["도움말", "명령어", "뭘 할 수 있어", "기능 목록"],
    ),
    # ── OpenClaw 스킬 매핑 (Phase 4) ─────────────────────────────────────────
    # GOG/GWS 스킬 — Google Sheets
    ToolDef(
        name="gog.sheets.read",
        description="Google Sheets 읽기 및 분석 (읽기 전용)",
        permission=PermissionLevel.SAFE,
        example_triggers=["시트 읽기", "스프레드시트", "구글 시트"],
    ),
    ToolDef(
        name="gog.sheets.write",
        description="Google Sheets 쓰기 및 수정 (쓰기 작업 포함)",
        permission=PermissionLevel.CONFIRM,
        example_triggers=["시트 쓰기", "스프레드시트 수정", "셀 업데이트"],
    ),
    # Excel 자동화 스킬
    ToolDef(
        name="excel.analyze",
        description="Excel 파일 분석 및 데이터 요약 (읽기 전용)",
        permission=PermissionLevel.SAFE,
        example_triggers=["엑셀 분석", "엑셀 읽기", "스프레드시트 분석"],
    ),
    ToolDef(
        name="excel.report",
        description="Excel 데이터 기반 보고서 생성",
        permission=PermissionLevel.SAFE,
        example_triggers=["엑셀 보고서", "데이터 보고서"],
    ),
    # Excel Live(COM) 도구 — 로컬 데스크톱 실시간 편집
    ToolDef(
        name="excel_live.list_workbooks",
        description="실행 중인 Excel의 열린 통합문서 목록 조회",
        permission=PermissionLevel.SAFE,
        example_triggers=["열린 엑셀 목록", "워크북 목록", "현재 엑셀 파일 보여줘"],
    ),
    ToolDef(
        name="excel_live.select_workbook",
        description="작업 대상 통합문서 선택 (읽기/수정 대상 지정)",
        permission=PermissionLevel.SAFE,
        example_triggers=["이 파일 선택", "워크북 선택", "작업 파일 지정"],
    ),
    ToolDef(
        name="excel_live.list_sheets",
        description="선택한 통합문서의 시트 목록과 활성 시트 조회",
        permission=PermissionLevel.SAFE,
        example_triggers=["시트 목록", "탭 목록", "현재 시트들 보여줘"],
    ),
    ToolDef(
        name="excel_live.select_sheet",
        description="작업 시트를 전환/활성화",
        permission=PermissionLevel.SAFE,
        example_triggers=["Sheet2로 이동", "요약 시트 선택", "시트 전환"],
    ),
    ToolDef(
        name="excel_live.create_sheet",
        description="새 시트를 생성하고 필요 시 활성화",
        permission=PermissionLevel.SAFE,
        example_triggers=["요약 시트 만들어줘", "새 탭 추가", "시트 생성"],
    ),
    ToolDef(
        name="excel_live.read_range",
        description="지정 시트/범위의 셀 값을 읽기",
        permission=PermissionLevel.SAFE,
        example_triggers=["범위 읽기", "셀 값 보여줘", "A1:C10 읽어줘"],
    ),
    ToolDef(
        name="excel_live.write_range",
        description="지정 범위에 셀 값 쓰기(수정)",
        permission=PermissionLevel.CONFIRM,
        example_triggers=["셀 값 수정", "값 입력", "범위 덮어쓰기"],
    ),
    ToolDef(
        name="excel_live.create_table",
        description="지정 시작 셀 기준으로 표(행/열) 생성 및 기본 경계선 적용",
        permission=PermissionLevel.CONFIRM,
        example_triggers=["5x5 표 만들어줘", "표 생성", "테이블 만들기"],
    ),
    ToolDef(
        name="excel_live.highlight_by_condition",
        description="조건에 맞는 셀 서식(배경색) 변경",
        permission=PermissionLevel.CONFIRM,
        example_triggers=["조건부 색칠", "노란색으로 칠해줘", "50 이상 강조"],
    ),
    ToolDef(
        name="excel_live.fill_range",
        description="지정 범위 전체를 단일 배경색으로 채우기",
        permission=PermissionLevel.CONFIRM,
        example_triggers=["전체 노란색", "표 색을 전반적으로 바꿔줘", "배경색 채우기"],
    ),
    ToolDef(
        name="excel_live.clear_range",
        description="지정 범위의 값/수식을 비우기 (서식 유지)",
        permission=PermissionLevel.CONFIRM,
        example_triggers=["안에 내용 전부 지워줘", "범위 비워줘", "깨끗하게 비워"],
    ),
    ToolDef(
        name="excel_live.apply_border",
        description="지정 범위에 경계선(테두리) 적용",
        permission=PermissionLevel.CONFIRM,
        example_triggers=["경계선 적용", "테두리 넣어줘", "border 적용"],
    ),
    ToolDef(
        name="excel_live.set_formula",
        description="지정 범위에 수식 적용",
        permission=PermissionLevel.CONFIRM,
        example_triggers=["수식 넣어줘", "합계 수식", "formula 적용"],
    ),
    ToolDef(
        name="excel_live.verify_formula_result",
        description="수식 결과 범위를 읽어 값 검증(개수/합계/평균/샘플) 수행",
        permission=PermissionLevel.SAFE,
        example_triggers=["수식 결과 확인", "값 검증", "계산 확인"],
    ),
    ToolDef(
        name="excel_live.sort_range",
        # sort_rows와 갈리는 지점은 "범위를 말했는가" 하나뿐이다. 예시 문구가 겹치면
        # 학습셋 라벨도 같이 겹쳐, 모델에게는 두 액션이 동전던지기가 된다.
        description="A1:D9처럼 지정한 범위를 열 기준으로 오름/내림차순 정렬 (범위를 명시한 경우)",
        permission=PermissionLevel.CONFIRM,
        example_triggers=["A1:F87 범위를 2번째 열 기준으로 정렬", "선택한 표 3번째 열로 정렬해줘"],
    ),
    ToolDef(
        name="excel_live.filter_rows",
        description="조건으로 행 필터 적용 (예: 상태=완료, 점수>=60)",
        permission=PermissionLevel.CONFIRM,
        example_triggers=["완료만 보여줘", "60점 미만 제외", "조건 필터"],
    ),
    ToolDef(
        name="excel_live.dedupe_rows",
        description="중복 행 제거 (기준 열 지정 가능)",
        permission=PermissionLevel.CONFIRM,
        example_triggers=["중복 제거", "전화번호 중복 지워줘"],
    ),
    ToolDef(
        name="excel_live.find_duplicates",
        description="중복 값을 제거하지 않고 어디에 몇 건 있는지 찾아 보고",
        permission=PermissionLevel.SAFE,
        example_triggers=["중복 주문번호 찾아줘", "겹치는 값 있는지 확인", "중복 점검"],
    ),
    ToolDef(
        name="excel_live.recalculate",
        description="수식 캐시를 무효화해 다음 열람 시 전체 재계산되도록 표시",
        permission=PermissionLevel.SAFE,
        example_triggers=["대시보드 갱신", "수식 새로고침", "최신 데이터로 업데이트"],
    ),
    ToolDef(
        name="excel_live.export_pdf",
        description="시트 또는 통합문서를 PDF로 내보내기 (Windows + Excel 필요)",
        permission=PermissionLevel.CONFIRM,
        example_triggers=["PDF로 저장해줘", "요약 시트 인쇄용으로 뽑아줘"],
    ),
    ToolDef(
        name="excel_live.pivot_table",
        description="행/값 기준 집계표(피벗 형태 요약표) 생성",
        permission=PermissionLevel.CONFIRM,
        example_triggers=["피벗 테이블", "부서별 합계", "월별 상품별 매출"],
    ),
    ToolDef(
        name="excel_live.create_chart",
        description="지정 범위로 차트(선/막대/원형) 생성",
        permission=PermissionLevel.CONFIRM,
        example_triggers=["차트 만들어줘", "그래프로 보여줘", "막대그래프 생성"],
    ),
    ToolDef(
        name="excel_live.validate_data",
        description="범위 데이터 품질 점검 (빈값/음수/이상치/날짜 범위)",
        permission=PermissionLevel.SAFE,
        example_triggers=["이상한 값 찾아줘", "데이터 검증", "오류 행 점검"],
    ),
    ToolDef(
        name="excel_live.protect_sheet",
        description="시트 보호/잠금 설정 (수식 셀 잠금, 입력 가능 범위 지정)",
        permission=PermissionLevel.CONFIRM,
        example_triggers=["수식 칸 보호", "입력칸만 수정 가능", "시트 잠금"],
    ),
    ToolDef(
        name="excel_live.set_data_validation",
        description="입력 제한/드롭다운 유효성 규칙 설정",
        permission=PermissionLevel.CONFIRM,
        example_triggers=["드롭다운 만들어줘", "숫자만 입력", "날짜만 입력"],
    ),
    ToolDef(
        name="excel_live.consolidate_sheets",
        description="같은 통합문서 내 여러 시트를 하나로 통합",
        permission=PermissionLevel.CONFIRM,
        example_triggers=["시트 여러 개 합쳐줘", "월별 시트 통합"],
    ),
    ToolDef(
        name="excel_live.consolidate_workbooks_from_folder",
        description="폴더 내 여러 Excel 파일을 현재 통합문서로 통합",
        permission=PermissionLevel.CONFIRM,
        example_triggers=["파일 여러 개 합쳐줘", "폴더 파일 통합"],
    ),
    ToolDef(
        name="excel_live.refresh_power_query",
        description="통합문서의 Power Query/연결(RefreshAll) 새로고침",
        permission=PermissionLevel.CONFIRM,
        example_triggers=["파워쿼리 새로고침", "연결 데이터 업데이트"],
    ),
    ToolDef(
        name="excel_live.run_vba_macro",
        description="지정한 VBA 매크로를 실행",
        permission=PermissionLevel.CONFIRM,
        example_triggers=["매크로 실행", "VBA 돌려줘"],
    ),
    ToolDef(
        name="excel_live.compare_ranges",
        description="두 범위를 비교해 차이 셀/값 목록 생성",
        permission=PermissionLevel.SAFE,
        example_triggers=["두 표 비교", "차이 찾아줘", "diff"],
    ),
    ToolDef(
        name="excel_live.forecast_linear",
        description="시계열 숫자 데이터의 단순 선형 예측 생성",
        permission=PermissionLevel.CONFIRM,
        example_triggers=["다음 달 예측", "추세 예측", "시뮬레이션"],
    ),
    ToolDef(
        name="excel_live.save_workbook",
        description="현재 엑셀 통합문서를 디스크에 저장",
        permission=PermissionLevel.SAFE,
        example_triggers=["엑셀 저장", "통합문서 저장", "지금 파일 저장"],
    ),
    ToolDef(
        name="excel_live.calculate_column_stat",
        description="지정 열(머리글 이름 또는 열 문자)의 숫자 통계 계산 (합계/평균/최소/최대/개수, 읽기 전용)",
        permission=PermissionLevel.SAFE,
        example_triggers=["매출 열 다 더해줘", "나이 평균 구해줘", "B열 합계"],
    ),
    ToolDef(
        name="excel_live.filter_rows",
        description="조건에 맞는 행만 남기고 나머지 데이터 행 삭제 (예: 매출 500만 이상만 남기기)",
        permission=PermissionLevel.CONFIRM,
        example_triggers=["이상인 행만 남겨줘", "조건 맞는 행만", "행 필터링"],
    ),
    ToolDef(
        name="excel_live.sort_rows",
        description="머리글 이름 기준으로 시트 전체 데이터 행 정렬 (범위를 말하지 않은 경우)",
        permission=PermissionLevel.CONFIRM,
        example_triggers=["금액 큰 순서대로", "재고금액 높은 순으로 정렬해줘", "단가 기준 내림차순으로"],
    ),
    ToolDef(
        name="excel_live.dedupe_rows",
        description="중복 데이터 행 제거 (첫 등장 행 유지)",
        permission=PermissionLevel.CONFIRM,
        example_triggers=["중복 제거", "중복 행 지워줘"],
    ),
    ToolDef(
        name="excel_live.drop_column",
        description="지정 열 삭제 (오른쪽 열이 왼쪽으로 이동)",
        permission=PermissionLevel.CONFIRM,
        example_triggers=["열 삭제", "컬럼 지워줘"],
    ),
    ToolDef(
        name="excel_live.rename_column",
        description="지정 열의 머리글 이름 변경",
        permission=PermissionLevel.CONFIRM,
        example_triggers=["열 이름 바꿔줘", "머리글 변경"],
    ),
    ToolDef(
        name="excel_live.add_column",
        description="테이블 오른쪽 끝에 새 열 추가 (선택적으로 수식 채움)",
        permission=PermissionLevel.CONFIRM,
        example_triggers=["열 추가", "새 컬럼 만들어줘"],
    ),
    ToolDef(
        name="excel_live.group_by_aggregate",
        description="그룹별 집계 계산 (지역별 매출 합계 등, 시트 미수정 읽기 전용)",
        permission=PermissionLevel.SAFE,
        example_triggers=["지역별 매출 합계", "그룹별 평균", "카테고리별 개수"],
    ),
    ToolDef(
        name="excel_live.find_replace",
        description="지정 범위에서 텍스트를 찾아 다른 텍스트로 바꾸기",
        permission=PermissionLevel.CONFIRM,
        example_triggers=["찾아바꿔줘", "OO를 XX로 바꿔줘", "일괄 치환"],
    ),
    ToolDef(
        name="excel_live.merge_cells",
        description="지정 범위의 셀을 하나로 병합",
        permission=PermissionLevel.CONFIRM,
        example_triggers=["셀 병합해줘", "합쳐줘", "merge"],
    ),
    ToolDef(
        name="excel_live.unmerge_cells",
        description="병합된 셀을 다시 원래 셀로 해제",
        permission=PermissionLevel.CONFIRM,
        example_triggers=["병합 풀어줘", "병합 해제", "unmerge"],
    ),
    ToolDef(
        name="excel_live.freeze_panes",
        description="머리글 행/열이 스크롤해도 고정되도록 틀 고정",
        permission=PermissionLevel.CONFIRM,
        example_triggers=["첫 행 고정해줘", "틀 고정", "머리글 안 움직이게"],
    ),
    ToolDef(
        name="excel_live.autofit_columns",
        description="열 너비를 내용 길이에 맞게 자동 조정",
        permission=PermissionLevel.CONFIRM,
        example_triggers=["열 너비 자동으로", "칸 좀 넓게 보이게", "autofit"],
    ),
    ToolDef(
        name="excel_live.define_named_range",
        description="지정 범위에 이름(정의된 이름)을 붙여 수식에서 참조 가능하게 함",
        permission=PermissionLevel.CONFIRM,
        example_triggers=["이 범위에 이름 붙여줘", "이름 정의", "named range"],
    ),
    ToolDef(
        name="excel_live.set_print_area",
        description="인쇄 영역/용지 방향/페이지 맞춤(fit to page) 설정",
        permission=PermissionLevel.CONFIRM,
        example_triggers=["인쇄 영역 설정", "가로로 인쇄", "한 장에 맞춰줘"],
    ),
    ToolDef(
        name="excel_live.add_cell_comment",
        description="지정 셀에 메모(코멘트) 추가",
        permission=PermissionLevel.CONFIRM,
        example_triggers=["메모 남겨줘", "코멘트 추가", "이 셀에 설명 달아줘"],
    ),
    ToolDef(
        name="excel_live.apply_color_scale",
        description="지정 범위에 값 크기에 따른 색조(그라디언트) 조건부 서식 적용",
        permission=PermissionLevel.CONFIRM,
        example_triggers=["색조로 표시해줘", "값 크기대로 색깔 진하게", "color scale"],
    ),
    ToolDef(
        name="excel_live.apply_data_bar",
        description="지정 범위에 값 크기를 시각화하는 데이터 막대 조건부 서식 적용",
        permission=PermissionLevel.CONFIRM,
        example_triggers=["데이터 막대로 보여줘", "막대 그래프처럼 셀에", "data bar"],
    ),
    ToolDef(
        name="excel_live.set_number_format",
        description="지정 범위의 표시 형식(숫자/퍼센트/통화/날짜 등) 변경",
        permission=PermissionLevel.CONFIRM,
        example_triggers=["퍼센트로 보여줘", "천단위 구분기호", "날짜 형식으로"],
    ),
    # ── 명시적 거부 목록 — 어떤 경우에도 실행 불가 ──────────────────────────
    ToolDef(
        name="DENIED.file_delete",
        description="파일 삭제 — 항상 거부",
        permission=PermissionLevel.DENIED,
        example_triggers=["파일 삭제", "파일 지워", "rm ", "delete file"],
    ),
    ToolDef(
        name="DENIED.shell_execute",
        description="셸/터미널 명령 실행 — 항상 거부",
        permission=PermissionLevel.DENIED,
        example_triggers=["터미널", "명령어 실행", "bash", "shell", "exec", "subprocess"],
    ),
    ToolDef(
        name="DENIED.system_modify",
        description="시스템 설정 변경 — 항상 거부",
        permission=PermissionLevel.DENIED,
        example_triggers=["설정 변경", "레지스트리", "시스템 설정"],
    ),
]

# ── 조회 헬퍼 ────────────────────────────────────────────────────────────────

_REGISTRY_MAP: dict[str, ToolDef] = {t.name: t for t in TOOL_REGISTRY}

# SAFE 실행 가능 도구 이름 집합 (intent 분류 LLM에 전달)
SAFE_TOOL_NAMES: set[str] = {
    t.name for t in TOOL_REGISTRY if t.permission == PermissionLevel.SAFE
}

# CONFIRM 권한 스킬 한국어 표시명
TOOL_DISPLAY_NAMES: dict[str, str] = {
    "gog.sheets.write": "Google Sheets 수정",
    "excel_live.write_range": "Excel 셀 값 수정",
    "excel_live.create_table": "Excel 표 생성",
    "excel_live.highlight_by_condition": "Excel 조건부 서식 변경",
    "excel_live.fill_range": "Excel 범위 배경색 채우기",
    "excel_live.clear_range": "Excel 범위 내용 비우기",
    "excel_live.apply_border": "Excel 경계선 적용",
    "excel_live.set_formula": "Excel 수식 적용",
    "excel_live.verify_formula_result": "Excel 수식 결과 검증",
    "excel_live.sort_range": "Excel 범위 정렬",
    "excel_live.filter_rows": "Excel 행 필터링(삭제 포함)",
    "excel_live.dedupe_rows": "Excel 중복 행 제거",
    "excel_live.pivot_table": "Excel 피벗 집계표 생성",
    "excel_live.find_duplicates": "Excel 중복 값 점검",
    "excel_live.recalculate": "Excel 수식 재계산 표시",
    "excel_live.export_pdf": "Excel PDF 내보내기",
    "excel_live.create_chart": "Excel 차트 생성",
    "excel_live.validate_data": "Excel 데이터 검증",
    "excel_live.protect_sheet": "Excel 시트 보호/잠금",
    "excel_live.set_data_validation": "Excel 입력 제한/드롭다운 설정",
    "excel_live.consolidate_sheets": "Excel 시트 통합",
    "excel_live.consolidate_workbooks_from_folder": "Excel 파일 통합",
    "excel_live.refresh_power_query": "Excel Power Query 새로고침",
    "excel_live.run_vba_macro": "Excel VBA 매크로 실행",
    "excel_live.compare_ranges": "Excel 범위 비교",
    "excel_live.forecast_linear": "Excel 추세 예측",
    "excel_live.save_workbook": "Excel 통합문서 저장",
    "excel_live.sort_rows": "Excel 행 정렬",
    "excel_live.drop_column": "Excel 열 삭제",
    "excel_live.rename_column": "Excel 열 이름 변경",
    "excel_live.add_column": "Excel 열 추가",
    "excel_live.find_replace": "Excel 텍스트 찾아바꾸기",
    "excel_live.merge_cells": "Excel 셀 병합",
    "excel_live.unmerge_cells": "Excel 셀 병합 해제",
    "excel_live.freeze_panes": "Excel 틀 고정",
    "excel_live.autofit_columns": "Excel 열 너비 자동 조정",
    "excel_live.define_named_range": "Excel 이름 정의",
    "excel_live.set_print_area": "Excel 인쇄 설정",
    "excel_live.add_cell_comment": "Excel 셀 메모 추가",
    "excel_live.apply_color_scale": "Excel 색조 조건부 서식",
    "excel_live.apply_data_bar": "Excel 데이터 막대 조건부 서식",
    "excel_live.set_number_format": "Excel 표시 형식 변경",
}

# ── 화이트리스트 오버라이드 (런타임에 사용자가 변경 가능) ─────────────────────

import json as _json
import logging as _logging

_wl_logger = _logging.getLogger(__name__)

# 런타임 오버라이드: 스킬 이름 → PermissionLevel
_whitelist_overrides: dict[str, PermissionLevel] = {}


def load_whitelist() -> None:
    """
    skill_whitelist.json을 읽어 _whitelist_overrides를 갱신한다.

    파일이 없거나 파싱 실패 시 코드 기본값을 유지한다.
    앱 시작 시 1회 호출하면 된다.
    """
    from office_claw_sidecar.config import get_whitelist_path

    path = get_whitelist_path()
    if not path.exists():
        return
    try:
        data: dict = _json.loads(path.read_text(encoding="utf-8"))
        _whitelist_overrides.clear()
        for skill_name, level_str in data.items():
            try:
                _whitelist_overrides[skill_name] = PermissionLevel(level_str)
            except ValueError:
                _wl_logger.warning("[tool_registry] 알 수 없는 권한 레벨: %s=%s", skill_name, level_str)
        _wl_logger.info("[tool_registry] 화이트리스트 로드: %d개", len(_whitelist_overrides))
    except Exception as exc:
        _wl_logger.error("[tool_registry] 화이트리스트 로드 실패 (기본값 사용): %s", exc)


def save_whitelist(overrides: dict[str, str]) -> None:
    """
    스킬 권한 오버라이드를 skill_whitelist.json에 저장하고 메모리를 갱신한다.

    임시 파일 → rename 패턴으로 파일 손상을 방지한다.
    """
    import shutil
    import tempfile

    from office_claw_sidecar.config import get_whitelist_path

    path = get_whitelist_path()
    # 유효성 검증
    validated: dict[str, str] = {}
    new_overrides: dict[str, PermissionLevel] = {}
    for skill_name, level_str in overrides.items():
        try:
            perm = PermissionLevel(level_str)
            validated[skill_name] = level_str
            new_overrides[skill_name] = perm
        except ValueError:
            raise ValueError(f"유효하지 않은 권한 레벨: '{level_str}' (허용: safe, confirm, denied)")

    # 임시 파일 → rename
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with open(tmp_fd, "w", encoding="utf-8") as f:
                _json.dump(validated, f, ensure_ascii=False, indent=2)
            shutil.move(tmp_path, str(path))
        except Exception:
            import os
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
            raise
    except Exception as exc:
        raise RuntimeError(f"화이트리스트 저장 실패: {exc}") from exc

    # 메모리 갱신
    _whitelist_overrides.clear()
    _whitelist_overrides.update(new_overrides)
    _wl_logger.info("[tool_registry] 화이트리스트 저장: %d개", len(new_overrides))


def get_whitelist_state() -> list[dict]:
    """
    현재 스킬별 권한 상태 목록을 반환한다 (화이트리스트 오버라이드 반영).

    Returns:
        [{"name": "gog.sheets.write", "display_name": "...", "default_permission": "confirm",
          "current_permission": "safe", "overridden": True}, ...]
    """
    result: list[dict] = []
    for tool in TOOL_REGISTRY:
        if tool.name.startswith("DENIED."):
            continue  # 내부 거부 도구는 제외
        default_perm = tool.permission.value
        current_perm = _whitelist_overrides.get(tool.name, tool.permission).value
        result.append({
            "name": tool.name,
            "display_name": TOOL_DISPLAY_NAMES.get(tool.name, tool.name),
            "description": tool.description,
            "default_permission": default_perm,
            "current_permission": current_perm,
            "overridden": tool.name in _whitelist_overrides,
        })
    return result


def get_tool(name: str) -> ToolDef | None:
    """현재 유효한 PermissionLevel을 반영한 ToolDef를 반환한다 (화이트리스트 오버라이드 포함)."""
    tool = _REGISTRY_MAP.get(name)
    if tool is None:
        return None
    overridden = _whitelist_overrides.get(name)
    if overridden is not None and overridden != tool.permission:
        # 화이트리스트 오버라이드: 새 ToolDef 반환 (원본 불변 유지)
        from dataclasses import replace as _dc_replace
        return _dc_replace(tool, permission=overridden)
    return tool


def get_tools_description() -> str:
    """SAFE 도구만 포함한 LLM 프롬프트용 설명 문자열."""
    lines = []
    for t in TOOL_REGISTRY:
        if t.permission == PermissionLevel.SAFE:
            lines.append(f"- {t.name}: {t.description}")
    return "\n".join(lines)


# ── 질문/설명 맥락 패턴 (SAFE_CONTEXT) ───────────────────────────────────────
# 이 패턴에 매칭되면 실행 의도가 아닌 것으로 판단 → SAFE 반환
_SAFE_CONTEXT_PATTERNS: list[re.Pattern] = [
    # 한국어 질문/설명 요청
    re.compile(
        r"(설명|알려|뭐야|뜻|개념|차이|비교|방법|관련|규정|정리|요약|이해|학습)"
        r".*?(줘|주세요|요|해줘|해주세요|할게요|드릴게요)$"
    ),
    # 영어 질문/설명 요청
    re.compile(r"\b(what|explain|describe|how|tell\s+me|define|clarify)\b", re.IGNORECASE),
]

# 짧은 영어 트리거(2글자 이하)는 동사 + 목적어 컨텍스트가 있어야만 차단
# 예: "rm file", "rm -rf", "rm /" 등에서는 차단
# "rm 코스메틱", "rm 콜라보" 등 일반 명사 뒤에는 차단하지 않음
_SHORT_TRIGGER_VERB_PATTERNS: dict[str, re.Pattern] = {
    # "rm " 트리거: 뒤에 파일 경로 패턴 or "-" 옵션이 있을 때만 차단
    "rm": re.compile(r"\brm\s+(?:-[a-z]+\s*|[/~\.]|[a-z_]+\.[a-z]+)", re.IGNORECASE),
}


def _is_korean(text: str) -> bool:
    """텍스트에 한글이 포함되어 있는지 확인."""
    return bool(re.search(r"[가-힣]", text))


def is_denied_intent(message: str) -> tuple[bool, str | None]:
    """
    메시지가 DENIED 도구의 트리거 키워드를 포함하는지 확인한다.

    개선 사항 (Phase 5-2):
    - 영어 트리거에 word boundary(\b) 적용 → false positive 대폭 감소
    - 질문/설명 맥락(safe context) 감지 시 SAFE 반환
    - 한국어 트리거는 기존 substring 매칭 유지 (그 자체가 의미 단위)

    Returns:
        (is_denied, matched_trigger): 차단 여부 + 매칭된 키워드 힌트
        차단 아닌 경우: (False, None)
    """
    msg_lower = message.lower()

    # 질문/설명 맥락이면 실행 의도가 아님 → 즉시 SAFE 반환
    for pattern in _SAFE_CONTEXT_PATTERNS:
        if pattern.search(msg_lower):
            return False, None

    for tool in TOOL_REGISTRY:
        if tool.permission != PermissionLevel.DENIED:
            continue
        for trigger in tool.example_triggers:
            trigger_lower = trigger.lower().strip()
            if _is_korean(trigger_lower):
                # 한국어 트리거: substring 매칭
                if trigger_lower in msg_lower:
                    return True, trigger
            else:
                # 짧은 트리거(공백 제거 후 2글자 이하): 별도 컨텍스트 패턴 확인
                bare_trigger = trigger_lower.strip()
                if len(bare_trigger) <= 2 and bare_trigger in _SHORT_TRIGGER_VERB_PATTERNS:
                    if _SHORT_TRIGGER_VERB_PATTERNS[bare_trigger].search(msg_lower):
                        return True, trigger
                    continue  # 컨텍스트 패턴에 안 걸리면 무시

                # 영어 트리거: word boundary 매칭
                pattern_str = r"\b" + re.escape(trigger_lower) + r"\b"
                if re.search(pattern_str, msg_lower):
                    return True, trigger

    return False, None
