// 요소의 현재 폭(px)을 구독한다.
//
// WorkspacePage가 채팅 패널 폭을 창 크기에 맞춰 깎고, 좁으면 세로로 쌓기 위해 쓴다.
// window.innerWidth가 아니라 **컨테이너** 폭을 재는 이유: 좌측 앱 사이드바가 접히거나
// 펼쳐지면 창 크기는 그대로인데 이 페이지의 가용 폭이 바뀐다.

import { useLayoutEffect, useState } from "react";

/**
 * @param {import("react").RefObject<HTMLElement>} ref 폭을 잴 요소
 * @returns {number} 폭(px). 아직 못 쟀으면 0.
 */
export function useElementWidth(ref) {
  const [width, setWidth] = useState(0);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return undefined;

    // 첫 페인트 전에 한 번 잰다. 이걸 ResizeObserver 콜백에만 맡기면 좁은 창에서
    // 한 프레임 동안 옛 배치(눌린 파일 목록)가 그려졌다가 바뀐다.
    setWidth(el.getBoundingClientRect().width);

    if (typeof ResizeObserver === "undefined") {
      // 아주 오래된 webview 폴백 — 창 크기 변화만 따라간다.
      const onResize = () => setWidth(el.getBoundingClientRect().width);
      window.addEventListener("resize", onResize);
      return () => window.removeEventListener("resize", onResize);
    }

    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      const next = entry?.contentRect?.width;
      setWidth(typeof next === "number" ? next : el.getBoundingClientRect().width);
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, [ref]);

  return width;
}
