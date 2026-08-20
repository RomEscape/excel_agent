/**
 * modelCatalog — Ollama 모델 ID → 온보딩 셀렉트 표시 모델 (순수).
 *
 * 최종 와이어프레임 A-3/A-4(Frame 161/162)의 모델 선택은 제조사 아이콘 + 모델명
 * + `추천` 배지 구성이다. 와이어프레임에 적힌 모델명(Opus 4.0 / Deepseek /
 * Gemma 3.0·4.0 / KIMI)은 예시일 뿐이고, 실제로 고를 수 있는 건 사용자 컴퓨터에
 * 설치된 Ollama 모델이다. 그래서 목록을 박지 않고, 받은 ID에서 제조사를 읽는다.
 *
 * ⚠️ 와이어프레임은 4개 항목 **전부**에 `추천` 배지를 달아놨다. 배지가 전부에
 * 붙으면 변별력이 없어서 컴포넌트 복제 흔적으로 보고, 여기서는 실제 추천 모델
 * 하나에만 붙인다 (SCREENS.md A-4의 확인 필요 항목).
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
 */
const BRANDS = [
  { match: /^qwen/i, label: "Qwen", color: "#615CED" },
  { match: /^gemma/i, label: "Google", color: "#4285F4" },
  { match: /^llama/i, label: "Meta", color: "#0866FF" },
  { match: /^deepseek/i, label: "DeepSeek", color: "#4D6BFE" },
  { match: /^mistral|^mixtral/i, label: "Mistral", color: "#FF7000" },
  { match: /^phi/i, label: "Microsoft", color: "#00A4EF" },
  { match: /^kimi/i, label: "Kimi", color: "#092400" },
  { match: /^claude|^opus|^sonnet|^haiku/i, label: "Anthropic", color: "#FF7043" },
];

const FALLBACK_BRAND = { label: "로컬 모델", color: "#828B80" };

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
 * 설치된 모델 목록 → 셀렉트 옵션.
 *
 * 추천 모델을 맨 위로 올린다 — 목록이 알파벳순이면 추천이 한참 아래 묻힌다.
 *
 * @param {string[]} ids
 */
export function buildModelOptions(ids) {
  const list = Array.isArray(ids) ? ids.filter((x) => typeof x === "string" && x.trim()) : [];
  // 중복 제거 — 같은 모델이 두 번 뜨면 어느 쪽을 고른 건지 알 수 없다.
  const unique = [...new Set(list.map((s) => s.trim()))];
  return unique
    .map(describeModel)
    .sort((a, b) => Number(b.recommended) - Number(a.recommended));
}
