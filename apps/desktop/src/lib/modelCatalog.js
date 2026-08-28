/**
 * modelCatalog — Ollama 모델 ID → 셀렉트 표시 모델 (순수).
 *
 * 최종 와이어프레임 A-3/A-4(Frame 161/162)의 모델 선택은 제조사 아이콘 + 모델명
 * + `추천` 배지 구성이다. 와이어프레임에 적힌 모델명(Opus 4.0 / Deepseek /
 * Gemma 3.0·4.0 / KIMI)은 예시일 뿐이고, 실제로 고를 수 있는 건 사용자 컴퓨터에
 * 설치된 Ollama 모델이다. 그래서 목록을 박지 않고, 받은 ID에서 제조사를 읽는다.
 *
 * ⚠️ 와이어프레임은 4개 항목 **전부**에 `추천` 배지를 달아놨다. 배지가 전부에
 * 붙으면 변별력이 없어서 컴포넌트 복제 흔적으로 보고, 여기서는 실제 추천 모델
 * 하나에만 붙인다 (SCREENS.md A-4의 확인 필요 항목).
 *
 * ## 이 모듈이 단일 소스인 이유
 *
 * 모델 목록의 원본이 **두 갈래**이고 모양이 다르다.
 *
 *   - Rust `ollama_status` → `/api/tags` 원본 그대로 → `[{ name, size, digest, ... }]` (객체)
 *   - 사이드카 `/health`   → `ollama_models` → `["qwen3:4b", ...]`               (문자열)
 *
 * 한쪽만 받는 함수를 쓰면 다른 쪽에서는 **조용히 빈 목록**이 된다(예외도 안 난다).
 * 그래서 정규화(`toModelId`)를 여기 한 곳에 두고, 화면은 전부 이걸 통과시킨다.
 */

/**
 * 추천 모델 — 4B 파라미터라 일반 노트북에서 돌고 tool-calling을 지원한다.
 * appStore의 llmConfig 기본값과 같아야 한다.
 */
export const RECOMMENDED_MODEL = "qwen3:4b";

/**
 * 제조사 판별 — 모델 ID 접두사로 맞춘다.
 *
 * 색은 와이어프레임이 각 아이콘에 쓴 브랜드 색이다. 우리 브랜드 색이 아니므로
 * 테마 토큰으로 옮기지 않고 그대로 둔다 (남의 로고 색).
 *
 * 입력은 **Ollama가 실제로 서빙하는 모델 ID**뿐이다 — 클라우드 전용 모델
 * (claude/opus/sonnet 등)은 이 목록에 올 수 없으므로 넣지 않는다.
 */
const BRANDS = [
  { match: /^qwen/i, label: "Qwen", color: "#615CED" },
  { match: /^gemma/i, label: "Google", color: "#4285F4" },
  { match: /^llama/i, label: "Meta", color: "#0866FF" },
  { match: /^deepseek/i, label: "DeepSeek", color: "#4D6BFE" },
  { match: /^mistral|^mixtral/i, label: "Mistral", color: "#FF7000" },
  { match: /^phi/i, label: "Microsoft", color: "#00A4EF" },
  { match: /^kimi/i, label: "Kimi", color: "#092400" },
];

const FALLBACK_BRAND = { label: "로컬 모델", color: "#828B80" };

/**
 * 모델 항목 → ID 문자열.
 *
 * `/api/tags`의 객체(`{name, model, ...}`)와 사이드카의 문자열을 모두 받는다.
 * 어느 쪽도 아니면 빈 문자열 — 호출부에서 걸러진다.
 *
 * @param {unknown} entry
 * @returns {string}
 */
export function toModelId(entry) {
  if (typeof entry === "string") return entry.trim();
  if (entry && typeof entry === "object") {
    const raw =
      /** @type {Record<string, unknown>} */ (entry).name ??
      /** @type {Record<string, unknown>} */ (entry).model ??
      /** @type {Record<string, unknown>} */ (entry).id;
    if (typeof raw === "string") return raw.trim();
  }
  return "";
}

/**
 * 모델 항목 배열 → 중복 없는 ID 배열 (입력 순서 유지).
 *
 * @param {unknown} entries
 * @returns {string[]}
 */
function normalizeIds(entries) {
  if (!Array.isArray(entries)) return [];
  const seen = new Set();
  const out = [];
  for (const entry of entries) {
    const id = toModelId(entry);
    // 중복 제거 — 같은 모델이 두 번 뜨면 어느 쪽을 고른 건지 알 수 없다.
    if (id && !seen.has(id)) {
      seen.add(id);
      out.push(id);
    }
  }
  return out;
}

