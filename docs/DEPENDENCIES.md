# 의존성 설치 가이드

officeclaw는 **한 파일로 전부 설치되는 구조가 아닙니다.** 역할별로 나뉩니다.

## 요약 표

| 영역 | 파일 | 설치 명령 |
|------|------|-----------|
| UI + Tauri CLI | `package.json` | `npm ci` |
| Python sidecar | `python-sidecar/pyproject.toml` | `cd python-sidecar && uv sync --frozen` |
| pip만 쓸 때 | `requirements.txt` (루트) | `pip install -r requirements.txt` |
| Rust 앱 셸 | `src-tauri/Cargo.toml` | Rust 설치 후 `npm run tauri:dev` |
| 로컬 LLM | Ollama | `ollama pull gemma4:e4b` 등 |
| 에이전트 | OpenClaw CLI | `npm install -g openclaw@latest` |

## package.json으로 안 되는 것

- FastAPI sidecar (Python)
- OpenClaw 게이트웨이 (전역 npm 패키지, 별도)
- **Gemma / ax4-light 모델** (Ollama 이미지, 수 GB)

## Python — 권장 (CI와 동일)

```bash
cd python-sidecar
uv sync --frozen --extra dev   # 개발·테스트
uv run python -m office_claw_sidecar --port 19532
```

## Python — pip만 있을 때

```bash
pip install -r requirements.txt
```

## LLM 모델 (코드 기본값 vs 실제 실행)

- **코드 기본 프리셋**: `gemma4:e4b` (`google/gemma-4-E4B-it`)
- **실제 게이트웨이 모델**: `~/.openclaw/openclaw.json`의 `agents.defaults.model` 확인

```powershell
openclaw config get agents.defaults.model
ollama list
```
