"""Excel Live Service 단위 테스트."""

from office_claw_sidecar.services.excel_live_service import (
    ExcelConnectionError,
    ExcelLiveService,
    WorkbookNotFoundError,
    WorksheetNotFoundError,
)


def _col_to_idx(col: str) -> int:
    v = 0
    for ch in col:
        v = v * 26 + (ord(ch.upper()) - ord("A") + 1)
    return v


def _idx_to_col(idx: int) -> str:
    result = ""
    n = idx
    while n > 0:
        n, rem = divmod(n - 1, 26)
        result = chr(ord("A") + rem) + result
    return result


def _parse_cell(cell: str) -> tuple[int, int]:
    letters = "".join(ch for ch in cell if ch.isalpha())
    digits = "".join(ch for ch in cell if ch.isdigit())
    return int(digits), _col_to_idx(letters)


def _cell_name(row: int, col: int) -> str:
    return f"{_idx_to_col(col)}{row}"


class _FakeRange:
    def __init__(self, sheet, start_row: int, start_col: int, row_count: int, col_count: int):
        self._sheet = sheet
        self._sr = start_row
        self._sc = start_col
        self._rows = row_count
        self._cols = col_count
        self._formula = ""
        self._color = None

    @property
    def address(self):
        start = _cell_name(self._sr, self._sc)
        end = _cell_name(self._sr + self._rows - 1, self._sc + self._cols - 1)
        return start if start == end else f"{start}:{end}"

    @property
    def row(self):
        return self._sr

    @property
    def column(self):
        return self._sc

    @property
    def rows(self):
        class _Rows:
            def __init__(self, count: int):
                self.count = count

        return _Rows(self._rows)

    @property
    def columns(self):
        class _Cols:
            def __init__(self, count: int):
                self.count = count

        return _Cols(self._cols)

    @property
    def value(self):
        if self._rows == 1 and self._cols == 1:
            return self._sheet._values.get((self._sr, self._sc))
        rows = []
        for r in range(self._rows):
            row_values = []
            for c in range(self._cols):
                row_values.append(self._sheet._values.get((self._sr + r, self._sc + c)))
            rows.append(row_values)
        return rows

    @value.setter
    def value(self, raw):
        if self._rows == 1 and self._cols == 1 and not isinstance(raw, list):
            self._sheet._values[(self._sr, self._sc)] = raw
            return

        rows = raw if isinstance(raw, list) and raw and isinstance(raw[0], list) else [raw]
        for r in range(self._rows):
            for c in range(self._cols):
                val = None
                if r < len(rows):
                    row = rows[r]
                    if isinstance(row, list) and c < len(row):
                        val = row[c]
                    elif c == 0 and not isinstance(row, list):
                        val = row
                self._sheet._values[(self._sr + r, self._sc + c)] = val

    @property
    def color(self):
        return self._color

    @color.setter
    def color(self, rgb):
        self._color = rgb
        for r in range(self._rows):
            for c in range(self._cols):
                self._sheet._colors[(self._sr + r, self._sc + c)] = rgb

    @property
    def formula(self):
        return self._formula

    @formula.setter
    def formula(self, value):
        self._formula = value
        for r in range(self._rows):
            for c in range(self._cols):
                self._sheet._formulas[(self._sr + r, self._sc + c)] = value

    def options(self, **_kwargs):
        return self

    def resize(self, row_count: int, col_count: int):
        return _FakeRange(self._sheet, self._sr, self._sc, row_count, col_count)

    def offset(self, row_offset: int, col_offset: int):
        return _FakeRange(self._sheet, self._sr + row_offset, self._sc + col_offset, 1, 1)

    @property
    def api(self):
        return _FakeRangeApi(self._sheet, self._sr, self._sc, self._rows, self._cols)


class _FakeBorder:
    def __init__(self):
        self.LineStyle = -4142
        self.Weight = None
        self.Color = None


class _FakeRangeApi:
    def __init__(self, sheet, start_row: int, start_col: int, row_count: int, col_count: int):
        self._sheet = sheet
        self._sr = start_row
        self._sc = start_col
        self._rows = row_count
        self._cols = col_count

    def Borders(self, edge: int):
        key = (self._sr, self._sc, edge)
        if key not in self._sheet._borders:
            self._sheet._borders[key] = _FakeBorder()
        return self._sheet._borders[key]


