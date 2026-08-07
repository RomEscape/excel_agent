"""excel_border의 순수 부분(색 변환·이름 정규화)과 플랫폼 분기 검증.

라이브 Excel이 없어도 도는 테스트만 둔다. 실제 적용은 macOS/Windows 실기기에서만
확인 가능하므로 여기서는 가짜 api 객체로 '어느 경로를 탔는지'까지만 본다.
"""

import pytest

from office_claw_sidecar.services import excel_border as eb


class TestColorConversion:
    def test_com_color_is_bgr_packed(self):
        # COM은 BGR 순서 — 빨강(255,0,0)이 정수 255가 된다.
        assert eb.rgb_to_com_color((255, 0, 0)) == 255
        assert eb.rgb_to_com_color((0, 255, 0)) == 65280
        assert eb.rgb_to_com_color((0, 0, 255)) == 16711680
        assert eb.rgb_to_com_color((0, 0, 0)) == 0
        assert eb.rgb_to_com_color((255, 255, 255)) == 16777215

    def test_applescript_color_is_16bit_list(self):
        # macOS는 0~65535 RGB 리스트. 257배(=65535/255)여야 순색이 정확히 맞는다.
        assert eb.rgb_to_applescript_color((255, 0, 0)) == [65535, 0, 0]
        assert eb.rgb_to_applescript_color((0, 0, 0)) == [0, 0, 0]
        assert eb.rgb_to_applescript_color((255, 255, 255)) == [65535, 65535, 65535]

    def test_applescript_color_not_left_shift(self):
        # << 8 로 만들면 255가 65280이 되어 순백이 미묘하게 어긋난다.
        assert eb.rgb_to_applescript_color((255, 255, 255)) != [65280, 65280, 65280]

    def test_color_values_are_clamped(self):
        assert eb.rgb_to_com_color((999, -5, 0)) == 255
        assert eb.rgb_to_applescript_color((999, -5, 0)) == [65535, 0, 0]


class TestNameNormalization:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("continuous", "continuous"),
            ("SOLID", "continuous"),
            ("실선", "continuous"),
            ("dashed", "dash"),
            ("점선", "dash"),
            ("없음", "none"),
            ("", "continuous"),
            (None, "continuous"),
            ("알 수 없는 값", "continuous"),
        ],
    )
    def test_line_style(self, raw, expected):
        assert eb.normalize_line_style(raw) == expected

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("thin", "thin"),
            ("얇게", "thin"),
            ("THICK", "thick"),
            ("굵게", "thick"),
            ("", "medium"),
            (None, "medium"),
        ],
    )
    def test_weight(self, raw, expected):
        assert eb.normalize_weight(raw) == expected


class FakeBorder:
    def __init__(self):
        self.LineStyle = None
        self.Weight = None
        self.Color = None


class FakeComRange:
    """Windows COM 경로를 흉내내는 가짜 range."""

    def __init__(self):
        self.borders = {}

    def Borders(self, index):
        return self.borders.setdefault(index, FakeBorder())


class TestWindowsPath:
    def test_applies_six_edges(self, monkeypatch):
        monkeypatch.setattr(eb, "is_macos", lambda: False)
        rng = FakeComRange()
        applied = eb.apply_borders(rng, line_style="continuous", weight="thin", rgb=(255, 0, 0))

        assert applied == 6
        # 바깥 4변 + 안쪽 격자 2개
        assert set(rng.borders) == {7, 8, 9, 10, 11, 12}
        b = rng.borders[7]
        assert b.LineStyle == 1
        assert b.Weight == 2
        assert b.Color == 255  # BGR 정수

    def test_none_range_raises(self, monkeypatch):
        monkeypatch.setattr(eb, "is_macos", lambda: False)
        with pytest.raises(eb.BorderUnsupportedError):
            eb.apply_borders(None)

    def test_outline_skips_existing_border(self, monkeypatch):
        monkeypatch.setattr(eb, "is_macos", lambda: False)
        rng = FakeComRange()
        # 이미 테두리가 그려진 edge는 건드리지 않아야 한다.
        rng.Borders(7).LineStyle = 1
        eb.apply_outline_if_absent(rng, (217, 217, 217))
        assert rng.borders[7].Color is None  # 유지됨
        assert rng.borders[8].Color is not None  # 비어 있던 곳만 채움

    def test_outline_swallows_errors(self, monkeypatch):
        monkeypatch.setattr(eb, "is_macos", lambda: False)

        class Exploding:
            def Borders(self, idx):
                raise RuntimeError("COM 실패")

        # 강조 표시의 보조 장치이므로 예외가 새어나가면 안 된다.
        eb.apply_outline_if_absent(Exploding(), (0, 0, 0))
        eb.apply_outline_if_absent(None, (0, 0, 0))


class TestPlatformDispatch:
    def test_macos_path_selected(self, monkeypatch):
        monkeypatch.setattr(eb, "is_macos", lambda: True)
        called = {}

        def fake_mac(api_range, style, weight, rgb):
            called["args"] = (style, weight, rgb)
            return 6

        monkeypatch.setattr(eb, "_apply_macos", fake_mac)
        eb.apply_borders(object(), line_style="실선", weight="굵게", rgb=(1, 2, 3))
        assert called["args"] == ("continuous", "thick", (1, 2, 3))

    def test_constant_tables_cover_all_normalized_names(self):
        # 정규화가 내놓는 이름은 두 플랫폼 테이블에 모두 있어야 한다.
        # 하나만 추가하면 다른 플랫폼에서 KeyError로 터진다.
        styles = set(eb._LINE_STYLE_ALIASES.values())
        weights = set(eb._WEIGHT_ALIASES.values())
        assert styles <= set(eb._COM_LINE_STYLE)
        assert styles <= set(eb._MAC_LINE_STYLE)
        assert weights <= set(eb._COM_WEIGHT)
        assert weights <= set(eb._MAC_WEIGHT)
