/**
 * theme — 테마 선택의 순수 계약.
 *
 * 여기에는 DOM도 store도 없다. "사용자 선택(preference) + OS 선호(systemDark)를
 * 실제로 칠할 테마(resolved)로 접는 규칙"만 있다. 부수효과는 lib/themeManager.js가,
 * 상태는 store/themeStore.js가 가진다.
 *
 * 왜 3값인가: `system`이 없으면 OS를 다크로 바꾼 사용자가 앱만 라이트로 남는다.
 * 반대로 `system`만 있으면 "이 앱만 밝게 쓰고 싶다"를 표현할 수 없다.
 */

/** 사용자가 고를 수 있는 값. 저장되는 것도 이 값이다(resolved가 아니라). */
export const THEME_PREFERENCES = Object.freeze(["system", "light", "dark"]);

export const DEFAULT_THEME_PREFERENCE = "system";

/** 설정 화면·명령 팔레트에 쓰는 라벨. */
export const THEME_LABELS = Object.freeze({
  system: "시스템 설정 따르기",
  light: "라이트",
  dark: "다크",
});

/**
 * 저장값·외부 입력을 안전한 preference로 좁힌다.
 * localStorage에 손으로 넣은 값이나 구버전 값이 들어와도 앱이 깨지지 않아야 한다.
 */
export function normalizePreference(value) {
  return THEME_PREFERENCES.includes(value) ? value : DEFAULT_THEME_PREFERENCE;
}

/**
 * preference + OS 선호 → 실제로 칠할 테마.
 *
 * @param {string} preference `system` | `light` | `dark`
 * @param {boolean} systemPrefersDark `(prefers-color-scheme: dark)` 매치 여부
 * @returns {'light'|'dark'}
 */
export function resolveTheme(preference, systemPrefersDark) {
  const pref = normalizePreference(preference);
  if (pref === "system") return systemPrefersDark ? "dark" : "light";
  return pref;
}

/**
 * 토글이 누를 다음 preference.
 *
 * `system`에서 누르면 *지금 보이는 것의 반대*로 간다. 시스템이 다크인데
 * `light`로 가면 아무것도 안 바뀐 것처럼 보이기 때문이다.
 *
 * @param {string} preference 현재 preference
 * @param {boolean} systemPrefersDark
 * @returns {'light'|'dark'}
 */
export function nextPreference(preference, systemPrefersDark) {
  return resolveTheme(preference, systemPrefersDark) === "dark" ? "light" : "dark";
}
