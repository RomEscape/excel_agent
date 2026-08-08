/**
 * Ollama가 실제로 tool_calls를 돌려주는지 모델별로 확인한다.
 *
 * 하네스(excel_tool_agent)는 OpenAI 호환 /v1/chat/completions에 tools를 실어
 * 보내고 네이티브 tool_calls 응답을 기대한다. 모델의 Ollama TEMPLATE에
 * `{{ .Tools }}` 자리가 없으면 스키마가 렌더되지 않아 tool_calls가 나올 수 없다.
 * 이 스크립트는 그 가정을 실측으로 확인한다.
 *
 * 사용: node ./scripts/probe-ollama-toolcalls.mjs [model...]
 */
import { readFileSync } from "node:fs";

const OLLAMA = process.env.OLLAMA_HOST || "http://127.0.0.1:11434";
const DUMP = "./logs/harness_prompt_dump.json";
const TIMEOUT_MS = 180_000;

const models = process.argv.slice(2);
if (models.length === 0) {
  console.error("모델을 하나 이상 지정하세요.");
  process.exit(2);
}

const dump = JSON.parse(readFileSync(DUMP, "utf8"));
const { system_prompt: systemPrompt, tools } = dump;

const USER_MESSAGE = "B2:B10 범위에 있는 값들을 읽어줘";

async function probe(model) {
  const body = {
    model,
    messages: [
      { role: "system", content: systemPrompt },
      { role: "user", content: USER_MESSAGE },
    ],
    tools,
    temperature: 0.2,
    stream: false,
  };

  const started = Date.now();
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);

  try {
    const res = await fetch(`${OLLAMA}/v1/chat/completions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: controller.signal,
    });
    const elapsed = ((Date.now() - started) / 1000).toFixed(1);
    const text = await res.text();

    if (!res.ok) {
      return { model, elapsed, status: res.status, error: text.slice(0, 400) };
    }

    const json = JSON.parse(text);
    const msg = json.choices?.[0]?.message ?? {};
    return {
      model,
      elapsed,
      status: res.status,
      toolCalls: msg.tool_calls ?? null,
      content: (msg.content || "").slice(0, 400),
    };
  } catch (err) {
    return { model, elapsed: ((Date.now() - started) / 1000).toFixed(1), error: String(err) };
  } finally {
    clearTimeout(timer);
  }
}

console.log(`툴 ${tools.length}개 전달 · 사용자 메시지: "${USER_MESSAGE}"\n`);

for (const model of models) {
  const r = await probe(model);
  console.log("─".repeat(70));
  console.log(`모델      : ${r.model}`);
  console.log(`소요       : ${r.elapsed}s   HTTP ${r.status ?? "-"}`);
  if (r.error) {
    console.log(`오류       : ${r.error}`);
    continue;
  }
  if (r.toolCalls?.length) {
    console.log(`tool_calls : ${r.toolCalls.length}건`);
    for (const call of r.toolCalls) {
      console.log(`   → ${call.function?.name}(${call.function?.arguments})`);
    }
  } else {
    console.log("tool_calls : 없음  ← 툴 스키마가 모델에 전달되지 않았을 가능성");
    console.log(`content    : ${r.content}`);
  }
}
