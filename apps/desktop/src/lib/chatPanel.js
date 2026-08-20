/**
 * chatPanel — 채팅 패널의 크기 계약 (순수).
 *
 * 최종 와이어프레임에서 채팅은 페이지가 아니라 본문 위의 패널이고, 크기가
 * 두 단계로 변한다 (design/desktop-shell/SCREENS.md B-2 / B-3):
 *
 *   docked   390×900  — 본문을 962px로 밀어내고 오른쪽에 붙는다 (Frame 168)
 *   floating 390×507  — 본문(1356px) 위에 떠서 우하단에 겹친다 (Frame 169)
 *
 * 패널 상단의 `tabler:aspect-ratio` 아이콘이 이 둘을 오간다.
 *
 * 폭 390px은 두 모드가 공유한다 — 토글할 때 가로가 같이 변하면 본문 표(엑셀)의
 * 열 위치까지 흔들려서 "크기 조절"이 아니라 "레이아웃 리플로우"로 읽힌다.
 */

/** 두 모드가 공유하는 패널 폭 (px). */
export const PANEL_WIDTH = 390;

/** 플로팅 모드의 높이 (px). 도킹은 지면 전체 높이라 상수가 없다. */
export const FLOATING_HEIGHT = 507;

export const PANEL_MODES = Object.freeze(["docked", "floating"]);

export const DEFAULT_PANEL_MODE = "docked";

/** 저장값·외부 입력을 안전한 모드로 좁힌다. */
export function normalizePanelMode(mode) {
  return PANEL_MODES.includes(mode) ? mode : DEFAULT_PANEL_MODE;
}

/** 크기 토글이 누를 다음 모드. */
export function nextPanelMode(mode) {
  return normalizePanelMode(mode) === "docked" ? "floating" : "docked";
}

/**
 * 모드별 표시 정보 — 아이콘 title/aria에 쓴다.
 * UI가 "지금 도킹이니까 다음은 플로팅"을 매번 직접 계산하지 않도록 여기서 준다.
 */
export function panelToggleLabel(mode) {
  return normalizePanelMode(mode) === "docked"
    ? "채팅 창 띄우기 (플로팅)"
    : "채팅 창 오른쪽에 붙이기 (도킹)";
}

/**
 * 본문이 패널에 자리를 내줘야 하는지.
 *
 * 도킹일 때만 true — 플로팅은 본문 위에 겹치므로 본문 폭이 그대로다.
 * (Frame 168은 본문 962px, Frame 169는 1356px)
 */
export function reservesLayoutSpace(mode) {
  return normalizePanelMode(mode) === "docked";
}
