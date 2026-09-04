# -*- coding: utf-8 -*-
"""플래너 모델(GGUF)을 Hugging Face에 올린다 — 클론-실행의 마지막 조각.

왜 이 방식인가:
    Ollama가 보관하는 blob 자체가 이미 GGUF다(매직바이트로 확인). 그래서 4.4GB를
    artifacts/로 복사한 뒤 올릴 필요가 없다 — blob 경로를 그대로 업로드 소스로
    준다. 디스크 4.4GB와 OneDrive 동기화를 통째로 아낀다.

인증:
    토큰을 인자로 받지 않는다. 터미널에서 `huggingface-cli login`을 한 번 하면
    캐시된 토큰을 huggingface_hub이 알아서 쓴다 — 토큰이 명령 이력이나 로그에
    남지 않는다.

사용:
    python scripts/publish_planner_model.py --repo PJiNH/ax7bplanner-v3-GGUF
    python scripts/publish_planner_model.py --repo ... --model ax7bplanner-v5r:latest --private
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

MODEL_CARD = """---
license: apache-2.0
library_name: gguf
tags:
  - gguf
  - ollama
  - excel
  - korean
---

# {model_name} (GGUF)

Office-Claw(김대리)의 **Excel 계획 수립 전용** 모델이다. 자연어 엑셀 명령을
실행 계획 JSON으로 옮기는 일만 하도록 파인튜닝됐다 — 일반 대화용이 아니다.

- 베이스: A.X-4.0-Light
- 용도: `planner_model` (일반 대화는 `skt/A.X-4.0-Light`가 맡는다)

## 쓰는 법

```bash
ollama pull hf.co/{repo_id}
ollama cp hf.co/{repo_id}:latest {model_name}:latest
```

Office-Claw 셋업은 저장소 이름만 알려 주면 위 두 줄을 자동으로 한다:

```powershell
powershell scripts\\setup.ps1 -PlannerHfRepo "{repo_id}"
```
"""


def find_blob(model: str) -> tuple[Path, int]:
    """Ollama 매니페스트에서 가중치 레이어의 blob 경로와 크기를 찾는다."""
    name, _, tag = model.partition(":")
    tag = tag or "latest"
    root = Path(os.environ.get("OLLAMA_MODELS") or (Path.home() / ".ollama" / "models"))
    manifest = root / "manifests" / "registry.ollama.ai" / "library" / name / tag
    if not manifest.exists():
        sys.exit(f"매니페스트가 없습니다: {manifest}\n`ollama list`로 '{model}' 존재를 확인해 주세요.")

    layers = json.loads(manifest.read_text(encoding="utf-8")).get("layers", [])
    layer = next((x for x in layers if str(x.get("mediaType", "")).endswith(".image.model")), None)
    if layer is None:
        sys.exit(f"매니페스트에 model 레이어가 없습니다: {manifest}")

    blob = root / "blobs" / str(layer["digest"]).replace(":", "-")
    if not blob.exists():
        sys.exit(f"blob이 없습니다: {blob}")

    # GGUF가 아니면 올려 봐야 ollama가 못 읽는다 — 여기서 막는다.
    with blob.open("rb") as f:
        if f.read(4) != b"GGUF":
            sys.exit(f"GGUF가 아닙니다(매직바이트 불일치): {blob}")
    return blob, int(layer.get("size") or blob.stat().st_size)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, help="예: PJiNH/ax7bplanner-v3-GGUF")
    ap.add_argument("--model", default="ax7bplanner-v3:latest")
    ap.add_argument("--private", action="store_true",
                    help="비공개로. 단, 남이 ollama pull 하려면 공개여야 한다.")
    args = ap.parse_args()

    from huggingface_hub import HfApi

    blob, size = find_blob(args.model)
    model_name = args.model.split(":")[0]
    print(f"모델   : {args.model}")
    print(f"원본   : {blob}")
    print(f"크기   : {size / 1e9:.2f} GB")
    print(f"올릴 곳: {args.repo} ({'비공개' if args.private else '공개'})")

    api = HfApi()
    who = api.whoami()
    print(f"계정   : {who.get('name')}")

    api.create_repo(repo_id=args.repo, repo_type="model",
                    private=args.private, exist_ok=True)
    print("저장소 준비 완료. 업로드 시작 — 크기가 커서 오래 걸립니다.")

    api.upload_file(
        path_or_fileobj=str(blob),
        path_in_repo=f"{model_name}.gguf",
        repo_id=args.repo,
        repo_type="model",
        commit_message=f"{model_name} GGUF (Office-Claw planner)",
    )
    api.upload_file(
        path_or_fileobj=MODEL_CARD.format(model_name=model_name, repo_id=args.repo).encode("utf-8"),
        path_in_repo="README.md",
        repo_id=args.repo,
        repo_type="model",
        commit_message="모델 카드",
    )
    print(f"\n완료: https://huggingface.co/{args.repo}")
    print(f"셋업에 넘길 값: -PlannerHfRepo \"{args.repo}\"")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
