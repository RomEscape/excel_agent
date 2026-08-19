"""블라스트 반경 가드 — 지목 밖의 값을 덮는가."""

from __future__ import annotations

import pytest

from office_claw_sidecar.services.excel_write_scope import (
    Rect,
    assess,
    parse_ref,
    stated_scope,
    write_footprint,
)


def reader_factory(data: dict[str, list[list]]):
    """data[sheet] = 시트 전체 격자(1행 1열부터). read_rect(sheet, ref) 흉내."""

    def read_rect(sheet: str, ref: str):
        rect = parse_ref(ref, sheet)
        grid = data.get(sheet)
        if rect is None or grid is None:
            raise KeyError(sheet)
        out = []
        for r in range(rect.r1, rect.r2 + 1):
            row = grid[r - 1] if 0 <= r - 1 < len(grid) else []
            out.append([row[c - 1] if 0 <= c - 1 < len(row) else None for c in range(rect.c1, rect.c2 + 1)])
        return out

    return read_rect


class TestScopeParsing:
    def test_parse_ref_forms(self):
        assert parse_ref("A1:C9", "S") == Rect("S", 1, 1, 9, 3)
        assert parse_ref("B2", "S") == Rect("S", 2, 2, 2, 2)
        assert parse_ref("'다른 시트'!B2", "S") == Rect("다른 시트", 2, 2, 2, 2)
        assert parse_ref("", "S") is None

    def test_stated_scope_collects_ranges_cells_and_columns(self):
        rects = stated_scope(message="A1:C9와 F2에 그리고 D열도", context_range=None, active_sheet="대시보드")
        assert Rect("대시보드", 1, 1, 9, 3) in rects
        assert Rect("대시보드", 2, 6, 2, 6) in rects
        assert any(r.c1 == 4 and r.r2 > 1000 for r in rects)

    def test_stated_scope_includes_the_paste_selection(self):
        rects = stated_scope(message="여기에 값 넣어줘", context_range="A1:B3", active_sheet="데이터")
        assert Rect("데이터", 1, 1, 3, 2) in rects

    def test_footprint_of_a_write_covers_the_whole_grid(self):
        steps = [{"action": "excel_live.write_range", "params": {"start_cell": "B2", "values_2d": [[1, 2, 3], [4, 5, 6]]}}]
        assert write_footprint(steps, "S") == [Rect("S", 2, 2, 3, 4)]

    def test_format_steps_are_not_in_the_footprint(self):
        steps = [{"action": "excel_live.fill_range", "params": {"target_range": "A1:Z99"}}]
        assert write_footprint(steps, "S") == []


