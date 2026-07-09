/**
 * useToast — 단일 토스트 상태와 자동 dismiss 타이머를 소유하는 훅.
 *
 * 기존에 Layout / CommandPalette / CredentialsManager가 각자 굴리던
 * `useState(null)` + `setTimeout` 자동 dismiss 패턴을 한 곳으로 모았다.
 *
 * 반환 형태는 `components/ui/toast.jsx`의 `Toast` primitive가 받는 shape과 동일:
 *   const { toast, showToast, dismissToast } = useToast();
 *   <Toast toast={toast} onDismiss={dismissToast} />
 *
 * showToast는 문자열 또는 토스트 객체(`{ message, variant?, action?, duration? }`)를 받는다.
 * 자동 dismiss 시간은 `data.duration` → 훅 생성 시 넘긴 `defaultDuration` 순으로 결정.
 *
 * @param {number} [defaultDuration=4000] - 개별 토스트에 duration이 없을 때 적용할 ms
 * @returns {{ toast: object|null, showToast: (data: string|object) => void, dismissToast: () => void }}
 */
import { useState, useRef, useCallback, useEffect } from "react";

export function useToast(defaultDuration = 4000) {
  const [toast, setToast] = useState(null);
  const timerRef = useRef(null);

  const clearTimer = useCallback(() => {
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const dismissToast = useCallback(() => {
    clearTimer();
    setToast(null);
  }, [clearTimer]);

  const showToast = useCallback(
    (data) => {
      const next = typeof data === "string" ? { message: data } : data;
      clearTimer();
      setToast(next);
      const duration = next?.duration ?? defaultDuration;
      timerRef.current = setTimeout(() => {
        timerRef.current = null;
        setToast(null);
      }, duration);
    },
    [clearTimer, defaultDuration]
  );

  // 언마운트 시 타이머 정리 (메모리 누수/유령 setState 방지)
  useEffect(() => clearTimer, [clearTimer]);

  return { toast, showToast, dismissToast };
}

export default useToast;
