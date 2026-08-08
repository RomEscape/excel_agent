/**
 * Vite dev 서버의 모듈 그래프를 따라가며 200이 아닌 응답을 찾는다.
 * 흰 화면 원인(깨진 import / 존재하지 않는 export) 추적용.
 *
 * 사용: node ./scripts/diagnose-dev-modules.mjs [baseUrl]
 */
const BASE = process.argv[2] || "http://localhost:1420";
const ENTRY = "/src/main.jsx";

const visited = new Set();
const failures = [];

function resolveUrl(spec, fromUrl) {
  if (spec.startsWith("http://") || spec.startsWith("https://")) return null;
  if (spec.startsWith("/")) return spec;
  if (spec.startsWith("./") || spec.startsWith("../")) {
    const base = new URL(fromUrl, BASE);
    return new URL(spec, base).pathname + new URL(spec, base).search;
  }
  return null;
}

function extractImports(code) {
  const specs = [];
  const patterns = [
    /import\s+[^"';]*?from\s*["']([^"']+)["']/g,
    /import\s*["']([^"']+)["']/g,
    /export\s+[^"';]*?from\s*["']([^"']+)["']/g,
    /import\(\s*["']([^"']+)["']\s*\)/g,
  ];
  for (const re of patterns) {
    let m;
    while ((m = re.exec(code)) !== null) specs.push(m[1]);
  }
  return specs;
}

async function walk(url, importer) {
  if (visited.has(url)) return;
  visited.add(url);

  let res;
  try {
    res = await fetch(BASE + url);
  } catch (err) {
    failures.push({ url, importer, status: "FETCH_ERROR", detail: String(err) });
    return;
  }

  if (res.status !== 200) {
    const body = await res.text();
    failures.push({ url, importer, status: res.status, detail: body.slice(0, 600) });
    return;
  }

  const code = await res.text();
  // Vite가 에러를 200 본문에 담아 보내는 경우도 잡는다.
  if (code.includes("Failed to resolve import") || code.includes("does not provide an export named")) {
    failures.push({ url, importer, status: "200_WITH_ERROR", detail: code.slice(0, 600) });
  }

  for (const spec of extractImports(code)) {
    const next = resolveUrl(spec, url);
    if (next) await walk(next, url);
  }
}

await walk(ENTRY, "(entry)");

console.log(`검사한 모듈: ${visited.size}개`);
if (failures.length === 0) {
  console.log("[OK] 모듈 그래프에 깨진 import 없음");
} else {
  console.log(`[FAIL] 문제 모듈 ${failures.length}개:\n`);
  for (const f of failures) {
    console.log("─".repeat(70));
    console.log(`URL      : ${f.url}`);
    console.log(`IMPORTER : ${f.importer}`);
    console.log(`STATUS   : ${f.status}`);
    console.log(`DETAIL   : ${f.detail}`);
  }
  process.exitCode = 1;
}
