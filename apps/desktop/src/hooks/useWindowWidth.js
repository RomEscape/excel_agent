// 창(뷰포트) 폭을 구독한다 — 좁은 창에서 앱 사이드바를 아이콘만 남기고 접기 위해.
//
// 2026-08-19 사용자 스크린샷: 창을 ~900px로 줄이면 224px짜리 사이드바가 그대로 남아
// 워크스페이스가 630px밖에 못 써 파일 목록과 채팅 패널이 위아래로 쌓였다. 창이 좁을
// 때 사이드바를 아이콘 폭(64px)으로 접으면 같은 창에서도 나란히 배치가 살아난다.

import { useEffect, useState } from "react";

/** 이 폭 미만이면 사이드바를 자동으로 접는다(사용자가 접어 둔 것과 별개). */
export const SIDEBAR_AUTO_COLLAPSE_WIDTH = 1024;

/** @returns {number} window.innerWidth. SSR·초기값은 큰 값(펼침 상태로 시작). */
export function useWindowWidth() {
  const [width, setWidth] = useState(() =>
    typeof window === "undefined" ? 4096 : window.innerWidth
  );
  useEffect(() => {
    if (typeof window === "undefined") return undefined;
    const onResize = () => setWidth(window.innerWidth);
    onResize();
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);
  return width;
}