/**
 * 모델 ID → 표시 모델.
 *
 * @param {string} id Ollama 모델 ID (`qwen3:4b`, `gemma2:2b` 등)
 * @returns {{id: string, name: string, tag: string, brand: string, color: string, recommended: boolean}}
 */
export function describeModel(id) {
  const raw = String(id || "").trim();
  const brand = BRANDS.find((b) => b.match.test(raw)) ?? FALLBACK_BRAND;

  // `qwen3:4b` → 이름 `qwen3` + 태그 `4b`. 태그는 크기 정보라 따로 보여준다.
  const colon = raw.indexOf(":");
  const name = colon > 0 ? raw.slice(0, colon) : raw;
  const tag = colon > 0 ? raw.slice(colon + 1) : "";

  return {
    id: raw,
    name,
    tag,
    brand: brand.label,
    color: brand.color,
    recommended: raw === RECOMMENDED_MODEL,
  };
}

/**
 * 셀렉트 정렬 — 추천 → 설치됨 → 이름순.
 *
 * 추천을 맨 위로 올린다: 목록이 알파벳순이면 추천이 한참 아래 묻힌다.
 * 이름순 tie-break를 두는 이유는 Ollama `/api/tags`의 반환 순서가 보장되지
 * 않아서다 — 순서가 갱신마다 흔들리면 같은 자리를 누르려던 사용자가 다른
 * 모델을 고르게 된다.
 */
function compareOptions(a, b) {
  if (a.recommended !== b.recommended) return Number(b.recommended) - Number(a.recommended);
  if (a.installed !== b.installed) return Number(b.installed) - Number(a.installed);
  return a.id.localeCompare(b.id);
}

/**
 * 설치된 모델 목록 → 셀렉트 옵션 (전부 `installed: true`).
 *
 * @param {unknown} models `/api/tags`의 객체 배열 또는 ID 문자열 배열
 */
export function buildModelOptions(models) {
  return normalizeIds(models)
    .map((id) => ({ ...describeModel(id), installed: true }))
    .sort(compareOptions);
}

/**
 * 설치된 모델 ∪ 추가 후보 → 셀렉트 옵션.
 *
 * 설치 마법사는 **아직 없는 모델을 받는 화면**이라 설치 목록만 보여주면
 * 고를 것이 하나도 없다. 반대로 설정 화면은 저장된 모델이 지워졌을 때
 * "선택된 모델 없음"으로 보이면 안 된다. 두 경우 모두 *목록에 없는 ID*를
 * 항목으로 끼워 넣어야 해서 같은 함수가 처리한다.
 *
 * 끼워 넣은 항목은 `installed: false`로 표시되고, 화면은 그걸로 `미설치`
 * 라벨을 붙인다 — 목록에 있다는 이유로 이미 받은 것처럼 보이면 안 된다.
 *
 * @param {unknown} installedModels 설치된 모델 (객체 또는 문자열 배열)
 * @param {unknown} extraIds 목록에 없어도 보여줘야 하는 ID들
 */
export function buildModelChoices(installedModels, extraIds) {
  const installed = normalizeIds(installedModels);
  const installedSet = new Set(installed);
  const extras = normalizeIds(extraIds).filter((id) => !installedSet.has(id));

  return [
    ...installed.map((id) => ({ ...describeModel(id), installed: true })),
    ...extras.map((id) => ({ ...describeModel(id), installed: false })),
  ].sort(compareOptions);
}

/**
 * 초기 선택값 결정 — 저장된 값 > 추천 > 첫 항목.
 *
 * 저장된 값이 목록에 있으면 **무조건 그것**이다. 예전에는 화면마다
 * `models[0]`을 집어넣어서, 설정에서 고른 모델이 다음 화면에서 슬그머니
 * 다른 것으로 바뀌어 있었다.
 *
 * @param {Array<{id: string, recommended: boolean}>} options
 * @param {string} [preferred] 저장된 모델 ID
 * @returns {string} 선택할 ID (고를 것이 없으면 빈 문자열)
 */
export function pickDefaultModel(options, preferred) {
  const list = Array.isArray(options) ? options : [];
  const want = String(preferred || "").trim();
  if (want && list.some((o) => o.id === want)) return want;
  return list.find((o) => o.recommended)?.id ?? list[0]?.id ?? "";
}
