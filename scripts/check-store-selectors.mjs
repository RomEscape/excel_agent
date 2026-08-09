/**
 * store에 없는 필드를 읽는 셀렉터를 찾아낸다.
 *
 * `useAppStore((s) => s.openclawStatus)`처럼 이미 제거된 필드를 읽으면 undefined가
 * 흘러들어가고, 그걸 `.state`로 파고드는 순간 렌더가 통째로 죽는다(흰 화면).
 * 두 번 당했으니 자동으로 잡는다.
 */
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";

// 저장소 경로에 한글이 들어 있다. URL.pathname은 퍼센트 인코딩된 문자열이라
// 그대로 fs에 넘기면 파일을 못 찾는다.
const ROOT = fileURLToPath(new URL("..", import.meta.url));
const SRC = join(ROOT, "src");

/** 검사 대상 store — 훅 이름 → 정의 파일 */
const STORES = {
  useAppStore: join(SRC, "store", "appStore.js"),
  useStatusStore: join(SRC, "store", "statusStore.js"),
};

function walk(dir) {
  const out = [];
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) out.push(...walk(path));
    else if (/\.jsx?$/.test(name)) out.push(path);
  }
  return out;
}

/**
 * store 파일에서 최상위 키를 뽑는다.
 *
 * 정확한 파서 대신 "들여쓰기 2~6칸 + 식별자 +:" 패턴을 쓴다. zustand store는
 * 객체 리터럴 한 덩어리라 이 정도로 충분하고, 놓치면 오탐이 날 뿐 누락은 아니다.
 */
function storeKeys(file) {
  const text = readFileSync(file, "utf8");
  const keys = new Set();
  for (const line of text.split("\n")) {
    const m = /^\s{2,8}([A-Za-z_$][\w$]*)\s*:/.exec(line);
    if (m) keys.add(m[1]);
    const fn = /^\s{2,8}([A-Za-z_$][\w$]*)\s*\(/.exec(line);
    if (fn) keys.add(fn[1]);
  }
  return keys;
}

const known = Object.fromEntries(
  Object.entries(STORES).map(([hook, file]) => [hook, storeKeys(file)])
);

let problems = 0;
for (const file of walk(SRC)) {
  const text = readFileSync(file, "utf8");
  for (const hook of Object.keys(STORES)) {
    const re = new RegExp(`${hook}\\(\\s*\\(\\s*(\\w+)\\s*\\)\\s*=>\\s*\\1\\.(\\w+)`, "g");
    let m;
    while ((m = re.exec(text)) !== null) {
      const field = m[2];
      if (known[hook].has(field)) continue;
      const line = text.slice(0, m.index).split("\n").length;
      console.log(
        `${relative(ROOT, file)}:${line}  ${hook} — '${field}'가 store에 없음`
      );
      problems += 1;
    }
  }
}

console.log(problems === 0 ? "\nOK: 없는 필드를 읽는 셀렉터 없음" : `\n문제 ${problems}건`);
process.exit(problems === 0 ? 0 : 1);
