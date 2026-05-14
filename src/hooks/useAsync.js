/**
 * useAsync — Unified async state hook.
 *
 * Replaces scattered boolean flags (loading, error, data) with a single
 * { status, data, error } state object, providing a consistent loading
 * pattern across all modules.
 *
 * @template T
 */
import { useState, useCallback } from "react";

/**
 * @typedef {'idle'|'loading'|'success'|'error'} AsyncStatus
 */

/**
 * @template T
 * @typedef {Object} AsyncState
 * @property {AsyncStatus} status
 * @property {T|null} data
 * @property {string|null} error
 */

/**
 * @template T
 * @param {() => Promise<T>} asyncFn - The async function to execute
 * @returns {{ state: AsyncState<T>, execute: () => Promise<T|undefined>, reset: () => void }}
 */
export function useAsync(asyncFn) {
  /** @type {[AsyncState<T>, React.Dispatch<React.SetStateAction<AsyncState<T>>>]} */
  const [state, setState] = useState({
    status: "idle",
    data: null,
    error: null,
  });

  const execute = useCallback(async () => {
    setState({ status: "loading", data: null, error: null });
    try {
      const data = await asyncFn();
      setState({ status: "success", data, error: null });
      return data;
    } catch (err) {
      const errorMsg =
        err instanceof Error ? err.message : typeof err === "string" ? err : String(err);
      setState({ status: "error", data: null, error: errorMsg });
    }
  }, [asyncFn]);

  const reset = useCallback(() => {
    setState({ status: "idle", data: null, error: null });
  }, []);

  return { state, execute, reset };
}

export default useAsync;
