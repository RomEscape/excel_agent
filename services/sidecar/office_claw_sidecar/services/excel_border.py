"""Excel 테두리 서식의 플랫폼 차이를 한 곳에 가둔다.

Windows(COM)와 macOS(AppleScript)는 테두리 API가 형태부터 다르다:

    Windows : rng.api.Borders(7)              # 정수 인덱스
              border.LineStyle = 1            # 정수 상수
              border.Color = 255              # BGR 정수

    macOS   : rng.api.get_border(which_border=k.border_left)   # appscript 키워드
              border.line_style.set(k.continuous)
              border.color.set([65535, 0, 0])                  # 16bit RGB 리스트

**색이 가장 위험하다.** COM의 BGR 정수를 macOS에 그대로 넘기면 예외가 나지 않고
조용히 검정으로 칠해진다(실측: `color.set(255)` → readback `[0,0,0]`). 그래서
플랫폼 판정을 호출부에 맡기지 않고 이 모듈이 전부 흡수한다.

읽기와 쓰기의 스케일도 다르다 — set은 0~65535, get은 0~255를 돌려준다.
여기서는 set 경로만 쓰므로 65535 기준으로 변환한다.
"""

from __future__ import annotations

import sys
from typing import Any

__all__ = [
    "is_macos",
    "rgb_to_com_color",
    "rgb_to_applescript_color",
    "normalize_line_style",
    "normalize_weight",
    "apply_borders",
    "BorderUnsupportedError",
]


class BorderUnsupportedError(RuntimeError):
    """현재 플랫폼/환경에서 테두리 서식을 적용할 수 없음."""


def is_macos() -> bool:
    return sys.platform == "darwin"


# ── 이름 정규화 (순수) ───────────────────────────────────────────────────────

#: 사용자/LLM이 주는 표기 → 내부 표준 이름
_LINE_STYLE_ALIASES = {
    "continuous": "continuous",
    "solid": "continuous",
    "실선": "continuous",
    "dash": "dash",
    "dashed": "dash",
    "점선": "dash",
    "dot": "dot",
    "dotted": "dot",
    "double": "double",
    "이중": "double",
    "none": "none",
    "없음": "none",
}

_WEIGHT_ALIASES = {
    "hairline": "hairline",
    "thin": "thin",
    "얇게": "thin",
    "medium": "medium",
    "보통": "medium",
    "thick": "thick",
    "굵게": "thick",
}


def normalize_line_style(value: str | None, default: str = "continuous") -> str:
    key = (value or "").strip().lower()
    return _LINE_STYLE_ALIASES.get(key, default)


def normalize_weight(value: str | None, default: str = "medium") -> str:
    key = (value or "").strip().lower()
    return _WEIGHT_ALIASES.get(key, default)


# ── 색 변환 (순수) ───────────────────────────────────────────────────────────


def rgb_to_com_color(rgb: tuple[int, int, int]) -> int:
    """(R,G,B) → Excel COM Color 정수. COM은 BGR 순서로 패킹한다."""
    r, g, b = (max(0, min(255, int(c))) for c in rgb)
    return r + (g << 8) + (b << 16)


def rgb_to_applescript_color(rgb: tuple[int, int, int]) -> list[int]:
    """(R,G,B) 0~255 → AppleScript RGB 리스트 0~65535.

    257 = 65535 / 255. 단순히 << 8 하면 255가 65280이 되어 순백/순색이
    미묘하게 어긋난다.
    """
    return [max(0, min(255, int(c))) * 257 for c in rgb]


# ── 플랫폼별 상수 테이블 ─────────────────────────────────────────────────────

#: Windows COM XlBordersIndex — left, top, bottom, right, inside_v, inside_h
_COM_EDGES = (7, 8, 9, 10, 11, 12)

#: XlLineStyle
_COM_LINE_STYLE = {
    "continuous": 1,
    "dash": -4115,
    "dot": -4118,
    "double": -4119,
    "none": -4142,
}

#: XlBorderWeight
_COM_WEIGHT = {"hairline": 1, "thin": 2, "medium": -4138, "thick": 4}

#: macOS appscript 키워드 이름 — 실측으로 확인한 것만 담는다.
_MAC_EDGES = (
    "border_left",
    "border_top",
    "border_bottom",
    "border_right",
    "inside_vertical",
    "inside_horizontal",
)

_MAC_LINE_STYLE = {
    "continuous": "continuous",
    "dash": "dash",
    "dot": "dot",
    "double": "double",
    "none": "line_style_none",
}

_MAC_WEIGHT = {
    "hairline": "border_weight_hairline",
    "thin": "border_weight_thin",
    "medium": "border_weight_medium",
    "thick": "border_weight_thick",
}


