/**
 * `/health` 응답 → 상태 표시줄이 쓸 사이드카 상태.
 *
 * ## 왜 따로 두는가
 *
 * 이전에는 `healthCheck()` 가 성공하기만 하면 무조건 `{state:"ok"}` 였다. 그래서
 * **정리되지 않고 남은 옛 사이드카**에 붙어 있어도 초록불이었고, 그 사이드카가 옛
 * 워크스페이스를 보는 바람에 "파일을 찾을 수 없습니다"만 반복됐다(2026-09-08 실측).
 * 응답이 왔다는 것과 **맞는 사이드카가 답했다는 것**은 다른 얘기다.
 *
 * 낡음 판정 자체는 사이드카가 한다(`sidecar_identity.describe_running_sidecar`).
 * 소스 위치는 환경마다 달라 앱이 알 수 없기 때문이다.
 */

/** 사람이 바로 행동할 수 있게 — 무엇이 잘못됐고 무엇을 누르면 되는지. */
const STALE_MESSAGE =
  "백그라운드 서비스가 예전 버전으로 돌고 있어요. 앱을 껐다 켜 주세요.";

/**
 * @param {object|null|undefined} health `/health` 응답
 * @returns {{state: "ok"|"stale", message: string, workspaceDir: string}}
 */
export function describeSidecarHealth(health) {
  const sidecar = health?.sidecar ?? null;
  const workspaceDir = typeof sidecar?.workspace_dir === "string" ? sidecar.workspace_dir : "";

  // `sidecar` 블록이 아예 없으면 **그 자체가 낡음의 증거**다 — 이 필드를 내려주지
  // 않던 시절의 사이드카이기 때문이다. 다만 필드가 있는데 값이 false 면 정상이다.
  if (sidecar && sidecar.code_stale !== true) {
    return { state: "ok", message: "연결됨", workspaceDir };
  }
  if (!sidecar) {
    return { state: "stale", message: STALE_MESSAGE, workspaceDir: "" };
  }
  return { state: "stale", message: STALE_MESSAGE, workspaceDir };
}

export { STALE_MESSAGE };
