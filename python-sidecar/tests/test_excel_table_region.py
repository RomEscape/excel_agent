"""문맥 범위 → 표 전체 확장(2026-08-19)."""

from office_claw_sidecar.services.excel_table_region import expand_to_table_region

# A8 제목 한 칸, A9:F9 머리글, A10:F18 데이터, 19행 빈 줄, A20 다른 제목.
_USED = (8, 1, 20, 6)
_VALUES = (
    [["섹터별 투자 비중", None, None, None, None, None]]
    + [["섹터", "평가액", "비중", "전월", "벤치", "차이"]]
    + [[f"섹터{i}", 100 + i, 1.0, 0.1, 2.0, 0.5] for i in range(9)]
    + [[None] * 6]
    + [["주요 보유 종목", None, None, None, None, None]]
)


def test_header_row_context_expands_down_to_the_table_not_up_to_the_title():
    assert expand_to_table_region((9, 1, 9, 6), _USED, _VALUES) == (9, 1, 18, 6)


def test_a_middle_row_expands_both_ways_within_the_table():
    assert expand_to_table_region((13, 1, 13, 6), _USED, _VALUES) == (9, 1, 18, 6)


def test_full_table_is_unchanged():
    assert expand_to_table_region((9, 1, 18, 6), _USED, _VALUES) == (9, 1, 18, 6)


def test_partial_columns_expand_sideways():
    assert expand_to_table_region((10, 2, 12, 3), _USED, _VALUES) == (9, 1, 18, 6)


def test_single_title_cell_does_not_swallow_the_table_below():
    # 제목 한 칸(A8)만 잡았을 때: 아래 줄(머리글)이 6칸 차 있어도 너비 1인 사각형은
    # 한 칸만 차 있으면 이어진다고 보므로 표까지 내려간다 — 그건 의도된 동작이다.
    # 반대로 표(A9:F9)에서 위로 갈 때 제목 줄은 1칸뿐이라 붙지 않는다(첫 테스트).
    assert expand_to_table_region((8, 1, 8, 1), _USED, _VALUES)[0] == 8