# ── 적용 ─────────────────────────────────────────────────────────────────────


def _apply_windows(api_range: Any, style: str, weight: str, rgb: tuple[int, int, int]) -> int:
    line_style_value = _COM_LINE_STYLE[style]
    weight_value = _COM_WEIGHT[weight]
    color_value = rgb_to_com_color(rgb)

    applied = 0
    for edge in _COM_EDGES:
        border = api_range.Borders(edge)
        border.LineStyle = line_style_value
        border.Weight = weight_value
        border.Color = color_value
        applied += 1
    return applied


def _apply_macos(api_range: Any, style: str, weight: str, rgb: tuple[int, int, int]) -> int:
    try:
        from appscript import k  # macOS 전용 — lazy import
    except ImportError as exc:  # pragma: no cover - macOS 외에서는 도달하지 않음
        raise BorderUnsupportedError(
            "macOS에서 테두리를 적용하려면 appscript가 필요합니다."
        ) from exc

    line_style_kw = getattr(k, _MAC_LINE_STYLE[style])
    weight_kw = getattr(k, _MAC_WEIGHT[weight])
    color_value = rgb_to_applescript_color(rgb)

    applied = 0
    for edge_name in _MAC_EDGES:
        try:
            border = api_range.get_border(which_border=getattr(k, edge_name))
            border.line_style.set(line_style_kw)
            border.weight.set(weight_kw)
            border.color.set(color_value)
            applied += 1
        except Exception:
            # 단일 범위(1x1)에는 안쪽 격자가 없는 등, 일부 edge는 정상적으로
            # 적용 대상이 아니다. 전부 실패하면 아래에서 걸러진다.
            continue

    if applied == 0:
        raise BorderUnsupportedError(
            "테두리를 적용하지 못했습니다. Excel이 응답하지 않거나 범위가 유효하지 않습니다."
        )
    return applied


#: 바깥 4변만 — 안쪽 격자는 셀 단위 처리에서 의미가 없다.
_COM_OUTER_EDGES = (7, 8, 9, 10)
_MAC_OUTER_EDGES = ("border_left", "border_top", "border_bottom", "border_right")

#: XlLineStyle의 "없음". 이 값이면 아직 테두리가 안 그려진 것으로 본다.
_COM_LINE_STYLE_NONE = -4142


def apply_outline_if_absent(api_range: Any, rgb: tuple[int, int, int]) -> None:
    """셀 테두리가 비어 있을 때만 얇은 선을 그어 시인성을 높인다.

    이미 사용자가 그려 둔 테두리는 건드리지 않는다. 강조 표시의 보조 장치이므로
    실패해도 조용히 넘어간다 — 이것 때문에 강조 자체가 실패하면 안 된다.
    """
    if api_range is None:
        return

    try:
        if is_macos():
            from appscript import k

            for edge_name in _MAC_OUTER_EDGES:
                border = api_range.get_border(which_border=getattr(k, edge_name))
                try:
                    # 이미 선이 있으면 유지. 조회 실패는 "없음"으로 간주한다.
                    if border.line_style.get() != k.line_style_none:
                        continue
                except Exception:
                    pass
                border.line_style.set(k.continuous)
                border.weight.set(k.border_weight_thin)
                border.color.set(rgb_to_applescript_color(rgb))
        else:
            color_value = rgb_to_com_color(rgb)
            for edge in _COM_OUTER_EDGES:
                border = api_range.Borders(edge)
                line_style = getattr(border, "LineStyle", _COM_LINE_STYLE_NONE)
                if line_style not in (None, 0, _COM_LINE_STYLE_NONE):
                    continue
                border.LineStyle = _COM_LINE_STYLE["continuous"]
                border.Weight = _COM_WEIGHT["thin"]
                border.Color = color_value
    except Exception:
        # 테마/권한 환경에 따라 실패 가능 — 비치명적이므로 삼킨다.
        return


def apply_borders(
    api_range: Any,
    *,
    line_style: str = "continuous",
    weight: str = "medium",
    rgb: tuple[int, int, int] = (0, 0, 0),
) -> int:
    """범위의 6개 경계(바깥 4변 + 안쪽 격자)에 테두리를 적용한다.

    :returns: 실제로 적용된 edge 수
    :raises BorderUnsupportedError: 적용 경로를 찾지 못한 경우
    """
    if api_range is None:
        raise BorderUnsupportedError(
            "경계선을 적용할 수 없습니다. Excel API 객체를 찾지 못했습니다."
        )

    style = normalize_line_style(line_style)
    wt = normalize_weight(weight)

    if is_macos():
        return _apply_macos(api_range, style, wt, rgb)
    return _apply_windows(api_range, style, wt, rgb)
