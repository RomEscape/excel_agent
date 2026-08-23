/**
 * fontScaleManager — 글자 크기 도메인의 액션·부수효과 소유자.
 *
 * 실제로 크기가 바뀌는 지점은 하나다: `<html>`의 inline `font-size`.
 * Tailwind 유틸이 전부 rem이라 이 값 하나로 앱 전체가 따라 커진다.
 *
 * themeManager와 같은 계약이다 — UI는 setFontScale만 부르고 상태는
 * fontScaleStore를 구독한다.
 */
import useFontScaleStore from "@/store/fontScaleStore";
import { rootFontSize } from "@/lib/fontScale";

/** 중복 구독 방지 — StrictMode의 이중 마운트에서도 한 번만 붙는다. */
let unsubscribe = null;

function paint(scale) {
  if (typeof document === "undefined") return;
  document.documentElement.style.fontSize = `${rootFontSize(scale)}px`;
}

/**
 * 앱 시작 시 1회. 저장된 값을 즉시 칠하고 이후 변경도 따라간다.
 *
 * @returns {() => void} 정리 함수
 */
export function initFontScale() {
  if (typeof window === "undefined") return () => {};
  if (unsubscribe) return unsubscribe;

  paint(useFontScaleStore.getState().scale);

  const stopStore = useFontScaleStore.subscribe((state, prev) => {
    if (state.scale !== prev?.scale) paint(state.scale);
  });

  unsubscribe = () => {
    stopStore();
    unsubscribe = null;
  };
  return unsubscribe;
}

/** 설정 화면에서 2값 중 하나를 고를 때. */
export function setFontScale(scale) {
  useFontScaleStore.getState().setScale(scale);
}
