# Gemma 4 + OpenClaw 로컬 스택

로컬에서 **Gemma 4 E4B 멀티모달**을 Ollama로 실행하고, **OpenClaw 게이트웨이**를 통해 사용자와 대화·스킬을 사용하는 기본 구성입니다.

## 모델

| 용도 | 식별자 |
|------|--------|
| Hugging Face (정본) | [google/gemma-4-E4B-it](https://huggingface.co/google/gemma-4-E4B-it) |
| Ollama pull/run | `gemma4:e4b` |
| OpenClaw `agents.defaults.model` | `ollama/gemma4:e4b` |

## 코드 위치

| 레이어 | 경로 |
|--------|------|
| 프리셋 (JS) | `src/lib/localStack/` |
| Wizard 로직 | `src/lib/localAISetupCore.js` |
| 프리셋 (Python) | `python-sidecar/office_claw_sidecar/local_stack/` |
| 자동 설정 UI | `src/components/guide/LocalAISetupWizard.jsx` |
| 사용자 대화 | `src/components/workspace/WorkspacePage.jsx` → sidecar `/agent/chat` → OpenClaw |

## Windows 빠른 기동 (지금 PC에서 테스트)

이미 Ollama만 있으면:

```powershell
npm install -g openclaw@latest
ollama launch openclaw --model ax4-light:latest --yes   # 또는 gemma4:e4b
.\scripts\start-local-stack.ps1
.\scripts\verify-local-stack.ps1
npm run tauri:dev   # 별 터미널
```

`scripts/local-env.ps1`이 `~/.openclaw/openclaw.json`의 게이트웨이 토큰을 읽어 sidecar에 전달합니다.

## 수동 설정 (CLI)

```bash
ollama pull gemma4:e4b
npm install -g openclaw@latest
openclaw config set models.providers.ollama.baseUrl http://127.0.0.1:11434
openclaw config set models.providers.ollama.apiKey ollama-local
openclaw config set models.providers.ollama.api ollama
openclaw config set agents.defaults.model ollama/gemma4:e4b
openclaw gateway --port 18789
```

앱에서는 **로컬 AI 설정 마법사**가 위 단계를 멱등하게 자동 실행합니다.

## 대화 경로

`ollama run`은 모델만 1:1 대화합니다. 메일·시트 등 스킬과 보안 정책은 다음 경로만 사용합니다.

```
사용자 → Workspace / 메신저 → Python sidecar → OpenClaw Gateway → Ollama (gemma4:e4b)
```

## VRAM

Ollama 공식 `gemma4:e4b`는 약 **9.6GB**입니다. **RTX 4060 Ti 16GB**에서 OpenClaw·게이트웨이와 함께 쓸 때 여유를 확인하세요.
