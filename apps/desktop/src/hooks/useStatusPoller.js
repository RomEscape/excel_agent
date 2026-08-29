/**
 * 시스템 상태 자동 폴러 — App 루트에서 1회 마운트.
 *
 *   1) 마운트 직후 1회 refreshAllModules 호출 (초기 상태 확보)
 *   2) 이후 N초마다 다시 호출 (기본 30초)
 *   3) 설치/시작 작업 후에는 statusManager 액션이 알아서 즉시 갱신하므로
 *      여기서는 추가 로직 불필요
 *
 * 각 모듈의 check()는 진행 중인 operation이 있으면 덮어쓰지 않도록
 * statusManager 액션 쪽에서 처리하는 것이 이상적이지만, 현재는 단순히 덮어쓴다
 * (operation 필드가 잠시 깜빡일 수 있음 — 대시보드/위저드 UX에 영향 없음).
 */
import { useEffect } from "react";
import { refreshAllModules } from "@/lib/statusManager";
import useStatusStore from "@/store/statusStore";

const DEFAULT_INTERVAL_MS = 30_000;

/**
 * @param {{ intervalMs?: number, skipInitial?: boolean }} [opts]
 */
export function useStatusPoller(opts = {}) {
  const intervalMs = opts.intervalMs ?? DEFAULT_INTERVAL_MS;
  const skipInitial = !!opts.skipInitial;

  useEffect(() => {
    if (!skipInitial) {
      refreshAllModules();
    }
    const t = setInterval(() => {
      // 진행 중인 작업이 있는 모듈은 폴링 건너뜀 — 사용자 조작 결과를 덮어쓰지 않기 위함.
      const modules = useStatusStore.getState().modules;
      const anyBusy = Object.values(modules).some((m) => m.operation && m.operation !== "checking");
      if (anyBusy) return;
      refreshAllModules();
    }, intervalMs);
    return () => clearInterval(t);
  }, [intervalMs, skipInitial]);
}

export default useStatusPoller;
