"""이벤트 줄의 UTC 시각을 KST 로 바꾼다.

2026-09-10 부터 이벤트는 별도 `all_events.jsonl` 이 아니라 `chat_log.jsonl` 의
`record == "event"` 줄이다. **이벤트 줄만** 고쳐 쓰고 턴 줄(`turn_id` 있음)·플래너 승격
줄·깨진 줄은 바이트 그대로 흘려보낸다 — 줄바꿈(CRLF/LF)도 원본대로 둔다. 옛
`all_events.jsonl` 은 (줄에 `record` 가 없고 `event_type` 만 있으므로) 그대로 읽힌다.

    python scripts/convert_all_events_to_kst.py --path <옛 all_events.jsonl>
    python scripts/convert_all_events_to_kst.py --path logs/chat_log.jsonl --allow-live

`--path` 는 필수다. 기본값이 살아 있는 `chat_log.jsonl` 이면 인자 없이 돌린 것만으로
사이드카가 붙여 쓰는 중인 파일을 통째로 바꿔치기하게 된다. 그 파일(`get_chat_log_path()`)
을 지목하면 거부하고, `--allow-live` 를 준 경우에만 진행한다 — **사이드카를 내린 뒤에만**
쓴다.

원본 백업은 `logs/` 가 아니라 `get_chat_log_archive_dir()`(저장소 밖) 에
`<파일명>.bak_<KST스탬프>` 로 남긴다 — `logs/` 에는 `chat_log.jsonl` 하나만 둔다.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from office_claw_sidecar.config import get_chat_log_archive_dir, get_chat_log_path

KST = timezone(timedelta(hours=9), name="KST")

ISO_WITH_TZ_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)


def _is_event_row(payload: Any) -> bool:
    """변환 대상 줄인가 — chat_log 의 `record == "event"` 줄, 또는 옛 all_events 꼴."""
    if not isinstance(payload, dict) or "turn_id" in payload:
        return False
    record = payload.get("record")
    if record is not None:
        return str(record) == "event"
    return "event_type" in payload


def _is_live_chat_log(path: Path) -> bool:
    """사이드카가 붙여 쓰는 그 `chat_log.jsonl` 인가."""
    try:
        return path.resolve() == get_chat_log_path().resolve()
    except OSError:
        return False


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


def convert_file_to_kst(path: Path, backup_dir: Path | None = None) -> dict[str, Any]:
    """`path` 의 이벤트 줄만 KST 로 고쳐 제자리에 다시 쓴다.

    - 백업은 `backup_dir`(기본 `get_chat_log_archive_dir()`, 저장소 `logs/` 밖)에
      `<파일명>.bak_<KST스탬프>` 로 둔다.
    - 이벤트가 아닌 줄과 빈 줄·깨진 줄은 줄바꿈까지 바이트 그대로 흘려보낸다
      (`newline=""` — Windows 텍스트 모드의 LF→CRLF 변환을 막는다).
    - 임시 파일은 같은 폴더의 `<파일명>.tmp` 이며 끝나면 원본을 대체한다. 도중에
      실패하면 지우고 원본은 그대로 둔다.
    """
    if not path.exists():
        raise FileNotFoundError(f"파일을 찾을 수 없습니다: {path}")

    stamp = datetime.now(KST).strftime("%Y%m%d_%H%M%S")
    if backup_dir is None:
        backup_dir = get_chat_log_archive_dir()
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"{path.name}.bak_{stamp}"
    temp_path = path.with_name(path.name + ".tmp")

    line_count = 0
    converted_timestamps = 0
    skipped_lines = 0
    passthrough_lines = 0

    shutil.copy2(path, backup_path)
    try:
        with (
            path.open("r", encoding="utf-8", newline="") as src,
            temp_path.open("w", encoding="utf-8", newline="") as dst,
        ):
            for raw_line in src:
                line = raw_line.rstrip("\r\n")
                eol = raw_line[len(line) :]
                if not line.strip():
                    dst.write(raw_line)
                    continue
                line_count += 1
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    skipped_lines += 1
                    dst.write(raw_line)
                    continue
                if not _is_event_row(payload):
                    # 턴 줄·플래너 승격 줄은 손대지 않는다 — 바이트 그대로 흘려보낸다.
                    passthrough_lines += 1
                    dst.write(raw_line)
                    continue
                converted, changed = _convert_timestamps(payload)
                converted_timestamps += changed
                dst.write(json.dumps(converted, ensure_ascii=False) + eol)
        temp_path.replace(path)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise

    return {
        "line_count": line_count,
        "converted_timestamps": converted_timestamps,
        "skipped_lines": skipped_lines,
        "passthrough_lines": passthrough_lines,
        "backup_path": str(backup_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="JSONL 의 이벤트 줄(record=event, 또는 옛 all_events 꼴)의 UTC 시각을 KST 로 변환"
    )
    parser.add_argument(
        "--path",
        type=Path,
        required=True,
        help="변환할 JSONL 파일 경로 (chat_log.jsonl 또는 옛 all_events.jsonl). 기본값은 없다.",
    )
    parser.add_argument(
        "--allow-live",
        action="store_true",
        help="사이드카가 붙여 쓰는 chat_log.jsonl(get_chat_log_path()) 도 변환한다. 사이드카를 내린 뒤에만 쓸 것.",
    )
    parser.add_argument(
        "--backup-dir",
        type=Path,
        default=None,
        help="백업을 둘 폴더 (기본: get_chat_log_archive_dir() — 저장소 logs/ 밖)",
    )
    args = parser.parse_args()

    if _is_live_chat_log(args.path) and not args.allow_live:
        print(
            f"거부: {args.path} 는 사이드카가 붙여 쓰는 chat_log.jsonl 이다. "
            "사이드카를 내린 뒤 --allow-live 를 붙여 다시 돌린다.",
            file=sys.stderr,
        )
        return 2

    result = convert_file_to_kst(args.path, backup_dir=args.backup_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
