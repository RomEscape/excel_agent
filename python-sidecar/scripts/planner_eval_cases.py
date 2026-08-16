"""고정 평가셋 — 플래너 회귀 측정용.

## 왜 따로 쓰는가

학습 데이터(`excel_action_coverage_cases.py`, `excel_clarify_cases.py`)는 템플릿
생성기다. 같은 생성기로 평가셋을 만들면 "템플릿을 외웠는가"를 재게 되고, 실제로
좋아졌는지는 알 수 없다. 그래서 이 파일의 두 가지는 학습 자산과 절대 공유하지
않는다.

1. **통합문서** — 병원·물류·학원·부동산·카페·영문재고. 학습 픽스처(매출·거래내역·
   급여대장·재고현황·학과운영비·출근부·고객명단·프로젝트일정·예산집행·설문응답)와
   도메인도 머리글도 겹치지 않는다.
2. **문장** — 전부 손으로 썼다. 존댓말·반말·명사형·오타·군더더기를 섞었고,
   학습 템플릿의 어투를 의도적으로 피했다.

## 채점 구분

`category`가 측정의 핵심이다. 전체 정답률 하나로는 무엇이 나빠졌는지 알 수 없다.

- `core` — 매일 쓰는 동작. 여기가 떨어지면 배포하면 안 된다.
- `rare` — 학습에서 예제가 0~적었던 액션. 여기가 오르는 게 v5의 목표다.
- `clarify_yes` — 추측하면 데이터가 망가지는 모호한 요청. 되물어야 정답.
- `clarify_no` — 답이 하나뿐인 요청. **되물으면 오답**이다. 되묻기를 가르친 뒤
  생기는 과잉 질문을 잡아내는 자리다.
- `multi` — 두 단계 이상.
- `colloquial` — 구어체·오타·생략. 템플릿 과적합이면 여기부터 무너진다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# ── 평가 전용 통합문서 ───────────────────────────────────────────────────


def _sheet(
    name: str,
    used_range: str,
    columns: list[tuple[str, str]],
    sample: list[str],
    categories: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    cats = categories or {}
    return {
        "name": name,
        "used_range": used_range,
        "columns": [
            {"letter": letter, "header": header, "categories": cats.get(header, [])}
            for letter, header in columns
        ],
        "sample_rows": [sample],
    }


WORKBOOKS: dict[str, dict[str, Any]] = {
    "병원": {
        "active_sheet": "진료기록",
        "sheets": [
            _sheet(
                "진료기록",
                "A1:G312",
                [
                    ("A", "진료일"),
                    ("B", "환자번호"),
                    ("C", "진료과"),
                    ("D", "담당의"),
                    ("E", "진료비"),
                    ("F", "본인부담금"),
                    ("G", "수납상태"),
                ],
                ["2026-01-08", "P-20315", "정형외과", "김도현", "84000", "25200", "완납"],
                {
                    "진료과": ["정형외과", "내과", "소아과", "피부과"],
                    "수납상태": ["완납", "미납", "부분수납"],
                },
            ),
            _sheet(
                "수납대장",
                "A1:D188",
                [("A", "수납일"), ("B", "환자번호"), ("C", "수납액"), ("D", "결제수단")],
                ["2026-01-08", "P-20315", "25200", "카드"],
            ),
        ],
    },
    "물류": {
        "active_sheet": "배송내역",
        "sheets": [
            _sheet(
                "배송내역",
                "A1:H427",
                [
                    ("A", "운송장번호"),
                    ("B", "출고일"),
                    ("C", "도착지"),
                    ("D", "배송사"),
                    ("E", "중량"),
                    ("F", "배송비"),
                    ("G", "배송상태"),
                    ("H", "지연일수"),
                ],
                ["1234-5678-9012", "2026-02-03", "대전", "한진", "2.4", "3800", "배송완료", "0"],
                {
                    "배송사": ["한진", "CJ대한통운", "우체국", "롯데"],
                    "배송상태": ["배송완료", "배송중", "미배송", "반송"],
                },
            ),
        ],
    },
    "학원": {
        "active_sheet": "성적표",
        "sheets": [
            _sheet(
                "성적표",
                "A1:G94",
                [
                    ("A", "학번"),
                    ("B", "학생명"),
                    ("C", "반"),
                    ("D", "중간고사"),
                    ("E", "기말고사"),
                    ("F", "출석점수"),
                    ("G", "총점"),
                ],
                ["24001", "김서준", "A반", "88", "92", "10", "190"],
                {"반": ["A반", "B반", "C반"]},
            ),
            _sheet(
                "출결",
                "A1:E94",
                [("A", "학번"), ("B", "학생명"), ("C", "출석"), ("D", "결석"), ("E", "지각")],
                ["24001", "김서준", "58", "1", "2"],
            ),
        ],
    },
    "부동산": {
        "active_sheet": "매물목록",
        "sheets": [
            _sheet(
                "매물목록",
                "A1:H156",
                [
                    ("A", "매물번호"),
                    ("B", "소재지"),
                    ("C", "면적"),
                    ("D", "층"),
                    ("E", "보증금"),
                    ("F", "월세"),
                    ("G", "관리비"),
                    ("H", "거래상태"),
                ],
                ["M-0031", "마포구 합정동", "84.3", "7", "50000000", "1200000", "150000", "거래가능"],
                {
                    "소재지": ["마포구 합정동", "강남구 역삼동", "송파구 문정동"],
                    "거래상태": ["거래가능", "계약중", "거래완료"],
                },
            ),
        ],
    },
    "카페": {
        "active_sheet": "일일매출",
        "sheets": [
            _sheet(
                "일일매출",
                "A1:G621",
                [
                    ("A", "일자"),
                    ("B", "매장"),
                    ("C", "메뉴"),
                    ("D", "판매수량"),
                    ("E", "단가"),
                    ("F", "매출액"),
                    ("G", "결제수단"),
                ],
                ["2026-03-11", "홍대점", "아메리카노", "42", "4500", "189000", "카드"],
                {
                    "매장": ["홍대점", "성수점", "판교점"],
                    "메뉴": ["아메리카노", "라떼", "콜드브루", "크로플"],
                    "결제수단": ["카드", "현금", "간편결제"],
                },
            ),
            _sheet(
                "메뉴원가",
                "A1:D28",
                [("A", "메뉴"), ("B", "원가"), ("C", "판매가"), ("D", "마진율")],
                ["아메리카노", "1100", "4500", "0.756"],
            ),
        ],
    },
    "영문재고": {
        "active_sheet": "Inventory",
        "sheets": [
            _sheet(
                "Inventory",
                "A1:G210",
                [
                    ("A", "SKU"),
                    ("B", "ItemName"),
                    ("C", "Warehouse"),
                    ("D", "OnHand"),
                    ("E", "ReorderPoint"),
                    ("F", "UnitCost"),
                    ("G", "TotalValue"),
                ],
                ["SKU-1042", "Wireless Mouse", "Seoul", "137", "60", "12500", "1712500"],
                {"Warehouse": ["Seoul", "Busan", "Incheon"]},
            ),
        ],
    },
}


@dataclass(frozen=True)
class EvalCase:
    case_id: str
    category: str
    workbook: str
    instruction: str
    expected: tuple[str, ...]


def _c(case_id: str, category: str, workbook: str, instruction: str, *expected: str) -> EvalCase:
    return EvalCase(case_id, category, workbook, instruction, tuple(expected))


A = "excel_live."

# ── core: 매일 쓰는 동작 ─────────────────────────────────────────────────

CORE: list[EvalCase] = [
    _c("core-001", "core", "병원", "진료비 높은 순으로 줄 세워주세요", f"{A}sort_range"),
    _c("core-002", "core", "카페", "매출액 기준 내림차순 정렬 부탁해", f"{A}sort_range"),
    _c("core-003", "core", "학원", "총점 낮은 학생이 위로 오게 바꿔줘", f"{A}sort_range"),
    _c("core-004", "core", "물류", "지연일수 많은 것부터 보여줘", f"{A}sort_range"),
    _c("core-005", "core", "부동산", "월세 싼 순서대로 정렬", f"{A}sort_range"),
    _c("core-006", "core", "병원", "수납상태가 미납인 행만 남겨주세요", f"{A}filter_rows"),
    _c("core-007", "core", "물류", "반송된 건만 골라내 줘", f"{A}filter_rows"),
    _c("core-008", "core", "카페", "홍대점 자료만 보고 싶어", f"{A}filter_rows"),
    _c("core-009", "core", "부동산", "월세 100만원 넘는 매물만 추려줘", f"{A}filter_rows"),
    _c("core-010", "core", "학원", "총점 180점 이상인 애들만 필터", f"{A}filter_rows"),
    _c("core-011", "core", "카페", "매장별로 매출액 합계 내줘", f"{A}group_by_aggregate"),
    _c("core-012", "core", "병원", "진료과마다 진료비 총액이 얼마인지 뽑아줘", f"{A}group_by_aggregate"),
    _c("core-013", "core", "물류", "배송사별 배송비 평균 계산해줘", f"{A}group_by_aggregate"),
    _c("core-014", "core", "학원", "반별 총점 평균 정리해 주세요", f"{A}group_by_aggregate"),
    _c("core-015", "core", "카페", "매장을 행, 메뉴를 열로 해서 매출액 피벗 만들어줘", f"{A}pivot_table"),
    _c("core-016", "core", "병원", "진료과별 수납상태 교차표로 만들어줘", f"{A}pivot_table"),
    _c("core-017", "core", "물류", "도착지랑 배송사로 피벗테이블 하나 뽑아줘", f"{A}pivot_table"),
    _c("core-018", "core", "학원", "H2에 중간고사랑 기말고사 더하는 수식 넣어줘", f"{A}set_formula"),
    _c("core-019", "core", "카페", "H열에 판매수량 곱하기 단가 수식 걸어줘", f"{A}set_formula"),
    _c("core-020", "core", "부동산", "I2에 보증금 대비 월세 비율 계산식 넣어주세요", f"{A}set_formula"),
    _c("core-021", "core", "병원", "H1에 진료비합계라고 써줘", f"{A}write_range"),
    _c("core-022", "core", "학원", "I1 칸에 비고 입력", f"{A}write_range"),
    _c("core-023", "core", "카페", "A1부터 G1까지 배경 노란색으로 칠해줘", f"{A}fill_range"),
    _c("core-024", "core", "물류", "머리글 줄에 회색 배경 넣어주세요", f"{A}fill_range"),
    _c("core-025", "core", "부동산", "월세 150만원 넘는 셀 빨갛게 표시해줘", f"{A}highlight_by_condition"),
    _c("core-026", "core", "학원", "총점 150점 밑인 애들 눈에 띄게 색칠해줘", f"{A}highlight_by_condition"),
    _c("core-027", "core", "병원", "미납인 줄 강조해 주세요", f"{A}highlight_by_condition"),
    _c("core-028", "core", "카페", "매장별 매출 막대그래프로 그려줘", f"{A}create_chart"),
    _c("core-029", "core", "학원", "반별 평균 점수 차트 만들어 주세요", f"{A}create_chart"),
    _c("core-030", "core", "물류", "운송장번호 같은 거 중복 지워줘", f"{A}dedupe_rows"),
    _c("core-031", "core", "병원", "환자번호 기준으로 중복 행 정리해줘", f"{A}dedupe_rows"),
    _c("core-032", "core", "카페", "지금 시트 내용 좀 보여줘", f"{A}read_range"),
    _c("core-033", "core", "부동산", "A1:H20 읽어줘", f"{A}read_range"),
    _c("core-034", "core", "학원", "총점 평균이 몇 점이야?", f"{A}calculate_column_stat"),
    _c("core-035", "core", "카페", "매출액 다 더하면 얼마야", f"{A}calculate_column_stat"),
    _c("core-036", "core", "물류", "배송비 최댓값 알려줘", f"{A}calculate_column_stat"),
    _c("core-037", "core", "병원", "정형외과를 정형외과의원으로 전부 바꿔줘", f"{A}find_replace"),
    _c("core-038", "core", "물류", "한진이라고 된 거 한진택배로 치환", f"{A}find_replace"),
    _c("core-039", "core", "카페", "열 너비 내용에 맞게 조절해줘", f"{A}autofit_columns"),
    _c("core-040", "core", "학원", "첫 줄 고정해서 스크롤해도 보이게 해줘", f"{A}freeze_panes"),
    _c("core-041", "core", "부동산", "보증금 열 천단위 콤마 찍어줘", f"{A}set_number_format"),
    _c("core-042", "core", "카페", "매출액을 원화 표시로 바꿔주세요", f"{A}set_number_format"),
    _c("core-043", "core", "학원", "출석점수 열 지워줘", f"{A}drop_column"),
    _c("core-044", "core", "물류", "맨 뒤에 비고 열 하나 추가해줘", f"{A}add_column"),
    _c("core-045", "core", "병원", "본인부담금을 자기부담금으로 이름 바꿔줘", f"{A}rename_column"),
    _c("core-046", "core", "카페", "G열 내용 싹 지워줘", f"{A}clear_range"),
    _c("core-047", "core", "학원", "5행 5열짜리 빈 표 하나 만들어줘", f"{A}create_table"),
    _c("core-048", "core", "부동산", "면적 큰 순으로 정렬해 주실래요", f"{A}sort_range"),
    _c("core-049", "core", "영문재고", "재고수량 많은 순으로 정렬해줘", f"{A}sort_range"),
    _c("core-050", "core", "영문재고", "창고별 재고금액 합계 내줘", f"{A}group_by_aggregate"),
    _c("core-051", "core", "영문재고", "현재고가 발주점보다 적은 항목 빨갛게 칠해줘", f"{A}highlight_by_condition"),
    _c("core-052", "core", "영문재고", "부산 창고 것만 남겨줘", f"{A}filter_rows"),
]

# ── rare: 학습 예제가 적었던 액션 ────────────────────────────────────────

RARE: list[EvalCase] = [
    _c("rare-001", "rare", "카페", "지금 열려있는 엑셀 파일 목록 좀", f"{A}list_workbooks"),
    _c("rare-002", "rare", "병원", "이 파일에 시트 뭐뭐 있어?", f"{A}list_sheets"),
    _c("rare-003", "rare", "학원", "출결 시트로 이동해줘", f"{A}select_sheet"),
    _c("rare-004", "rare", "카페", "메뉴원가 시트 좀 열어봐", f"{A}select_sheet"),
    _c("rare-005", "rare", "물류", "2026년_실적.xlsx 파일로 바꿔줘", f"{A}select_workbook"),
    _c("rare-006", "rare", "병원", "통계라는 이름으로 시트 새로 만들어줘", f"{A}create_sheet"),
    _c("rare-007", "rare", "부동산", "지금까지 한 거 저장해줘", f"{A}save_workbook"),
    _c("rare-008", "rare", "학원", "성적표 PDF로 내보내줘", f"{A}export_pdf"),
    _c("rare-009", "rare", "카페", "표 전체에 테두리 둘러줘", f"{A}apply_border"),
    _c("rare-010", "rare", "물류", "배송비 열에 색조 넣어서 크기 비교되게 해줘", f"{A}apply_color_scale"),
    _c("rare-011", "rare", "학원", "총점 열에 데이터 막대 표시해줘", f"{A}apply_data_bar"),
    _c("rare-012", "rare", "병원", "E1 셀에 부가세 별도라고 메모 달아줘", f"{A}add_cell_comment"),
    _c("rare-013", "rare", "카페", "A1이랑 B1 셀 합쳐줘", f"{A}merge_cells"),
    _c("rare-014", "rare", "카페", "병합된 셀 다시 나눠줘", f"{A}unmerge_cells"),
    _c("rare-015", "rare", "부동산", "이 시트 수정 못 하게 잠가줘", f"{A}protect_sheet"),
    _c("rare-016", "rare", "학원", "총점 범위를 총점범위라는 이름으로 정의해줘", f"{A}define_named_range"),
    _c("rare-017", "rare", "물류", "배송상태 열에 목록 선택만 되게 제한 걸어줘", f"{A}set_data_validation"),
    _c("rare-018", "rare", "병원", "A1:G50만 인쇄 영역으로 잡아줘", f"{A}set_print_area"),
    _c("rare-019", "rare", "학원", "계산 결과 갱신 좀 해줘", f"{A}recalculate"),
    _c("rare-020", "rare", "카페", "파워쿼리 새로고침 해줘", f"{A}refresh_power_query"),
    _c("rare-021", "rare", "부동산", "정리매크로라는 매크로 실행시켜줘", f"{A}run_vba_macro"),
    _c("rare-022", "rare", "물류", "운송장번호 중복된 거 있는지 찾아만 줘", f"{A}find_duplicates"),
    _c("rare-023", "rare", "카페", "지난 매출로 다음 달 매출 예측해줘", f"{A}forecast_linear"),
    _c("rare-024", "rare", "학원", "성적표랑 출결 시트 비교해서 다른 데 찾아줘", f"{A}compare_ranges"),
    _c("rare-025", "rare", "카페", "일일매출이랑 메뉴원가 시트 하나로 합쳐줘", f"{A}consolidate_sheets"),
    _c("rare-026", "rare", "물류", "C:\\배송자료 폴더에 있는 엑셀 파일들 다 합쳐줘", f"{A}consolidate_workbooks_from_folder"),
    _c("rare-027", "rare", "병원", "진료비 열에 이상한 값 없는지 검사해줘", f"{A}validate_data"),
    _c("rare-028", "rare", "학원", "G2 수식 결과가 맞는지 확인해줘", f"{A}verify_formula_result"),
    _c("rare-029", "rare", "부동산", "면적 열 기준으로 행 정렬해줘", f"{A}sort_rows"),
    _c("rare-030", "rare", "영문재고", "SKU 중복 있는지만 알려줘", f"{A}find_duplicates"),
    _c("rare-031", "rare", "병원", "수납대장 시트 보호 걸어줘", f"{A}protect_sheet"),
    _c("rare-032", "rare", "물류", "이 통합문서 PDF로 저장", f"{A}export_pdf"),
    _c("rare-033", "rare", "병원", "수납대장 시트 이름을 수납내역으로 바꿔줘", f"{A}rename_sheet"),
    _c("rare-034", "rare", "학원", "출결 시트 삭제해줘", f"{A}delete_sheet"),
    _c("rare-035", "rare", "학원", "성적표 머리글을 굵게 해줘", f"{A}set_font"),
    _c("rare-036", "rare", "영문재고", "재고 시트를 InventoryTable 이름으로 엑셀 표 테이블로 만들어줘", f"{A}convert_to_excel_table"),
    _c("rare-037", "rare", "병원", "수납상태가 미납이면 빨간 조건부서식", f"{A}apply_formula_cf"),
]

# ── clarify_yes: 되물어야 정답 ───────────────────────────────────────────

CLARIFY_YES: list[EvalCase] = [
    _c("cly-001", "clarify_yes", "카페", "정렬해줘", f"{A}clarify"),
    _c("cly-002", "clarify_yes", "학원", "순서대로 좀 맞춰줘", f"{A}clarify"),
    _c("cly-003", "clarify_yes", "병원", "필요없는 거 지워줘", f"{A}clarify"),
    _c("cly-004", "clarify_yes", "물류", "이거 좀 정리해줘", f"{A}clarify"),
    _c("cly-005", "clarify_yes", "부동산", "좀 추려봐", f"{A}clarify"),
    _c("cly-006", "clarify_yes", "카페", "중복 없애줘", f"{A}clarify"),
    _c("cly-007", "clarify_yes", "학원", "시각화 좀 해줄래", f"{A}clarify"),
    _c("cly-008", "clarify_yes", "병원", "집계 좀 내줘", f"{A}clarify"),
    _c("cly-009", "clarify_yes", "물류", "눈에 띄게 만들어줘", f"{A}clarify"),
    _c("cly-010", "clarify_yes", "카페", "합계 구해줘", f"{A}clarify"),
    _c("cly-011", "clarify_yes", "학원", "보기 좋게 바꿔줄 수 있어?", f"{A}clarify"),
    _c("cly-012", "clarify_yes", "부동산", "위에서부터 지워줘", f"{A}clarify"),
    _c("cly-013", "clarify_yes", "병원", "숫자 형식 바꿔줘", f"{A}clarify"),
    _c("cly-014", "clarify_yes", "카페", "빈 칸 채워줘", f"{A}clarify"),
    _c("cly-015", "clarify_yes", "물류", "이상한 데 찾아줘", f"{A}clarify"),
    _c("cly-016", "clarify_yes", "학원", "표로 만들어줘", f"{A}clarify"),
    _c("cly-017", "clarify_yes", "영문재고", "정리 좀", f"{A}clarify"),
    _c("cly-018", "clarify_yes", "카페", "여기 강조 좀 해줘", f"{A}clarify"),
]

# ── clarify_no: 되물으면 오답 ────────────────────────────────────────────

CLARIFY_NO: list[EvalCase] = [
    _c("cln-001", "clarify_no", "카페", "매출액 열 기준 내림차순으로 정렬해줘", f"{A}sort_range"),
    _c("cln-002", "clarify_no", "학원", "총점 열 오름차순 정렬", f"{A}sort_range"),
    _c("cln-003", "clarify_no", "병원", "수납상태가 미납인 행만 필터링해줘", f"{A}filter_rows"),
    _c("cln-004", "clarify_no", "물류", "배송상태 열에서 반송만 남겨줘", f"{A}filter_rows"),
    _c("cln-005", "clarify_no", "카페", "매장 열 기준으로 매출액 합계 집계해줘", f"{A}group_by_aggregate"),
    _c("cln-006", "clarify_no", "부동산", "월세가 150만원 이상인 셀을 빨간색으로 강조해줘", f"{A}highlight_by_condition"),
    _c("cln-007", "clarify_no", "학원", "학번 열 기준으로 중복 행 제거해줘", f"{A}dedupe_rows"),
    _c("cln-008", "clarify_no", "병원", "진료비 열의 평균을 계산해줘", f"{A}calculate_column_stat"),
    _c("cln-009", "clarify_no", "카페", "H2 셀에 =D2*E2 수식 입력해줘", f"{A}set_formula"),
    _c("cln-010", "clarify_no", "물류", "지금 열려있는 통합문서 목록 알려줘", f"{A}list_workbooks"),
    _c("cln-011", "clarify_no", "학원", "이 통합문서의 시트 이름들 알려줘", f"{A}list_sheets"),
    _c("cln-012", "clarify_no", "부동산", "현재 시트 저장해줘", f"{A}save_workbook"),
    _c("cln-013", "clarify_no", "카페", "모든 열 너비를 내용에 맞춰 자동 조정해줘", f"{A}autofit_columns"),
    _c("cln-014", "clarify_no", "병원", "1행을 틀 고정해줘", f"{A}freeze_panes"),
    _c("cln-015", "clarify_no", "물류", "A1 셀에 배송집계표라고 입력해줘", f"{A}write_range"),
    _c("cln-016", "clarify_no", "학원", "출석점수 열을 삭제해줘", f"{A}drop_column"),
    _c("cln-017", "clarify_no", "카페", "일일매출 시트를 선택해줘", f"{A}select_sheet"),
    _c("cln-018", "clarify_no", "영문재고", "Warehouse 열 기준으로 TotalValue 합계 내줘", f"{A}group_by_aggregate"),
    _c("cln-019", "clarify_no", "부동산", "면적 열을 소수점 첫째자리까지 표시해줘", f"{A}set_number_format"),
    _c("cln-020", "clarify_no", "병원", "진료기록 시트 A1:G50을 인쇄 영역으로 설정해줘", f"{A}set_print_area"),
]

# ── multi: 두 단계 이상 ──────────────────────────────────────────────────

MULTI: list[EvalCase] = [
    _c("mul-001", "multi", "카페", "매출액 큰 순으로 정렬하고 상위 셀들 색칠해줘",
       f"{A}sort_range", f"{A}highlight_by_condition"),
    _c("mul-002", "multi", "학원", "총점 기준으로 정렬한 다음 첫 줄 고정해줘",
       f"{A}sort_range", f"{A}freeze_panes"),
    _c("mul-003", "multi", "병원", "미납만 필터하고 진료비 합계 내줘",
       f"{A}filter_rows", f"{A}calculate_column_stat"),
    _c("mul-004", "multi", "물류", "배송사별로 배송비 합계 내고 막대그래프 그려줘",
       f"{A}group_by_aggregate", f"{A}create_chart"),
    _c("mul-005", "multi", "부동산", "월세 순으로 정렬하고 열 너비 맞춰줘",
       f"{A}sort_range", f"{A}autofit_columns"),
    _c("mul-006", "multi", "카페", "매장별 매출 집계해서 새 시트에 넣고 저장해줘",
       f"{A}group_by_aggregate", f"{A}save_workbook"),
    _c("mul-007", "multi", "학원", "중복 학번 제거하고 총점 순으로 정렬해줘",
       f"{A}dedupe_rows", f"{A}sort_range"),
    _c("mul-008", "multi", "병원", "진료과별 진료비 합계 구하고 차트로 그려줘",
       f"{A}group_by_aggregate", f"{A}create_chart"),
    _c("mul-009", "multi", "물류", "지연일수 0보다 큰 것만 남기고 빨갛게 표시해줘",
       f"{A}filter_rows", f"{A}highlight_by_condition"),
    _c("mul-010", "multi", "영문재고", "창고별 합계 내고 PDF로 내보내줘",
       f"{A}group_by_aggregate", f"{A}export_pdf"),
    _c("mul-011", "multi", "카페", "H1에 원가율이라고 쓰고 H2에 수식 넣어줘",
       f"{A}write_range", f"{A}set_formula"),
    _c("mul-012", "multi", "학원", "비고 열 추가하고 테두리 둘러줘",
       f"{A}add_column", f"{A}apply_border"),
]

# ── colloquial: 구어체·오타·생략 ─────────────────────────────────────────

COLLOQUIAL: list[EvalCase] = [
    _c("col-001", "colloquial", "카페", "매출액 큰거부터 좀 보여줄래?", f"{A}sort_range"),
    _c("col-002", "colloquial", "학원", "총점 순으루 정렬해죠", f"{A}sort_range"),
    _c("col-003", "colloquial", "병원", "미납된것만 딱 뽑아봐", f"{A}filter_rows"),
    _c("col-004", "colloquial", "물류", "배송사 별로 배송비 얼마인지 합쳐죠", f"{A}group_by_aggregate"),
    _c("col-005", "colloquial", "부동산", "월세 비싼순", f"{A}sort_range"),
    _c("col-006", "colloquial", "카페", "매장마다 매출 얼마나 나왔는지", f"{A}group_by_aggregate"),
    _c("col-007", "colloquial", "학원", "총점평균좀", f"{A}calculate_column_stat"),
    _c("col-008", "colloquial", "물류", "중복된 운송장 지워주세여", f"{A}dedupe_rows"),
    _c("col-009", "colloquial", "병원", "진료과별 합계 부탁드립니다..", f"{A}group_by_aggregate"),
    _c("col-010", "colloquial", "카페", "열너비 자동으로", f"{A}autofit_columns"),
    _c("col-011", "colloquial", "학원", "1행 고정!", f"{A}freeze_panes"),
    _c("col-012", "colloquial", "부동산", "보증금에 콤마 넣어주라", f"{A}set_number_format"),
    _c("col-013", "colloquial", "물류", "그래프 하나 뽑아줘 배송사별 배송비로", f"{A}create_chart"),
    _c("col-014", "colloquial", "카페", "저장 좀", f"{A}save_workbook"),
    _c("col-015", "colloquial", "병원", "시트 뭐있지", f"{A}list_sheets"),
    _c("col-016", "colloquial", "학원", "출석점수 필요없어 없애줘", f"{A}drop_column"),
    _c("col-017", "colloquial", "카페", "메뉴원가 시트좀", f"{A}select_sheet"),
    _c("col-018", "colloquial", "영문재고", "재고 적은거 표시해줘 발주점보다 낮은거", f"{A}highlight_by_condition"),
    _c("col-019", "colloquial", "부동산", "거래완료된거 빼고 보고싶은데", f"{A}filter_rows"),
    _c("col-020", "colloquial", "물류", "지연 젤 심한거 알려줘", f"{A}calculate_column_stat"),
]


ALL_CASES: list[EvalCase] = [
    *CORE,
    *RARE,
    *CLARIFY_YES,
    *CLARIFY_NO,
    *MULTI,
    *COLLOQUIAL,
]


def all_cases() -> list[EvalCase]:
    return list(ALL_CASES)