class _FakeSheet:
    def __init__(self, name: str):
        self.name = name
        self._values: dict[tuple[int, int], object] = {}
        self._colors: dict[tuple[int, int], tuple[int, int, int]] = {}
        self._formulas: dict[tuple[int, int], str] = {}
        self._borders: dict[tuple[int, int, int], _FakeBorder] = {}

    def seed(self, ref: str, values):
        rng = self.range(ref)
        rng.value = values

    def range(self, ref: str):
        upper = ref.upper()
        if ":" in upper:
            left, right = upper.split(":", 1)
            # A:A 형태 지원
            if left.isalpha() and right.isalpha():
                start_col = _col_to_idx(left)
                end_col = _col_to_idx(right)
                return _FakeRange(self, 1, start_col, 200, end_col - start_col + 1)
            sr, sc = _parse_cell(left)
            er, ec = _parse_cell(right)
            return _FakeRange(self, sr, sc, er - sr + 1, ec - sc + 1)
        row, col = _parse_cell(upper)
        return _FakeRange(self, row, col, 1, 1)

    @property
    def used_range(self):
        populated = [k for k, v in self._values.items() if v is not None]
        if not populated:
            return _FakeRange(self, 1, 1, 1, 1)
        rows = [r for r, _ in populated]
        cols = [c for _, c in populated]
        min_row, max_row = min(rows), max(rows)
        min_col, max_col = min(cols), max(cols)
        return _FakeRange(self, min_row, min_col, max_row - min_row + 1, max_col - min_col + 1)


class _FakeSheets(list):
    @property
    def active(self):
        return self[0] if self else None


class _FakeWorkbook:
    def __init__(self, name: str, fullname: str, sheets: list[_FakeSheet]):
        self.name = name
        self.fullname = fullname
        self.sheets = _FakeSheets(sheets)
        self.saved = False

    def save(self):
        self.saved = True


class _FakeApp:
    def __init__(self, books):
        self.books = books


class _FakeApps:
    def __init__(self, active):
        self.active = active


class _FakeXw:
    def __init__(self, active_app):
        self.apps = _FakeApps(active_app)


def _build_service():
    s1 = _FakeSheet("Sheet1")
    s1.seed("A1:B2", [[10, 20], [30, 40]])
    s1.seed("A1", "헤더")
    s2 = _FakeSheet("Sheet2")
    s2.seed("C1:C2", [[1], [2]])
    wb1 = _FakeWorkbook(name="sales.xlsx", fullname=r"C:\work\sales.xlsx", sheets=[s1, s2])

    main = _FakeSheet("Main")
    main.seed("A1", "상품")
    wb2 = _FakeWorkbook(name="inventory.xlsx", fullname=r"C:\work\inventory.xlsx", sheets=[main])

    service = ExcelLiveService(xw_module=_FakeXw(_FakeApp([wb1, wb2])))
    return service, wb1, wb2


def test_list_workbooks_returns_opened_workbooks():
    service, wb1, wb2 = _build_service()

    rows = service.list_workbooks()

    assert len(rows) == 2
    assert rows[0]["workbook_id"] == wb1.fullname
    assert rows[0]["name"] == "sales.xlsx"
    assert rows[0]["active_sheet"] == "Sheet1"
    assert rows[1]["workbook_id"] == wb2.fullname


def test_select_workbook_sets_selected_id():
    service, wb1, _ = _build_service()

    result = service.select_workbook("sales.xlsx")

    assert result == {"selected": True, "workbook_id": wb1.fullname}
    assert service.get_selected_workbook_id() == wb1.fullname


def test_read_range_uses_selected_workbook_when_id_missing():
    service, wb1, _ = _build_service()
    service.select_workbook(wb1.fullname)

    result = service.read_range(workbook_id=None, sheet_name="Sheet1", range_ref="A1:B2")

    assert result["row_count"] == 2
    assert result["col_count"] == 2
    assert result["values"] == [["헤더", 20], [30, 40]]


def test_read_range_normalizes_single_cell_to_2d_array():
    service, wb1, _ = _build_service()

    result = service.read_range(workbook_id=wb1.fullname, sheet_name="Sheet1", range_ref="A1")

    assert result["values"] == [["헤더"]]
    assert result["row_count"] == 1
    assert result["col_count"] == 1


def test_read_range_empty_cell_returns_1x1_none():
    service, wb1, _ = _build_service()
    result = service.read_range(workbook_id=wb1.fullname, sheet_name="Sheet1", range_ref="B9")
    assert result["values"] == [[None]]
    assert result["row_count"] == 1
    assert result["col_count"] == 1


def test_read_range_raises_when_workbook_missing():
    service, _, _ = _build_service()

    try:
        service.read_range("missing.xlsx", "Sheet1", "A1")
        assert False, "WorkbookNotFoundError expected"
    except WorkbookNotFoundError:
        pass


def test_read_range_raises_when_sheet_missing():
    service, wb1, _ = _build_service()

    try:
        service.read_range(wb1.fullname, "NoSheet", "A1")
        assert False, "WorksheetNotFoundError expected"
    except WorksheetNotFoundError:
        pass


