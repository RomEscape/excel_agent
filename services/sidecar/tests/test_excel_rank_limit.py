"""수량 한정어 해석 — "상위 3개"가 실제 기준값이 되는지."""

from __future__ import annotations

from office_claw_sidecar.services import excel_rank_limit as rank_limit

_DIGEST = {
    "active_sheet": "매출",
    "sheets": [
        {
            "name": "매출",
            "used_range": "A1:D9",
            "columns": [
                {"letter": "A", "header": "코드"},
                {"letter": "B", "header": "지역", "categories": ["서울", "부산"]},
                {"letter": "C", "header": "금액", "numeric": True},
                {"letter": "D", "header": "날짜"},
            ],
            "sample_rows": [["A-001", "서울", "520", "2026-01-05"]],
        }
    ],
}
_AMOUNTS = [[520], [180], [340], [610], [90], [430], [275], [250]]


class TestDetect:
    def test_explicit_rank_word(self):
        found = rank_limit.detect("금액이 높은 상위 3개 행을 노란색으로 강조해줘")
        assert found is not None
        assert (found.count, found.descending) == (3, True)
        assert found.metric_term == "금액"

    def test_comparative_without_rank_word(self):
        found = rank_limit.detect("매출이 높은 10개 제품을 강조해줘")
        assert found is not None
        assert (found.count, found.descending) == (10, True)
        assert found.metric_term == "매출"

    def test_bottom_is_ascending(self):
        found = rank_limit.detect("하위 5개만 빨갛게")
        assert found is not None
        assert found.descending is False

    def test_plain_count_is_not_a_rank_limit(self):
        # "2행 2열 표"의 숫자를 한정어로 오해하면 멀쩡한 표 만들기가 플래너로 샌다.
        assert rank_limit.detect("2행 2열 표 만들어줘") is None
        assert rank_limit.detect("C3에 120 입력해줘") is None
        assert rank_limit.detect("금액 높은 순으로 정렬해줘") is None


class TestThreshold:
    def test_kth_largest(self):
        limit = rank_limit.RankLimit(count=3, descending=True)
        assert rank_limit.threshold_for([520, 180, 340, 610, 90, 430], limit) == 430

    def test_kth_smallest(self):
        limit = rank_limit.RankLimit(count=2, descending=False)
        assert rank_limit.threshold_for([520, 180, 340, 610], limit) == 340

    def test_text_numbers_count(self):
        limit = rank_limit.RankLimit(count=2, descending=True)
        assert rank_limit.threshold_for(["1,200", "800", "", None, "abc"], limit) == 800

    def test_too_few_values_gives_up(self):
        # 지어낸 기준값으로 전체를 칠하느니 아무것도 하지 않는 편이 낫다.
        limit = rank_limit.RankLimit(count=5, descending=True)
        assert rank_limit.threshold_for([1, 2], limit) is None


class TestResolveStep:
    def test_builds_condition_from_real_values(self):
        step = rank_limit.resolve_step(
            "금액이 높은 상위 3개 행을 노란색으로 강조해줘",
            _DIGEST,
            sheet_name="매출",
            read_column=lambda ref: _AMOUNTS if ref == "C2:C9" else [],
        )
        assert step is not None
        assert step["action"] == "excel_live.highlight_by_condition"
        assert step["params"]["target_range"] == "C2:C9"
        assert step["params"]["operator"] == ">="
        assert step["params"]["threshold"] == 430

    def test_single_numeric_column_needs_no_metric_word(self):
        step = rank_limit.resolve_step(
            "상위 2개를 강조해줘",
            _DIGEST,
            sheet_name="매출",
            read_column=lambda _ref: _AMOUNTS,
        )
        assert step is not None
        assert step["params"]["threshold"] == 520

    def test_ambiguous_metric_gives_up(self):
        digest = {
            "active_sheet": "매출",
            "sheets": [
                {
                    "name": "매출",
                    "used_range": "A1:C9",
                    "columns": [
                        {"letter": "A", "header": "코드"},
                        {"letter": "B", "header": "수량", "numeric": True},
                        {"letter": "C", "header": "금액", "numeric": True},
                    ],
                }
            ],
        }
        # 숫자 열이 둘인데 어느 쪽인지 말하지 않았다. 찍으면 조용한 오답이 된다.
        assert (
            rank_limit.resolve_step(
                "상위 2개를 강조해줘",
                digest,
                sheet_name="매출",
                read_column=lambda _ref: _AMOUNTS,
            )
            is None
        )

    def test_no_rank_limit_means_no_step(self):
        assert (
            rank_limit.resolve_step(
                "금액 열을 노란색으로 칠해줘",
                _DIGEST,
                sheet_name="매출",
                read_column=lambda _ref: _AMOUNTS,
            )
            is None
        )
