/**
 * fontScaleStore — 글자 크기 도메인의 상태 소유자.
 *
 * themeStore와 같은 모양이다. 저장되는 건 사용자가 고른 `scale`뿐이고,
 * 실제 적용(`<html>`의 font-size)은 lib/fontScaleManager.js가 한다.
 */
import { create } from "zustand";
import { persist } from "zustand/middleware";
import { DEFAULT_FONT_SCALE, normalizeFontScale } from "@/lib/fontScale";

const useFontScaleStore = create(
  persist(
    (set) => ({
      /** @type {'default'|'large'} */
      scale: DEFAULT_FONT_SCALE,

      setScale: (scale) => set({ scale: normalizeFontScale(scale) }),
    }),
    {
      name: "office-claw-font-scale",
      partialize: (state) => ({ scale: state.scale }),
    }
  )
);

export default useFontScaleStore;
