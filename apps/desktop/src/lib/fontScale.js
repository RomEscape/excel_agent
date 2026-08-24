/**
 * fontScale — 글자 크기 선택의 순수 계약.
 *
 * 테마(`lib/theme.js`)와 같은 구조다: 여기에는 DOM도 store도 없고 "사용자 선택 →
 * 루트 font-size" 규칙만 있다. 적용은 `lib/fontScaleManager.js`, 상태는
 * `store/fontScaleStore.js`가 갖는다.
 *
 * 루트 font-size 하나만 바꾸는 이유: Tailwind의 크기 유틸(text-sm, p-4, h-11 …)이
 * 전부 rem이라 루트만 키우면 글자·여백·컨트롤이 함께 커진다. 개별 컴포넌트에
 * `large:` 변형을 붙이면 새 화면을 만들 때마다 빠뜨린 곳이 생긴다.
 */

/** 와이어프레임의 2단계. `기본 사이즈` / `큰 사이즈`. */
export const FONT_SCALES = Object.freeze(["default", "large"]);

export const DEFAULT_FONT_SCALE = "default";

export const FONT_SCALE_LABELS = Object.freeze({
  default: "기본 사이즈",
  large: "큰 사이즈",
});

/**
 * 루트 font-size(px).
 *
 * 18px는 브라우저 기본 16px의 1.125배다. 1.25배(20px)까지 올리면 1600×900
 * 창에서 작업 기록 표의 `명령` 칸이 2줄로 접히기 시작해서 여기서 끊었다.
 */
export const FONT_SCALE_PX = Object.freeze({
  default: 16,
  large: 18,
});

/** 저장값·외부 입력을 안전한 값으로 좁힌다. */
export function normalizeFontScale(scale) {
  return FONT_SCALES.includes(scale) ? scale : DEFAULT_FONT_SCALE;
}

/** 선택값 → 실제로 `<html>`에 세울 px. */
export function rootFontSize(scale) {
  return FONT_SCALE_PX[normalizeFontScale(scale)];
}
