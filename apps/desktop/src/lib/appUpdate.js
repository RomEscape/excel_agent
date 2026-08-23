/**
 * appUpdate — "지금 새 버전이 있나?"를 묻는 한 곳.
 *
 * `components/updater/UpdateNotice.jsx`는 앱 시작 5초 뒤에 **자동으로** 확인하고
 * 새 버전이 있을 때만 알린다. 이 모듈은 설정 화면에서 사용자가 **직접** 눌러
 * 확인하는 경로다 — 그래서 "최신입니다"라는 결과도 돌려줘야 한다(자동 경로는
 * 최신이면 조용히 아무것도 안 한다).
 *
 * Tauri updater 플러그인이 없거나(웹 dev 모드) 네트워크가 막히면 던지지 않고
 * 상태로 돌려준다 — 설정 화면이 예외로 깨지면 안 된다.
 */
import packageJson from "../../package.json";

/** 현재 실행 중인 앱 버전. */
export const CURRENT_VERSION = packageJson.version;

// Vite가 정적 분석으로 모듈 부재 에러를 내지 않도록 변수 specifier로 dynamic import.
const UPDATER_MODULE = "@tauri-apps/plugin-updater";

/**
 * @typedef {Object} UpdateCheckResult
 * @property {'latest'|'available'|'unsupported'|'error'} status
 * @property {string} [version]  status='available'일 때 새 버전
 * @property {string} [message]  status='error'일 때 사유
 */

/**
 * 새 버전 확인.
 *
 * - `unsupported` — 플러그인 없음. 브라우저 dev(`npm run dev`)에서는 항상 이 값이다.
 * - `error` — 네트워크·서명 실패. 사용자에게 사유를 보여준다.
 *
 * @returns {Promise<UpdateCheckResult>}
 */
export async function checkForUpdate() {
  let plugin;
  try {
    plugin = await import(/* @vite-ignore */ UPDATER_MODULE);
  } catch {
    return { status: "unsupported" };
  }
  if (!plugin || typeof plugin.check !== "function") {
    return { status: "unsupported" };
  }

  try {
    const result = await plugin.check();
    if (!result) return { status: "latest" };
    const version = result.version || result?.manifest?.version;
    if (!version) return { status: "latest" };
    return { status: "available", version };
  } catch (err) {
    return { status: "error", message: err?.message || String(err) };
  }
}
