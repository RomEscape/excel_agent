/**
 * 워크스페이스 경로 도메인 모듈.
 *
 * 워크스페이스가 어디인지 아는 곳은 사이드카(`config.get_workspace_root()`) 하나뿐이다.
 * 화면마다 경로를 하드코딩하거나 따로 조회하면 파일을 만드는 폴더와 여는 폴더가
 * 어긋나므로, 조회와 store 반영은 여기서만 한다.
 */

import { workspaceListFiles } from "@/lib/api";
import useAppStore from "@/store/appStore";

/** 동시에 여러 화면이 요청해도 왕복은 한 번만 하도록 붙잡아 두는 promise */
let inflight = null;

/**
 * 워크스페이스 절대 경로를 사이드카에서 받아 store에 채운다.
 *
 * @returns {Promise<string>} 워크스페이스 절대 경로. 사이드카가 응답하지 않으면 빈 문자열.
 */
export async function refreshWorkspacePath() {
  if (inflight) return inflight;

  inflight = (async () => {
    try {
      const data = await workspaceListFiles("");
      const root = typeof data?.workspace === "string" ? data.workspace : "";
      if (root) useAppStore.getState().setWorkspacePath(root);
      return root;
    } catch {
      // 경로 표시는 부가 정보다 — 사이드카가 아직 안 떴으면 조용히 비워 둔다.
      return "";
    } finally {
      inflight = null;
    }
  })();

  return inflight;
}
