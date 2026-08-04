from office_claw_sidecar.services.excel_workbook_digest import (
    build_workbook_digest,
    invalidate_digest_cache,
    render_workbook_digest,
)


class _FakeService:
    def __init__(self):
        self.read_calls: list[tuple[str, str]] = []

    def list_sheets(self, workbook_id):
        return {"sheets": ["매출", "예측1"], "count": 2, "active_sheet": "매출"}

    def get_used_range_ref(self, workbook_id, sheet_name):
        return "A1:E9" if sheet_name == "매출" else "A1"

    def read_range(self, workbook_id, sheet_name, range_ref):
        self.read_calls.append((sheet_name, range_ref))
        if sheet_name != "매출":
            return {"values": [[None]], "address": range_ref}
        return {
            "values": [
                ["날짜", "월", "카테고리", "상태", "금액"],
                ["2026-01-01", 1, "A", "완료", 1000],
                ["2026-02-01", 2, "B", "진행중", 1440],
            ],
            "address": range_ref,
        }


def _fresh_digest(service):
    invalidate_digest_cache()
    return build_workbook_digest(service, workbook_id="wb", use_cache=False)


def test_digest_maps_headers_to_column_letters():
    digest = _fresh_digest(_FakeService())
    sales = next(s for s in digest["sheets"] if s["name"] == "매출")
    letters = {col["header"]: col["letter"] for col in sales["columns"]}
    assert letters["금액"] == "E"
    assert letters["날짜"] == "A"
    assert sales["used_range"] == "A1:E9"


def test_digest_limits_rows_read_from_large_range():
    service = _FakeService()
    _fresh_digest(service)
    # 비활성 시트는 머리글+샘플 몇 줄만 읽어야 한다.
    other_call = next(call for call in service.read_calls if call[0] == "예측1")
    assert other_call[1] == "A1"
    # 활성 시트는 값 후보 수집이 필요해 더 읽지만, 사용범위를 넘지는 않는다.
    sales_call = next(call for call in service.read_calls if call[0] == "매출")
    assert sales_call[1] == "A1:E9"


def test_digest_collects_value_candidates_for_active_sheet():
    digest = _fresh_digest(_FakeService())
    sales = next(s for s in digest["sheets"] if s["name"] == "매출")
    status = next(col for col in sales["columns"] if col["header"] == "상태")
    amount = next(col for col in sales["columns"] if col["header"] == "금액")
    assert status["categories"] == ["완료", "진행중"]
    assert amount["numeric"] is True
    assert "categories" not in amount


def test_render_includes_sheet_headers_and_sample():
    rendered = render_workbook_digest(_fresh_digest(_FakeService()))
    assert "시트 매출 (활성)" in rendered
    assert "E=금액" in rendered
    assert "예시행" in rendered
    assert "'상태' 값 후보: 완료, 진행중" in rendered


def test_digest_survives_service_errors():
    class _BrokenService(_FakeService):
        def get_used_range_ref(self, workbook_id, sheet_name):
            raise RuntimeError("boom")

    digest = _fresh_digest(_BrokenService())
    assert [s["name"] for s in digest["sheets"]] == ["매출", "예측1"]
    assert all(not s["columns"] for s in digest["sheets"])


def test_render_returns_empty_when_no_sheets():
    assert render_workbook_digest({"active_sheet": "", "sheets": []}) == ""
