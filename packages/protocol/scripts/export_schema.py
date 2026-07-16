"""oc_protocol Pydantic 모델 → JSON Schema export (Flutter Dart codegen 입력).

Usage (packages/protocol/python 에서):
    uv run python ../scripts/export_schema.py

출력: packages/protocol/schema/*.json
이후 Flutter 측은 quicktype/json_serializable로 schema → dart 모델을 생성한다.
CI에서는 이 스크립트 재실행 후 git diff가 비어야 통과(계약 drift 방지).
"""

import json
from pathlib import Path

from pydantic import TypeAdapter

from oc_protocol.envelope import Envelope
from oc_protocol.frames import Frame

OUT = Path(__file__).resolve().parent.parent / "schema"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "envelope.schema.json").write_text(
        json.dumps(Envelope.model_json_schema(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (OUT / "frame.schema.json").write_text(
        json.dumps(TypeAdapter(Frame).json_schema(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"[oc-protocol] JSON Schema 생성 완료 → {OUT}")


if __name__ == "__main__":
    main()
