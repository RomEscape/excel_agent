/**
 * themeStore — 테마 도메인의 상태 소유자.
 *
 * 저장되는 건 `preference`(사용자가 고른 값)뿐이다. `systemDark`는 OS가 주는
 * 값이라 매 실행마다 다시 읽어야 하고, `resolved`는 둘에서 파생되므로
 * 저장하면 서로 어긋날 수 있다.
 *
 * 적용(=<html>에 .dark 붙이기)은 lib/themeManager.js가 한다. 이 파일은 setter만 갖는다.
 */
import { create } from "zustand";
import { persist } from "zustand/middleware";
import {
  DEFAULT_THEME_PREFERENCE,
  normalizePreference,
  resolveTheme,
} from "@/lib/theme";

const useThemeStore = create(
  persist(
    (set) => ({
      /** @type {'system'|'light'|'dark'} 사용자가 고른 값 */
      preference: DEFAULT_THEME_PREFERENCE,

      /** OS가 다크를 선호하는지 — themeManager가 media query로 채운다. */
      systemDark: false,

      /** @type {'light'|'dark'} 실제로 칠하는 테마 (preference + systemDark 파생) */
      resolved: "light",

      setPreference: (preference) =>
        set((state) => {
          const next = normalizePreference(preference);
          return { preference: next, resolved: resolveTheme(next, state.systemDark) };
        }),

      setSystemDark: (systemDark) =>
        set((state) => ({
          systemDark: !!systemDark,
          resolved: resolveTheme(state.preference, !!systemDark),
        })),
    }),
    {
      name: "office-claw-theme",
      partialize: (state) => ({ preference: state.preference }),
    }
  )
);

export default useThemeStore;