class TestBlastRadius:
    """2026-08-19 결과 워크북 감사에서 실제로 데이터가 지워진 사고의 재현."""

    def _grade_book(self):
        return {
            "성적부": [["번호", "이름"], [1, "학생1"], [2, "학생2"], *[[i, f"학생{i}"] for i in range(3, 12)]],
            "대시보드": [["AI 기반 학사 대시보드"], [], [], [], [], [], [], [], [], ["총 결석"]],
        }

    def test_a_formula_landing_on_the_source_sheet_is_flagged(self):
        # "B10에다 성적부 시트 결석 다 더한 값 가져와줘" — 지목은 대시보드!B10, 계획은 성적부!B10.
        data = self._grade_book()
        steps = [
            {
                "action": "excel_live.set_formula",
                "params": {"range_ref": "B10", "formula_a1": "=SUM(F2:F17)", "sheet_name": "성적부"},
            }
        ]
        verdict = assess(
            steps=steps,
            message="B10에다 성적부 시트 결석 다 더한 값 가져와줘",
            context_range=None,
            active_sheet="대시보드",
            read_rect=reader_factory(data),
        )
        assert verdict.is_risky, verdict
        assert verdict.risky[0].sheet == "성적부"
        assert verdict.risky[0].address == "B10"
        assert "학생" in str(verdict.risky[0].value)
        assert "성적부!B10" in verdict.summary()

    def test_the_same_formula_on_the_named_sheet_is_safe(self):
        data = self._grade_book()
        steps = [
            {
                "action": "excel_live.set_formula",
                "params": {"range_ref": "B10", "formula_a1": "=SUM('성적부'!F2:F11)", "sheet_name": "대시보드"},
            }
        ]
        verdict = assess(
            steps=steps,
            message="B10에다 성적부 시트 결석 다 더한 값 가져와줘",
            context_range=None,
            active_sheet="대시보드",
            read_rect=reader_factory(data),
        )
        assert not verdict.is_risky, verdict

    def test_rewriting_the_range_the_user_pointed_at_is_safe(self):
        data = {"데이터": [["지역", "건수"], ["수도권", 10], ["충청권", 20]]}
        steps = [
            {"action": "excel_live.write_range", "params": {"start_cell": "A1", "values_2d": [["지역", "건수"], ["수도권", 99]]}}
        ]
        verdict = assess(
            steps=steps,
            message="A1:B2에 지역,건수; 수도권,99 입력해줘",
            context_range=None,
            active_sheet="데이터",
            read_rect=reader_factory(data),
        )
        assert not verdict.is_risky, verdict

    def test_a_grid_spilling_past_the_selection_onto_data_is_flagged(self):
        # 붙여넣기가 선택보다 넓어 옆 칸의 값을 먹는 경우.
        data = {"데이터": [["a", "b", "지키고 싶은 값"], [1, 2, "여기도"]]}
        steps = [
            {"action": "excel_live.write_range", "params": {"start_cell": "A1", "values_2d": [["x", "y", "z"], [1, 2, 3]]}}
        ]
        verdict = assess(
            steps=steps,
            message="여기에 값 넣어줘",
            context_range="A1:B2",
            active_sheet="데이터",
            read_rect=reader_factory(data),
        )
        assert verdict.is_risky
        assert {c.address for c in verdict.risky} == {"C1", "C2"}

    def test_spilling_onto_blank_cells_is_not_flagged(self):
        data = {"데이터": [["a", "b"], [1, 2]]}
        steps = [
            {"action": "excel_live.write_range", "params": {"start_cell": "A1", "values_2d": [["x", "y", "z"], [1, 2, 3]]}}
        ]
        verdict = assess(
            steps=steps,
            message="여기에 값 넣어줘",
            context_range="A1:B2",
            active_sheet="데이터",
            read_rect=reader_factory(data),
        )
        assert not verdict.is_risky, verdict

    @pytest.mark.parametrize("message", ["시트 전체 지워줘", "표 전체 비워줘", "다 지워"])
    def test_a_whole_scope_request_is_not_second_guessed(self, message):
        data = {"데이터": [["a", "b"], [1, 2]]}
        steps = [{"action": "excel_live.clear_range", "params": {"target_range": "A1:B2"}}]
        verdict = assess(
            steps=steps, message=message, context_range=None, active_sheet="데이터", read_rect=reader_factory(data)
        )
        assert not verdict.is_risky
        assert verdict.checked is False

    def test_an_unreadable_range_passes_rather_than_blocking(self):
        steps = [{"action": "excel_live.write_range", "params": {"start_cell": "Z1", "values_2d": [[1]]}}]

        def boom(sheet, ref):
            raise RuntimeError("못 읽음")

        verdict = assess(
            steps=steps, message="A1에 값", context_range=None, active_sheet="데이터", read_rect=boom
        )
        assert not verdict.is_risky
        assert verdict.checked is False

    def test_clearing_without_a_stated_range_is_flagged(self):
        # "차트 전부 지워 주세요, 데이터는 그대로 두시고요" → B2 데이터까지 지워졌고 카드도 없었다
        # (2026-08-19 블라인드 게이트). 자리를 안 짚은 지우기는 무엇을 지우는지 보여 주고 확인받는다.
        data = {"데이터": [["지역", "건수"], ["수도권", 10]]}
        steps = [
            {"action": "excel_live.delete_charts", "params": {}},
            {"action": "excel_live.clear_range", "params": {"target_range": "A1:B2"}},
        ]
        verdict = assess(
            steps=steps,
            message="차트 전부 지워 주세요, 데이터는 그데로 두시고요",
            context_range=None,
            active_sheet="데이터",
            read_rect=reader_factory(data),
        )
        assert verdict.is_risky, verdict
        assert {c.address for c in verdict.risky} == {"A1", "B1", "A2", "B2"}

    def test_writing_without_a_stated_range_is_still_skipped(self):
        # 지우기가 아닌 쓰기는 활성 셀 경로가 정상이라 그대로 통과시킨다(가드를 시끄럽게 하지 않는다).
        data = {"데이터": [["지역"]]}
        steps = [{"action": "excel_live.write_range", "params": {"start_cell": "A1", "values_2d": [["완료"]]}}]
        verdict = assess(
            steps=steps, message="완료라고 써줘", context_range=None, active_sheet="데이터", read_rect=reader_factory(data)
        )
        assert not verdict.is_risky
        assert verdict.checked is False

    def test_placeholders_are_resolved_like_the_executor_does(self):
        # 실행기는 __ACTIVE_SELECTION__ 을 실행 직전에 푼다. 가드가 안 풀면 clear_range를 통째로 놓친다
        # (2026-08-19 적대적 검증). 같은 값을 보게 한다.
        data = {"데이터": [["지역", "건수"], ["수도권", 10]]}
        steps = [{"action": "excel_live.clear_range", "params": {"target_range": "__ACTIVE_SELECTION__"}}]
        verdict = assess(
            steps=steps,
            message="차트 지우고 정리해줘",
            context_range=None,
            active_sheet="데이터",
            read_rect=reader_factory(data),
            resolve_placeholder=lambda sheet, token: "A1:B2",
        )
        assert verdict.is_risky, verdict
        assert {c.address for c in verdict.risky} == {"A1", "B1", "A2", "B2"}

    def test_without_a_resolver_a_placeholder_is_skipped_not_guessed(self):
        data = {"데이터": [["지역", "건수"], ["수도권", 10]]}
        steps = [{"action": "excel_live.clear_range", "params": {"target_range": "__ACTIVE_SELECTION__"}}]
        verdict = assess(
            steps=steps, message="정리해줘", context_range=None, active_sheet="데이터", read_rect=reader_factory(data)
        )
        assert not verdict.is_risky
        assert verdict.checked is False

    def test_find_replace_is_deliberately_out_of_scope(self):
        # 치환은 본래 표 전체를 훑는 일괄 작업이라 이 가드를 걸면 매번 경고가 뜬다(거짓 양성).
        data = {"데이터": [["수도권", 10], ["충청권", 20]]}
        steps = [
            {"action": "excel_live.find_replace", "params": {"target_range": "A1:B2", "find_text": "수도권", "replace_text": "서울권"}}
        ]
        verdict = assess(
            steps=steps, message="수도권을 서울권으로 바꿔줘", context_range=None, active_sheet="데이터", read_rect=reader_factory(data)
        )
        assert not verdict.is_risky
        assert verdict.checked is False

    def test_only_the_cells_outside_the_scope_are_read(self):
        # 넓은 쓰기에서 발자국 전체가 아니라 지목 밖의 바운딩 박스만 읽는다(왕복 비용).
        data = {"데이터": [["a"] * 10 for _ in range(10)]}
        seen: list[str] = []

        def spy(sheet, ref):
            seen.append(ref)
            return reader_factory(data)(sheet, ref)

        steps = [
            {"action": "excel_live.write_range", "params": {"start_cell": "A1", "values_2d": [["x"] * 5 for _ in range(5)]}}
        ]
        assess(
            steps=steps, message="A1:D4에 넣어줘", context_range=None, active_sheet="데이터", read_rect=spy
        )
        assert (seen and seen[0] == "A1:E5") or seen[0] == "A5:E5", seen