def test_is_available_false_when_excel_not_running():
    service = ExcelLiveService(xw_module=_FakeXw(active_app=None))
    assert service.is_available() is False


def test_list_workbooks_raises_when_excel_not_running():
    service = ExcelLiveService(xw_module=_FakeXw(active_app=None))

    try:
        service.list_workbooks()
        assert False, "ExcelConnectionError expected"
    except ExcelConnectionError:
        pass


def test_write_range_writes_cells_and_returns_address():
    service, wb1, _ = _build_service()
    result = service.write_range(
        workbook_id=wb1.fullname,
        sheet_name="Sheet1",
        start_cell="B3",
        values_2d=[[100, 200], [300, 400]],
    )
    assert result["written_cells"] == 4
    read = service.read_range(wb1.fullname, "Sheet1", "B3:C4")
    assert read["values"] == [[100, 200], [300, 400]]


def test_highlight_by_condition_changes_only_matching_cells():
    service, wb1, _ = _build_service()
    service.write_range(
        workbook_id=wb1.fullname,
        sheet_name="Sheet1",
        start_cell="A5",
        values_2d=[[10], [60], [70], [20]],
    )
    result = service.highlight_by_condition(
        workbook_id=wb1.fullname,
        sheet_name="Sheet1",
        target_range="A5:A8",
        operator=">=",
        threshold=50,
        fill_color="#FFFF00",
    )
    assert result["matched_cells"] == 2
    assert result["changed_cells"] == 2
    # 테두리도 함께 적용되는지 확인
    # edge: 7(left),8(top),9(bottom),10(right)
    for edge in (7, 8, 9, 10):
        assert service._find_sheet(wb1, "Sheet1")._borders[(6, 1, edge)].LineStyle == 1  # A6
        assert service._find_sheet(wb1, "Sheet1")._borders[(7, 1, edge)].LineStyle == 1  # A7


def test_ensure_visual_gridline_keeps_existing_border():
    service, wb1, _ = _build_service()
    sheet = service._find_sheet(wb1, "Sheet1")
    cell = sheet.range("B5")
    # 기존 테두리가 있는 상태를 가정
    for edge in (7, 8, 9, 10):
        border = cell.api.Borders(edge)
        border.LineStyle = 1
        border.Weight = 3
        border.Color = 255

    service._ensure_visual_gridline(cell, (255, 255, 255))

    for edge in (7, 8, 9, 10):
        border = cell.api.Borders(edge)
        assert border.LineStyle == 1
        assert border.Weight == 3
        assert border.Color == 255


def test_highlight_full_column_uses_used_range_rows():
    service, wb1, _ = _build_service()
    service.write_range(
        workbook_id=wb1.fullname,
        sheet_name="Sheet1",
        start_cell="A5",
        values_2d=[[10], [60], [70], [20]],
    )
    result = service.highlight_by_condition(
        workbook_id=wb1.fullname,
        sheet_name="Sheet1",
        target_range="A:A",
        operator=">=",
        threshold=50,
        fill_color="#FFFF00",
    )
    assert result["matched_cells"] == 2
    assert result["changed_cells"] == 2
    assert result["address"] == "A1:A8"


def test_apply_border_sets_all_edges_and_inside_lines():
    service, wb1, _ = _build_service()
    service.write_range(
        workbook_id=wb1.fullname,
        sheet_name="Sheet1",
        start_cell="B2",
        values_2d=[[1, 2], [3, 4]],
    )
    result = service.apply_border(
        workbook_id=wb1.fullname,
        sheet_name="Sheet1",
        target_range="B2:C3",
        line_style="continuous",
        weight="thin",
        color="#D9D9D9",
    )
    assert result["changed_cells"] == 4
    assert result["address"] == "B2:C3"

    border_map = service._find_sheet(wb1, "Sheet1")._borders
    for edge in (7, 8, 9, 10, 11, 12):
        border = border_map[(2, 2, edge)]
        assert border.LineStyle == 1
        assert border.Weight == 2
        assert border.Color is not None


def test_set_formula_applies_to_entire_range():
    service, wb1, _ = _build_service()
    result = service.set_formula(
        workbook_id=wb1.fullname,
        sheet_name="Sheet1",
        range_ref="D1:D3",
        formula_a1="=SUM(A1:B1)",
    )
    assert result["formula_applied_cells"] == 3


def test_save_workbook_marks_saved_and_returns_metadata():
    service, wb1, _ = _build_service()
    result = service.save_workbook(wb1.fullname)
    assert result["saved"] is True
    assert result["name"] == wb1.name
    assert wb1.saved is True

