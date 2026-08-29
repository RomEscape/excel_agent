// 에이전트가 지금 무엇을 대상으로 삼고 있는지 구독한다.
//
// 상태는 `lib/excelTargetManager`가 소유한다(CLAUDE.md §4). 이 훅은 구독만 한다 —
// 여러 컴포넌트가 각자 사이드카에 묻는 중복 fetch를 막기 위함이다.

import { useEffect, useState } from "react";

import {
  getExcelTarget,
  refreshExcelTarget,
  subscribeExcelTarget,
} from "@/lib/excelTargetManager.js";

/**
 * @param {number} pollMs 주기적 갱신 간격(ms). 0이면 갱신하지 않는다.
 *                        Excel을 열고 닫는 걸 화면이 따라가야 해서 기본값을 둔다.
 */
export function useExcelTarget(pollMs = 8000) {
  const [target, setTarget] = useState(getExcelTarget);

  useEffect(() => subscribeExcelTarget(setTarget), []);

  useEffect(() => {
    if (!pollMs) return undefined;
    let alive = true;
    const tick = () => {
      if (alive) refreshExcelTarget();
    };
    tick();
    const id = setInterval(tick, pollMs);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, [pollMs]);

  return target;
}
