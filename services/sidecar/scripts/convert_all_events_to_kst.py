from __future__ import annotations

import argparse
import json
import re
import shutil
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

KST = timezone(timedelta(hours=9), name="KST")
ISO_WITH_TZ_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)


def _to_kst_if_timestamp(value: str) -> str:
    text = str(value or "").strip()
    if not ISO_WITH_TZ_RE.match(text):
        return value
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except Exception:
        return value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(KST).isoformat()


def _convert_timestamps(obj: Any) -> tuple[Any, int]:
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        changed = 0
        for key, val in obj.items():
            converted, n = _convert_timestamps(val)
            out[key] = converted
            changed += n
        return out, changed
    if isinstance(obj, list):
        out_list: list[Any] = []
        changed = 0
        for item in obj:
            converted, n = _convert_timestamps(item)
            out_list.append(converted)
            changed += n
        return out_list, changed
    if isinstance(obj, str):
        converted = _to_kst_if_timestamp(obj)
        return converted, 1 if converted != obj else 0
    return obj, 0


def convert_file_to_kst(path: Path) -> dict[str, int]:
    if not path.exists():
        raise FileNotFoundError(f"파일을 찾을 수 없습니다: {path}")

    stamp = datetime.now(KST).strftime("%Y%m%d_%H%M%S")
    backup_path = path.with_suffix(path.suffix + f".bak_{stamp}")
    temp_path = path.with_suffix(path.suffix + ".tmp")

    line_count = 0
    converted_timestamps = 0
    skipped_lines = 0

    shutil.copy2(path, backup_path)
    with path.open("r", encoding="utf-8") as src, temp_path.open("w", encoding="utf-8") as dst:
        for raw_line in src:
            line = raw_line.rstrip("\n")
            if not line.strip():
                dst.write("\n")
                continue
            line_count += 1
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                skipped_lines += 1
                dst.write(raw_line)
                continue
            converted, changed = _convert_timestamps(payload)
            converted_timestamps += changed
            dst.write(json.dumps(converted, ensure_ascii=False) + "\n")

    temp_path.replace(path)
    return {
        "line_count": line_count,
        "converted_timestamps": converted_timestamps,
        "skipped_lines": skipped_lines,
        "backup_path": str(backup_path),
    }


def main() -> None:
    default_path = Path(__file__).resolve().parents[3] / "logs" / "all_events.jsonl"
    parser = argparse.ArgumentParser(description="all_events.jsonl의 UTC 시각을 KST로 변환")
    parser.add_argument("--path", type=Path, default=default_path, help="변환할 JSONL 파일 경로")
    args = parser.parse_args()

    result = convert_file_to_kst(args.path)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

