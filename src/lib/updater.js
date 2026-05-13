/**
 * updater.js — Sprint 5 자동 업데이트 wrapper.
 *
 * Tauri IPC의 check_for_update 명령을 통해 GitHub Releases에서
 * 새 버전을 확인하고, tauri-plugin-updater로 다운로드 및 재시작한다.
 *
 * 사전 조건:
 *   - tauri.conf.json의 plugins.updater.endpoints가 실제 GitHub repo로 설정되어 있어야 함
 *   - plugins.updater.pubkey가 `cargo tauri signer generate`로 생성된 공개키여야 함
 */

import { invoke } from "@tauri-apps/api/core";
import { check } from "@tauri-apps/plugin-updater";
import { relaunch } from "@tauri-apps/plugin-process";

/**
 * 새 버전이 있는지 확인한다.
 * @returns {{ available: boolean, version?: string, currentVersion?: string, notes?: string, error?: string } | null}
 */
export async function checkForUpdate() {
  try {
    const raw = await invoke("check_for_update");
    const result = typeof raw === "string" ? JSON.parse(raw) : raw;
    return result;
  } catch (err) {
    return { available: false, error: String(err) };
  }
}

/**
 * 업데이트를 다운로드하고 설치 후 앱을 재시작한다.
 *
 * @param {(event: { event: string, data?: object }) => void} [progressCb]
 *   다운로드 진행 콜백. event 종류:
 *     - "Started"    { contentLength?: number }
 *     - "Progress"   { chunkLength: number }
 *     - "Finished"   {}
 * @returns {Promise<void>}
 */
export async function downloadAndInstall(progressCb) {
  // tauri-plugin-updater의 check()로 실제 Update 객체를 가져온다
  const update = await check();
  if (!update || !update.available) {
    return;
  }

  let downloaded = 0;
  let contentLength = 0;

  await update.downloadAndInstall((event) => {
    switch (event.event) {
      case "Started":
        contentLength = event.data?.contentLength ?? 0;
        progressCb?.({ event: "Started", data: { contentLength } });
        break;
      case "Progress":
        downloaded += event.data?.chunkLength ?? 0;
        progressCb?.({
          event: "Progress",
          data: { chunkLength: event.data?.chunkLength, downloaded, contentLength },
        });
        break;
      case "Finished":
        progressCb?.({ event: "Finished", data: {} });
        break;
    }
  });

  // 설치 완료 후 앱 재시작
  await relaunch();
}
