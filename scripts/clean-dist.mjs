/**
 * 빌드 전에 출력 폴더를 지운다 — **경로에 한글이 있으면 vite 의 `emptyOutDir` 이
 * node 를 죽이기 때문**이다.
 *
 * ## 증상 (2026-09-08 실측)
 *
 * 이미 `dist/` 가 있는 상태에서 `npm run build` 를 다시 돌리면
 * `✓ 1711 modules transformed.` 직후 **오류 한 줄 없이** 끝난다.
 * 종료코드는 `0xC0000409`(STATUS_STACK_BUFFER_OVERRUN, PowerShell 표기
 * `-1073740791`; Git Bash 는 같은 것을 `127` 로 보여 준다).
 * 메시지가 없어서 `| tail` 로만 보면 **성공처럼 보인다** — 실제로 이 개발기의
 * 이전 기록들이 그렇게 "build OK" 로 적혔다.
 *
 * ## 원인은 OneDrive 도 dist 도 아니다 — 경로의 한글이다
 *
 * 같은 프로브(`mkdir` → 파일 하나 → 삭제)를 위치만 바꿔 가며 잰 결과:
 *
 *   OneDrive 밖 · ASCII 경로            → ok
 *   OneDrive 안 · ASCII 경로            → ok
 *   OneDrive 밖 · 한글 경로             → 죽음
 *   저장소 안(상위 경로만 한글)         → 죽음
 *
 * 삭제 방식만 바꿔 다시 재면:
 *
 *   fs.rmSync(dir, {recursive: true})            → 죽음
 *   readdir + unlink + rmdir 로 손수 재귀        → ok
 *
 * 즉 node(v24.11.1, Windows)의 **재귀 삭제 구현**이 non-ASCII 경로에서 죽는다.
 * 한 칸씩 지우는 경로는 멀쩡하므로, 여기서 손으로 돌아 지우고 vite 에는
 * 빈 상태를 넘긴다(`dist/` 가 없으면 vite 는 비우기를 건너뛴다).
 *
 * 경로가 전부 ASCII 인 환경(CI 등)에서는 그냥 평범한 삭제라 무해하다.
 */
import { readdirSync, unlinkSync, rmdirSync, existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join, resolve } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const target = resolve(process.argv[2] ?? join(here, "..", "apps", "desktop", "dist"));

/** node 의 재귀 삭제를 쓰지 않고 한 항목씩 지운다. */
function removeTree(dir) {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) removeTree(path);
    else unlinkSync(path);
  }
  rmdirSync(dir);
}

if (existsSync(target)) {
  try {
    removeTree(target);
  } catch (err) {
    // 지우지 못해도 빌드를 막지 않는다 — vite 가 자기 방식으로 시도할 여지를 남긴다.
    console.warn(`[clean-dist] ${target} 를 지우지 못했습니다: ${err.message}`);
  }
}
